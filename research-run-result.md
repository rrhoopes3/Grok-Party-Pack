## Round 1 — 2026-04-29 10:02
**Change:** Shortened the "IMPORTANT" context-reuse rule in `forge/executor.py` from 173 chars to 78 chars
**Before:**
- Prompt chars: 1125
- Tools JSON bytes: 2338
- Tool count: 8
- Trading addendum: no
**After:**
- Prompt chars: 1030
- Tools JSON bytes: 2338
- Tool count: 8
- Trading addendum: no
**Delta:** -95 chars prompt (-8.4%), 0 bytes tools (0%)
**Observation:** The longest single rule line in EXECUTOR_SYSTEM_BASE was the biggest low-hanging fruit; trimming verbose phrasing saves ~100 chars without losing meaning.
**Top 3 longest descriptions:** render_widget: 2335 chars, fake_audio_detect: 888 chars, fake_audio_neuro_compare: 813 chars
