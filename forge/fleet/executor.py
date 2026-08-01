"""
Grok Build executor backend — interim path (spec §10).

Spawns stock ``grok-build --headless`` (or a configurable command) as a
subprocess, one session per DAG step. Fully mockable: inject ``run_fn`` so
CI never needs the binary installed.

**Trust boundary:** ``command``, ``working_dir``, and ``env`` are trusted
operator/config values — never take them from untrusted step task text.
The step ``task`` string is passed as a CLI argument to the binary; treat
the binary itself as the sandbox boundary. Full ``os.environ`` is inherited
so provider API keys work; do not log secrets (task text is truncated).

Assumed CLI interface (injectable / overridable)::

    grok-build --headless --model <model_id> <task>

Environment overrides when flags are unknown to an older binary:

    GROK_BUILD_MODEL, GROK_BUILD_STEP_ID, GROK_BUILD_HEADLESS=1
    GROK_BUILD_BASE_URL  (when ModelEntry.base_url is set)

Optional usage line on stdout (last JSON object matching)::

    {"usage": {"input_tokens": N, "output_tokens": M, "cost_usd": X}}
    # or flat: {"input_tokens": N, "output_tokens": M}

When ``run_fn`` is injected it **owns teardown** (timeouts, kill). The
default path uses Popen + process-group/tree kill on timeout.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

log = logging.getLogger("forge.fleet.executor")

RunFn = Callable[..., Any]


@dataclass
class GrokBuildResult:
    """Captured outcome of one grok-build subprocess session."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False
    model: str = ""
    step_id: str = ""
    duration_s: float = 0.0
    command: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error

    @property
    def input_tokens(self) -> int:
        try:
            return max(0, int(self.usage.get("input_tokens") or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def output_tokens(self) -> int:
        try:
            return max(0, int(self.usage.get("output_tokens") or 0))
        except (TypeError, ValueError):
            return 0

    @property
    def cost_usd(self) -> float | None:
        if "cost_usd" not in self.usage or self.usage["cost_usd"] is None:
            return None
        try:
            v = float(self.usage["cost_usd"])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v) or v < 0:
            return None
        return v

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "usage": dict(self.usage),
            "timed_out": self.timed_out,
            "model": self.model,
            "step_id": self.step_id,
            "duration_s": self.duration_s,
            "command": list(self.command),
            "error": self.error,
            "ok": self.ok,
        }


_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "prompt_tokens",
    "completion_tokens",
)


def parse_usage_from_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    """Best-effort parse of a usage JSON blob from process output."""
    blobs = _iter_json_objects(stdout) + _iter_json_objects(stderr)
    for obj in reversed(blobs):
        if not isinstance(obj, dict):
            continue
        if "usage" in obj and isinstance(obj["usage"], dict):
            return _normalize_usage(obj["usage"])
        if any(k in obj for k in _USAGE_KEYS):
            return _normalize_usage(obj)
    return {}


def _normalize_usage(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    in_tok = raw.get("input_tokens", raw.get("prompt_tokens", 0))
    out_tok = raw.get("output_tokens", raw.get("completion_tokens", 0))
    try:
        out["input_tokens"] = max(0, int(in_tok or 0))
    except (TypeError, ValueError):
        out["input_tokens"] = 0
    try:
        out["output_tokens"] = max(0, int(out_tok or 0))
    except (TypeError, ValueError):
        out["output_tokens"] = 0
    if "cost_usd" in raw and raw["cost_usd"] is not None:
        try:
            c = float(raw["cost_usd"])
            if math.isfinite(c) and c >= 0:
                out["cost_usd"] = c
            # else drop invalid cost
        except (TypeError, ValueError):
            pass
    for k, v in raw.items():
        if k not in out and k != "cost_usd":
            out[k] = v
    return out


def _iter_json_objects(text: str) -> list[Any]:
    if not text:
        return []
    found: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not found:
        m = re.search(r"(\{.*\})\s*$", text, re.DOTALL)
        if m:
            try:
                found.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
    return found


def _as_cmd_list(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return list(command)


def _truncate_task(task: str, limit: int = 80) -> str:
    t = (task or "").replace("\n", " ").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


class GrokBuildExecutor:
    """Subprocess wrapper for stock grok-build headless sessions.

    Parameters
    ----------
    command:
        Binary or argv prefix (default ``grok-build``). **Trusted config.**
    timeout:
        Wall-clock seconds per step (enforced via Popen wait + tree kill).
    working_dir:
        cwd for the subprocess. **Trusted config.**
    env:
        Extra env vars merged onto ``os.environ``.
    run_fn:
        Injectable replacement for the process runner (tests).
        Signature: ``run_fn(cmd, *, cwd, env, capture_output, text, timeout)``
        → object with ``returncode``, ``stdout``, ``stderr``.
        When set, the injectee owns timeout/teardown.
    registry:
        Optional ModelRegistry for base_url injection.
    """

    def __init__(
        self,
        command: str | Sequence[str] = "grok-build",
        timeout: float = 300.0,
        working_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
        run_fn: RunFn | None = None,
        extra_args: Sequence[str] | None = None,
        model_flag: str = "--model",
        headless_flag: str = "--headless",
        pass_model_via_env: bool = True,
        registry: Any | None = None,
    ):
        self.command = command
        self.timeout = timeout
        self.working_dir = Path(working_dir) if working_dir else None
        self.env = dict(env or {})
        self.run_fn = run_fn
        self.extra_args = list(extra_args or [])
        self.model_flag = model_flag
        self.headless_flag = headless_flag
        self.pass_model_via_env = pass_model_via_env
        self.registry = registry

    def build_command(self, task: str, model: str) -> list[str]:
        cmd = _as_cmd_list(self.command)
        if self.headless_flag and self.headless_flag not in cmd:
            cmd.append(self.headless_flag)
        if self.model_flag:
            cmd.extend([self.model_flag, model])
        cmd.extend(self.extra_args)
        cmd.append(task)
        return cmd

    def build_env(
        self, model: str, step_id: str, base_url: str | None = None
    ) -> dict[str, str]:
        env = {**os.environ, **self.env}
        if self.pass_model_via_env:
            env["GROK_BUILD_MODEL"] = model
            env["GROK_BUILD_HEADLESS"] = "1"
            if step_id:
                env["GROK_BUILD_STEP_ID"] = step_id
        if base_url is None and self.registry is not None:
            entry = self.registry.get(model) if hasattr(self.registry, "get") else None
            if entry is not None:
                base_url = getattr(entry, "base_url", None)
        if base_url:
            env["GROK_BUILD_BASE_URL"] = str(base_url)
        return env

    def run_step(
        self,
        task: str,
        model: str,
        step_id: str = "",
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> GrokBuildResult:
        """Run one headless grok-build session bound to ``model``."""
        cmd = self.build_command(task, model)
        env = self.build_env(model, step_id, base_url=base_url)
        to = self.timeout if timeout is None else timeout
        cwd = str(self.working_dir) if self.working_dir else None
        started = time.time()

        log.info(
            "grok-build exec step=%s model=%s binary=%s task=%r",
            step_id or "-",
            model,
            cmd[0] if cmd else "?",
            _truncate_task(task),
        )

        if self.run_fn is not None:
            return self._run_via_injectable(cmd, cwd, env, to, model, step_id, started)

        return self._run_via_popen(cmd, cwd, env, to, model, step_id, started)

    def _run_via_injectable(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str],
        to: float,
        model: str,
        step_id: str,
        started: float,
    ) -> GrokBuildResult:
        try:
            completed = self.run_fn(
                cmd,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=to,
            )
            stdout = getattr(completed, "stdout", "") or ""
            stderr = getattr(completed, "stderr", "") or ""
            code = int(getattr(completed, "returncode", 1))
            usage = parse_usage_from_output(stdout, stderr)
            return GrokBuildResult(
                exit_code=code,
                stdout=stdout,
                stderr=stderr,
                usage=usage,
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error="" if code == 0 else (stderr.strip() or f"exit {code}"),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            return GrokBuildResult(
                exit_code=-1,
                stdout=stdout,
                stderr=stderr or f"timeout after {to}s",
                usage=parse_usage_from_output(stdout, stderr),
                timed_out=True,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error=f"timeout after {to}s",
            )
        except FileNotFoundError as exc:
            return GrokBuildResult(
                exit_code=127,
                stdout="",
                stderr=str(exc),
                usage={},
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error=f"binary not found: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return GrokBuildResult(
                exit_code=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                usage={},
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_via_popen(
        self,
        cmd: list[str],
        cwd: str | None,
        env: dict[str, str],
        to: float,
        model: str,
        step_id: str,
        started: float,
    ) -> GrokBuildResult:
        """Default runner: Popen with process-group kill on timeout."""
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        # New process group so we can kill the tree
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            popen_kwargs["start_new_session"] = True

        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(timeout=to if to and to > 0 else None)
            except subprocess.TimeoutExpired:
                self.kill_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except Exception:  # noqa: BLE001
                    stdout, stderr = "", ""
                log.warning(
                    "grok-build timeout step=%s after %.1fs (tree killed)",
                    step_id,
                    to,
                )
                return GrokBuildResult(
                    exit_code=-1,
                    stdout=stdout or "",
                    stderr=stderr or f"timeout after {to}s",
                    usage=parse_usage_from_output(stdout or "", stderr or ""),
                    timed_out=True,
                    model=model,
                    step_id=step_id,
                    duration_s=round(time.time() - started, 3),
                    command=cmd,
                    error=f"timeout after {to}s",
                )
            code = int(proc.returncode if proc.returncode is not None else 1)
            usage = parse_usage_from_output(stdout or "", stderr or "")
            return GrokBuildResult(
                exit_code=code,
                stdout=stdout or "",
                stderr=stderr or "",
                usage=usage,
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error="" if code == 0 else ((stderr or "").strip() or f"exit {code}"),
            )
        except FileNotFoundError as exc:
            return GrokBuildResult(
                exit_code=127,
                stdout="",
                stderr=str(exc),
                usage={},
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error=f"binary not found: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            if proc is not None:
                self.kill_tree(proc)
            return GrokBuildResult(
                exit_code=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                usage={},
                timed_out=False,
                model=model,
                step_id=step_id,
                duration_s=round(time.time() - started, 3),
                command=cmd,
                error=f"{type(exc).__name__}: {exc}",
            )

    def kill_tree(self, proc: Any) -> None:
        """Best-effort process + process-group teardown."""
        try:
            if proc is None:
                return
            if hasattr(proc, "poll") and proc.poll() is not None:
                return
            pid = getattr(proc, "pid", None)
            if os.name == "nt" and pid:
                # taskkill /T kills the whole tree on Windows
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                except Exception:  # noqa: BLE001
                    if hasattr(proc, "kill"):
                        proc.kill()
            else:
                # POSIX: kill process group
                try:
                    if pid:
                        os.killpg(pid, signal.SIGKILL)
                except Exception:  # noqa: BLE001
                    if hasattr(proc, "kill"):
                        proc.kill()
            if hasattr(proc, "wait"):
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.debug("kill_tree ignored error: %s", exc)
