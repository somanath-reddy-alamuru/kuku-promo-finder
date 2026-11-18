"""Command line:

    python run.py <folder | zip | txt> --out outputs            # AI mode (needs LLM_* settings)
    python run.py <folder | zip | txt> --no-llm --out baseline  # keyword baseline, no key needed
    python run.py --list-models                                 # model names your key can use

Model settings come from three environment variables (see README):
    LLM_BASE_URL  gemini | groq | openrouter | openai | any OpenAI-compatible URL
    LLM_MODEL     the model name
    LLM_API_KEY   your key (never written to any output file)
"""
import argparse, os, sys, time

from promo_finder import pipeline
from promo_finder.config import Settings
from promo_finder.inputs import InputError
from promo_finder.llm import LLM

ap = argparse.ArgumentParser(description="Find and rank promo clips in microdrama screenplays.")
ap.add_argument("source", nargs="?", help="a folder of .txt screenplays, a .zip, or one .txt")
ap.add_argument("--out", default="outputs", help="folder for the results (default: outputs)")
ap.add_argument("--no-llm", action="store_true", help="keyword baseline only, no API key needed")
ap.add_argument("--trailers", type=int, default=3, help="multi-episode trailers to build (AI mode)")
ap.add_argument("--per-episode", type=int, default=2, help="shortlisted clips per episode")
ap.add_argument("--judge-batch", type=int, default=1, help="clips per judge call (1 = independent)")
ap.add_argument("--pause", type=float, default=6.0, help="seconds between API calls (free-tier rate limit)")
ap.add_argument("--list-models", action="store_true", help="print the model names your key can use")
a = ap.parse_args()

llm = None if a.no_llm else LLM(cache_dir=os.path.join(a.out, "llm_calls"), pause=a.pause)
if a.list_models:
    if llm is None:
        ap.error("--list-models needs LLM settings, not --no-llm")
    print("\n".join(llm.list_models()))
    raise SystemExit
if not a.source:
    ap.error("give a folder, a .zip or a .txt")
if llm and not (llm.api_key and llm.model):
    raise SystemExit("Set LLM_API_KEY and LLM_MODEL (or use --no-llm). See README.")

cfg = Settings(trailers=a.trailers, per_episode=a.per_episode, judge_batch=a.judge_batch)
t0 = time.time()


def show(event):
    stage, done, total = event["stage"], event.get("done"), event.get("total")
    if stage == "parse" and "report" in event:
        r = event["report"]
        print(f"Parsed {r['parsed']} episodes from {r['files_read']} files, {r['beats']} beats, "
              f"{len(r['unusable'])} unusable, {len(r['duplicates'])} duplicates, "
              f"{sum(len(w) for w in r['warnings'].values())} warnings", flush=True)
    elif stage == "tag":
        extra = f" | fallback episodes: {event['fallback']}" if event.get("fallback") else ""
        print(f"[{time.time() - t0:5.0f}s] rated lines of episode {event['episode']} ({done}/{total}){extra}", flush=True)
    elif stage == "judge":
        print(f"[{time.time() - t0:5.0f}s] stranger test {done}/{total} ({event['attempt']})", flush=True)
    elif stage in ("trailers", "audit") and done == 0:
        print(f"[{time.time() - t0:5.0f}s] {stage}...", flush=True)


try:
    result = pipeline.run(a.source, llm, cfg, progress=show)
except InputError as e:
    raise SystemExit(f"Input problem: {e}")
except RuntimeError as e:
    raise SystemExit(f"Model problem: {e}")
pipeline.save(result, a.out)
print()
print(pipeline.quality_report(result))
for r in list(pipeline.rows(result["promos"]))[:10]:
    print(f"#{r['rank']:>2}  {r['score']:5}  {r['segments']}  ({r['seconds']}s)  {r['hook'][:70]}")
print(f"\nSaved {len(result['promos'])} promos to {a.out}/")
if llm and llm.stats["failed"]:
    print(f"\nWARNING: {llm.stats['failed']} model calls failed, so some results are estimates. "
          "Run the same command again: finished calls are reused, only the failed ones are retried.",
          file=sys.stderr)
