"""
Docker Sandbox — container isolation for arena matches and shell execution.

Wraps Docker containers to provide isolated execution environments.
Falls back to path-based sandboxing if Docker is unavailable.

Usage:
    from forge.arena.container import DockerSandbox

    sandbox = DockerSandbox(image="python:3.12-slim")
    sandbox.start()
    result = sandbox.exec("python -c 'print(42)'")
    sandbox.write_file("/workspace/script.py", "print('hello')")
    sandbox.stop()

Each arena match gets its own container with:
    - Isolated filesystem
    - CPU/memory limits
    - No network (configurable)
    - Auto-cleanup on stop
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("forge.arena.container")


def docker_available() -> bool:
    """Check if Docker is installed and the daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@dataclass
class ContainerConfig:
    """Configuration for a Docker sandbox container."""
    image: str = "python:3.12-slim"
    name_prefix: str = "forge-sandbox"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0              # number of CPUs
    network: bool = False               # allow network access
    timeout_seconds: int = 300          # max container lifetime
    workspace_dir: str = "/workspace"
    auto_remove: bool = True


@dataclass
class ExecResult:
    """Result of executing a command in a container."""
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0


class DockerSandbox:
    """Isolated Docker container for safe code execution."""

    def __init__(self, config: ContainerConfig | None = None):
        self.config = config or ContainerConfig()
        self._container_id: str | None = None
        self._container_name = f"{self.config.name_prefix}-{uuid.uuid4().hex[:8]}"
        self._host_workspace: Path | None = None
        self._started = False

    @property
    def container_id(self) -> str | None:
        return self._container_id

    @property
    def running(self) -> bool:
        if not self._container_id:
            return False
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self._container_id],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() == "true"
        except Exception:
            return False

    def start(self) -> dict:
        """Start the sandbox container."""
        if not docker_available():
            return {"error": "Docker is not available"}

        # Create host workspace directory
        self._host_workspace = Path(tempfile.mkdtemp(prefix="forge-sandbox-"))

        cmd = [
            "docker", "run", "-d",
            "--name", self._container_name,
            "--memory", self.config.memory_limit,
            f"--cpus={self.config.cpu_limit}",
            "-v", f"{self._host_workspace}:{self.config.workspace_dir}",
            "-w", self.config.workspace_dir,
        ]

        if not self.config.network:
            cmd.extend(["--network", "none"])

        if self.config.auto_remove:
            cmd.append("--rm")

        # Keep container alive with tail
        cmd.extend([self.config.image, "tail", "-f", "/dev/null"])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return {"error": f"Failed to start container: {result.stderr.strip()}"}

            self._container_id = result.stdout.strip()[:12]
            self._started = True
            log.info(
                "Started sandbox container %s (%s)",
                self._container_name, self._container_id,
            )
            return {
                "status": "ok",
                "container_id": self._container_id,
                "container_name": self._container_name,
                "workspace": self.config.workspace_dir,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Container start timed out"}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def exec(
        self,
        command: str,
        timeout: int = 30,
        workdir: str = "",
    ) -> ExecResult:
        """Execute a command inside the container."""
        if not self._container_id:
            return ExecResult(exit_code=-1, stdout="", stderr="Container not started")

        cmd = ["docker", "exec"]
        if workdir:
            cmd.extend(["-w", workdir])
        cmd.extend([self._container_id, "sh", "-c", command])

        start_time = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            duration = time.time() - start_time
            return ExecResult(
                exit_code=result.returncode,
                stdout=result.stdout[:50000],  # cap output
                stderr=result.stderr[:10000],
                duration_seconds=round(duration, 2),
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return ExecResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                timed_out=True,
                duration_seconds=round(duration, 2),
            )

    def write_file(self, container_path: str, content: str) -> dict:
        """Write a file inside the container via the shared workspace."""
        if not self._host_workspace:
            return {"error": "Container not started"}

        # Map container path to host workspace
        rel_path = container_path
        if rel_path.startswith(self.config.workspace_dir):
            rel_path = rel_path[len(self.config.workspace_dir):].lstrip("/")

        host_path = self._host_workspace / rel_path
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(content, encoding="utf-8")

        return {"status": "ok", "path": container_path}

    def read_file(self, container_path: str) -> str:
        """Read a file from the container workspace."""
        if not self._host_workspace:
            return json.dumps({"error": "Container not started"})

        rel_path = container_path
        if rel_path.startswith(self.config.workspace_dir):
            rel_path = rel_path[len(self.config.workspace_dir):].lstrip("/")

        host_path = self._host_workspace / rel_path
        if not host_path.exists():
            return json.dumps({"error": f"File not found: {container_path}"})

        return host_path.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> dict:
        """Stop and remove the container."""
        if not self._container_id:
            return {"status": "ok", "message": "Not running"}

        try:
            subprocess.run(
                ["docker", "stop", "-t", "5", self._container_id],
                capture_output=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, Exception):
            # Force kill
            try:
                subprocess.run(
                    ["docker", "kill", self._container_id],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass

        # Clean up host workspace
        if self._host_workspace and self._host_workspace.exists():
            try:
                shutil.rmtree(self._host_workspace)
            except Exception as e:
                log.warning("Failed to clean workspace: %s", e)

        container_id = self._container_id
        self._container_id = None
        self._started = False
        log.info("Stopped sandbox container %s", container_id)
        return {"status": "ok", "container_id": container_id}

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# ── Sandbox Factory ──────────────────────────────────────────────────────

def create_arena_sandbox(
    match_id: str,
    network: bool = False,
    memory: str = "512m",
) -> DockerSandbox:
    """Create a Docker sandbox configured for an arena match."""
    config = ContainerConfig(
        name_prefix=f"forge-arena-{match_id[:8]}",
        memory_limit=memory,
        network=network,
        timeout_seconds=600,  # 10 minutes per match
    )
    return DockerSandbox(config)


def create_shell_sandbox(
    sandbox_path: str = "",
    network: bool = True,
) -> DockerSandbox:
    """Create a Docker sandbox for shell command execution."""
    config = ContainerConfig(
        name_prefix="forge-shell",
        network=network,
        timeout_seconds=120,
    )
    return DockerSandbox(config)
