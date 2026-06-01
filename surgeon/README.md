# Surgeon (OBLITERATUS)

Standalone model surgery toolkit — extracted from the Grok Party Pack.

This is the heavy research-grade abliteration tool. It lets you probe a model's refusal geometry and surgically remove (or attenuate) refusal directions while attempting to preserve general capabilities.

It was previously buried inside The Forge (Grok-Party-Pack). We pulled it out so normal people can use the Party Pack without needing a 24 GB VRAM card and a manual clone of a non-pip-installable research repo just to import the tool registry.

## The One Thing You Must Do First

This tool is a thin, opinionated wrapper around **[OBLITERATUS](https://github.com/Projects/OBLITERATUS)**.

You must clone it yourself:

```bash
git clone https://github.com/Projects/OBLITERATUS OBLITERATUS-main
```

Then tell Surgeon where it lives (in priority order):

1. Environment variable (recommended)
   ```bash
   export OBLITERATUS_ROOT=/absolute/path/to/OBLITERATUS-main
   ```

2. Pass it explicitly to functions (see below)

3. It will also search a few common locations for convenience.

## Installation (the other heavy bits)

```bash
pip install torch transformers accelerate safetensors datasets pydantic
```

(Use the CUDA wheels for torch if you have a GPU.)

## Quick Usage

### Python API

```python
from surgeon import check_dependencies, scan_model, operate

# 1. Verify your setup
print(check_dependencies())

# 2. Scan first (cheap, tells you which layers are carrying refusal)
scan = scan_model("meta-llama/Llama-3.1-8B-Instruct", device="cuda")
print(scan.strong_layers)
print(scan.recommended_method)

# 3. Run surgery
record = operate(
    model_name="meta-llama/Llama-3.1-8B-Instruct",
    method="advanced",           # or "aggressive", "surgical", etc.
    device="cuda",
    dtype="float16",
)
print(record.output_path)
print(record.quality_metrics)
```

### CLI (very minimal for now)

```bash
python -m surgeon.cli check
python -m surgeon.cli scan meta-llama/Llama-3.1-8B-Instruct --device cuda
python -m surgeon.cli operate meta-llama/Llama-3.1-8B-Instruct --method advanced --device cuda
```

(The CLI is intentionally tiny in v1. The real power is the Python API + the web UI below.)

## Web UI (the nice part)

```bash
python surgeon/web_app.py
```

Then open http://localhost:5001 (or whatever port it picks).

This is basically the old "Surgeon" tab from The Forge, now living in its own focused little app.

## Methods (quick reference)

See `AVAILABLE_METHODS` in `engine.py` for the full table. Highlights:

- `advanced` — good default (4 directions, norm-preserving)
- `aggressive` / `surgical` — when you want to go harder
- `nuclear` — everything, maximum drama, highest VRAM

## Data Location

All operations, saved models, logs, etc. go under:

- `~/.surgeon/` (by default)
- Or set `SURGEON_HOME=/some/other/place`

This is deliberately separate from any Forge data directory now.

## Why It Was Extracted

The Grok Party Pack was intentionally a maximalist "yes, and..." project. At some point the "and also model surgery via a whole separate research repo" started punishing normal users who just wanted to play with the arenas or the LCARS UI or the trading tools.

So we cut it out. The Party Pack is now lighter. This thing gets to be its own weird, high-VRAM, slightly dangerous research toy.

## Origin

Born inside https://github.com/rrhoo/Grok-Party-Pack as the `forge/surgeon/` module.

If you're here because you found the Party Pack first: welcome. This is the part that made the dependency graph explode.

## License / Credit

OBLITERATUS is the real work. This is just a convenient (and opinionated) interface + persistence + UI layer on top of it.
