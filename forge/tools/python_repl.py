from __future__ import annotations
import json
import subprocess
import tempfile
import os
from .registry import ToolRegistry
from forge.config import SHELL_TIMEOUT_SECONDS, SHELL_WORKING_DIR


def run_python(code: str, _sandbox_cwd: str = "") -> str:
    """Execute Python code in a subprocess and return stdout + stderr."""
    try:
        cwd = _sandbox_cwd if _sandbox_cwd else str(SHELL_WORKING_DIR)

        # Write code to a temp file and execute it
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            # Two encoding fixes for Windows:
            # 1. Capture stdout/stderr as UTF-8 (default cp1252 chokes
            #    on emoji + any byte >0x7F, which wrecked the arena's
            #    Pictionary RULES.txt read).
            # 2. PYTHONIOENCODING=utf-8 forces the child Python to
            #    WRITE utf-8 to stdout in the first place — without it,
            #    print("🎨") on Windows throws UnicodeEncodeError
            #    before our capture side even gets a chance.
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            result = subprocess.run(
                ["python", tmp_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SHELL_TIMEOUT_SECONDS,
                cwd=cwd,
                env=env,
            )
            output = {
                "returncode": result.returncode,
                "stdout": result.stdout[:4_000] if result.stdout else "",
                "stderr": result.stderr[:2_000] if result.stderr else "",
            }
            return json.dumps(output)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Python execution timed out after {SHELL_TIMEOUT_SECONDS}s"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# -- Registration ------------------------------------------------------------

def register(registry: ToolRegistry):
    registry.register(
        name="run_python",
        description=(
            "Execute a Python code snippet and return stdout/stderr. "
            f"Runs in {SHELL_WORKING_DIR} with a {SHELL_TIMEOUT_SECONDS}s timeout. "
            "Use for calculations, data processing, or testing code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
            },
            "required": ["code"],
        },
        handler=run_python,
    )
