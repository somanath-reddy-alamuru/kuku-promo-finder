# Promo Finder

**AI-powered microdrama promo discovery and ranking.** Upload screenplay text files and Promo Finder proposes 20–60 second promo segments with episode numbers, timestamps, hook and cliffhanger lines, scores, reasons and quality warnings.

The application is built for the Kuku AI Generalist assignment, Problem 1. It reads **screenplays, not video**; visual acting, music and edits that are absent from the script cannot be evaluated.

## What it does

1. **Parse (Python):** screenplay blocks become timestamped beats: camera, action, dialogue or sound.
2. **Rate (AI or keyword baseline):** an LLM rates hook and cliffhanger strength, or a simple keyword baseline runs without an API key.
3. **Search (Python):** deterministic code builds legal 20–60 second clips, checks line boundaries and camera-cut adjustments, and scores candidate clips.
4. **Judge (AI):** shortlisted promos are evaluated independently for hook, cliffhanger and standalone clarity.
5. **Audit and rank (Python):** rule-breaking candidates are rejected; the remaining promos are scored and ordered for posting.

The LLM does not create timestamps. It returns screenplay line IDs; Python checks those IDs and derives timestamps from the parsed source.

## Run locally

Use Python 3.12 (recommended for the hosted deployment) or a compatible Python 3.11+ environment.

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

The UI accepts one ZIP of screenplay `.txt` files or multiple `.txt` files. A small invented demo episode is included so the interface can be tried without confidential Kuku material.

## Analysis modes

- **Keyword baseline:** no API key required. This is a simple comparison baseline, not an AI evaluation.
- **AI-powered:** each visitor supplies their own provider, model ID and API key in the UI. The public application does not use a shared owner API key. The application sends screenplay dialogue, actions, camera notes and timestamps to the chosen model provider; do not upload confidential scripts unless you are authorised and have checked the provider's terms.

The local CLI also supports environment-based model configuration:

```powershell
$env:LLM_BASE_URL = "gemini"
$env:LLM_API_KEY = "YOUR_API_KEY"
$env:LLM_MODEL = "YOUR_MODEL_ID"
python run.py "C:\path\to\screenplays" --out outputs_ai
```

Keyword baseline from the CLI:

```powershell
python run.py "C:\path\to\screenplays" --no-llm --out outputs_baseline
```

Input may be a folder of `.txt` files, a ZIP containing TXT files, or one TXT file.

## Tests

Development-only test dependencies are in `requirements-dev.txt`:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -rs
```

Without the private 50-episode input pack, three full-pack tests are skipped; the remaining tests use synthetic test data. To run the full suite, extract the private input pack locally and set `KUKU_PACK` to its folder before running pytest. **Do not commit the screenplay pack to GitHub.**

## Optional human-reference evaluation

`eval.py` compares generated promos with a manually prepared reference file using one-to-one temporal matching. Start from `gold_template.csv`, make your selections before reviewing the tool's output, and then run:

```bash
python eval.py --gold gold.csv --run AI=outputs_ai --run Keywords=outputs_baseline --out comparison
```

Do not report precision, recall or F1 until a genuine human reference set has been prepared.

## Repository contents

| Path | Purpose |
|---|---|
| `app.py` | Streamlit user interface |
| `promo_finder/` | Parsing, input validation, prompts, LLM client, search, audit and ranking |
| `examples/` | One invented demo screenplay (not Kuku content) |
| `run.py` | Local command-line entry point |
| `eval.py`, `gold_template.csv` | Optional comparison against manually selected promos |
| `tests/` | Automated tests; not required at application runtime |
| `.streamlit/config.toml` | Streamlit theme and upload configuration |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | Test dependency |
| `DEPLOYMENT.md` | GitHub and Streamlit Community Cloud instructions |

## Output and confidentiality

Each run can create ranked CSV/JSON, a quality report, line tags, rejected trailer proposals, run logs and (in AI mode) raw model prompts and responses. Those files may contain screenplay-derived content.

Do not commit Kuku's screenplays, input ZIPs, raw outputs, model caches, `gold.csv`, `.env`, Streamlit secrets or API keys. The `.gitignore` excludes common private and generated files. Review `git status --short` before every push.
