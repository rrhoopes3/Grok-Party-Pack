You are a local Qwen model running inside the Forge harness. Your job is to measure your own harness overhead, identify the single biggest remaining inefficiency, make **one targeted surgical text reduction**, re-measure, and record everything in `research-run-result.md`. Then loop.

## Measurement Protocol

For each iteration, measure these four numbers:

1. **First-turn prompt size** — total characters in the full prompt sent to the model.
2. **Tool schema bytes** — total JSON bytes of the tools array for a representative task.
3. **Tool count** — how many tools are in the filtered set.
4. **Trading addendum presence** — does the system prompt include the trading rules section? (yes/no)

**Use this exact portable measurement script** (it auto-detects the repo root):

```python
import sys, json, os
from pathlib import Path

REPO_ROOT = Path(os.getcwd()).resolve()
if not (REPO_ROOT / "forge").exists():
    for candidate in [Path.home() / "Grok-Party-Pack", Path("B:/Grok/Grok-Party-Pack"), Path("/home/workdir"), Path("/home/workdir/artifacts")]:
        if (candidate / "forge").exists():
            REPO_ROOT = candidate
            break

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from forge.executor import _build_system_prompt
from forge.tools import create_registry
from forge.tools.registry import infer_tools_for_task
from forge.providers import _to_openai_tools

TASK = "list the files in this directory"
registry = create_registry()
tool_filter = infer_tools_for_task(TASK)
prompt = _build_system_prompt(tool_filter)
tools = _to_openai_tools(registry, only=tool_filter, compact=True)
tools_json = json.dumps(tools)

print(f"Prompt chars: {len(prompt)}")
print(f"Tools JSON bytes: {len(tools_json.encode())}")
print(f"Tool count: {len(tools)}")
print(f"Trading addendum: {'Trading Tools' in prompt}")
print(f"Repo root used: {REPO_ROOT}")
Strict Execution Rules (MUST FOLLOW)

You MUST run the exact measurement script above before and after every change.
You are only allowed ONE single-line text reduction per round. No refactoring, no moving code, no changing logic or function names.
Always quote the exact line you plan to change before editing the file.
If the measurement script fails or throws an error, print the full error and stop immediately.
After every round, also print the three longest tool descriptions (by character count) from the current registry.
If any metric goes up, immediately revert the change and try something else.
Think step-by-step before every action. Be ruthless about bloat but extremely careful.

Iteration Loop
Step 1: Run Baseline
Execute the measurement script above using the run_python tool. Record all four numbers as your Before state.
Step 2: Identify the Biggest Inefficiency
Read these files and find the single highest-impact text reduction:

forge/executor.py
forge/tools/registry.py
forge/providers.py

Recommended first target (Round 1): Make the long "Trading Tools" addendum conditional. Only include the trading rules paragraph if the task mentions trading, stocks, crypto, PCR, portfolio, markets, or similar keywords. This should save thousands of characters on normal tasks.
Step 3: Make the Change
Edit only one line. Add a short comment on that line explaining what you trimmed and why.
Step 4: Re-measure
Run the measurement script again and record the After numbers.
Step 5: Write Results
Append a new entry to research-run-result.md (create the file if it doesn't exist) using exactly this format:
text## Round N — YYYY-MM-DD HH:MM
**Change:** [one-line description of what you changed and in which file]
**Before:**
- Prompt chars: X
- Tools JSON bytes: X
- Tool count: X
- Trading addendum: yes/no
**After:**
- Prompt chars: X
- Tools JSON bytes: X
- Tool count: X
- Trading addendum: yes/no
**Delta:** -X chars prompt (-Y%), -Z bytes tools (-W%)
**Observation:** [one sentence on what you learned]
**Top 3 longest descriptions:** [list the three longest tool descriptions with their char counts]
Step 6: Commit
Run:
Bashgit -C B:\Grok\Grok-Party-Pack add -A
git -C B:\Grok\Grok-Party-Pack commit -m "research: round N — <short description>"
Step 7: Loop
Go back to Step 2. Stop after 5 rounds maximum or when Prompt chars < 800 AND Tools JSON bytes < 4500, whichever comes first.
Constraints

Only edit files under forge/
Do not change function signatures, return types, or tool names
Do not break infer_tools_for_task or _build_system_prompt
Every round must reduce at least one metric (preferably both)
Write results after every round, even if the gain is small

End State
When finished, add a final ## Summary section to research-run-result.md with:

Total chars saved from prompt
Total bytes saved from tools JSON
The single change with the best ROI
One sentence on what remains to be done that you couldn't tackle in this session


Begin now.
First action: Run the measurement script and report the baseline numbers.
text---

