"""Compare runs with each other and with promos a person picked by hand.

    # one or more runs against your hand picks
    python eval.py --gold gold.csv --run AI=outputs_ai --run Keywords=outputs_baseline --out comparison

    # two runs against each other only (no hand picks yet)
    python eval.py --run AI=outputs_ai --run Keywords=outputs_baseline --out comparison

gold.csv has one row per hand-picked promo segment (see gold_template.csv):
    episode,start,end,note
    1,00:46.80,01:10.40,wedding betrayal

How a tool promo is matched to a hand pick
------------------------------------------
Overlap is measured as IoU (intersection over union) on the timeline of one episode:
shared seconds / seconds covered by either. 1.0 = identical, 0 = no overlap.
A pair counts as a match when IoU >= 0.5 (change with --threshold). At 0.5, a 30 s pick
still matches a 30 s promo shifted by 10 s (20 shared / 40 covered), but not one shifted
by 15 s. Matching is one-to-one: the pair with the highest IoU is matched first, then the
next highest among what is left, so one tool promo can never match two hand picks.

Only tool promos in episodes that have hand picks are counted, because for other episodes
we do not know what the right answer is.

    precision = matched / tool promos in those episodes   (how many picks were right)
    recall    = matched / hand picks                      (how many right answers were found)
    F1        = 2 * precision * recall / (precision + recall)
"""
import argparse, csv, json, os


def secs(t):
    t = str(t).strip()
    if ":" not in t:
        return float(t)
    parts = [float(x) for x in t.split(":")]
    return parts[-1] + 60 * parts[-2] + (3600 * parts[-3] if len(parts) > 2 else 0)


def read_gold(path):
    gold = []
    with open(path, encoding="utf-8-sig") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    for n, row in enumerate(csv.DictReader(lines), 2):
        try:
            s, e = secs(row["start"]), secs(row["end"])
            gold.append({"id": f"G{len(gold) + 1}", "episode": int(row["episode"]), "start": s,
                         "end": e, "note": (row.get("note") or "").strip()})
        except (KeyError, ValueError, TypeError):
            raise SystemExit(f"{path}: row {n} needs episode,start,end (got {dict(row)})")
        if e <= s:
            raise SystemExit(f"{path}: row {n} ends before it starts")
    return gold


def read_run(folder):
    with open(os.path.join(folder, "promos_ranked.json"), encoding="utf-8") as f:
        promos = json.load(f)
    log = {}
    if os.path.exists(os.path.join(folder, "run_log.json")):
        with open(os.path.join(folder, "run_log.json"), encoding="utf-8") as f:
            log = json.load(f)
    return promos, log


def iou(a, b):
    """a, b: (episode, start, end). Intersection over union on the same episode's timeline."""
    if a[0] != b[0]:
        return 0.0
    inter = max(0.0, min(a[2], b[2]) - max(a[1], b[1]))
    union = max(a[2], b[2]) - min(a[1], b[1])
    return inter / union if union > 0 else 0.0


def promo_iou(promo, g):
    """A multi-segment promo is compared through its best segment in the pick's episode."""
    return max((iou((s["episode"], s["start"], s["end"]), (g["episode"], g["start"], g["end"]))
                for s in promo["segments"]), default=0.0)


def match(promos, gold, threshold):
    """Greedy one-to-one matching by highest IoU first."""
    pairs = sorted(((promo_iou(p, g), pi, gi) for pi, p in enumerate(promos) for gi, g in enumerate(gold)),
                   reverse=True)
    used_p, used_g, matches = set(), set(), []
    for v, pi, gi in pairs:
        if v < threshold:
            break
        if pi not in used_p and gi not in used_g:
            used_p.add(pi)
            used_g.add(gi)
            matches.append((pi, gi, v))
    return matches


def score_run(promos, gold, threshold):
    eps = {g["episode"] for g in gold}
    pool = [p for p in promos if any(s["episode"] in eps for s in p["segments"])]
    m = match(pool, gold, threshold)
    tp = len(m)
    precision = tp / len(pool) if pool else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    matched_p = {pi for pi, _, _ in m}
    matched_g = {gi for _, gi, _ in m}
    # Union the generated intervals inside each hand-picked span so overlapping promos
    # cannot count the same second twice.
    covered_seconds = 0.0
    for g in gold:
        intervals = sorted((max(g["start"], s["start"]), min(g["end"], s["end"]))
                           for p in pool for s in p["segments"]
                           if s["episode"] == g["episode"] and s["end"] > g["start"] and s["start"] < g["end"])
        merged = []
        for a, b in intervals:
            if b <= a:
                continue
            if not merged or a > merged[-1][1]:
                merged.append([a, b])
            else:
                merged[-1][1] = max(merged[-1][1], b)
        covered_seconds += sum(b - a for a, b in merged)
    return {
        "tool_promos_in_gold_episodes": len(pool), "hand_picks": len(gold), "matched": tp,
        "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
        "mean_iou_of_matches": round(sum(v for _, _, v in m) / tp, 3) if tp else None,
        "share_of_hand_picked_seconds_covered": round(min(1.0, covered_seconds / sum(g["end"] - g["start"] for g in gold)), 3)
        if gold else None,
        "matches": [{"rank": pool[pi]["rank"], "hand_pick": gold[gi]["id"], "iou": round(v, 2)} for pi, gi, v in m],
        "false_positives": [{"rank": p["rank"], "segments": _label(p), "score": p["score"],
                             "best_iou": round(max((promo_iou(p, g) for g in gold), default=0), 2)}
                            for i, p in enumerate(pool) if i not in matched_p],
        "missed": [{"hand_pick": g["id"], "episode": g["episode"], "start": g["start"], "end": g["end"],
                    "note": g["note"], "best_iou": round(max((promo_iou(p, g) for p in pool), default=0), 2)}
                   for i, g in enumerate(gold) if i not in matched_g],
    }


def compare_runs(a, b, threshold):
    """How much two runs agree, without any hand picks."""
    m = match(a, [{"episode": s["episode"], "start": s["start"], "end": s["end"]}
                  for p in b for s in p["segments"][:1]], threshold)
    eps = lambda run: {s["episode"] for p in run for s in p["segments"]}
    return {"promos_a": len(a), "promos_b": len(b), "overlapping_promos": len(m),
            "episodes_covered_a": len(eps(a)), "episodes_covered_b": len(eps(b)),
            "episodes_covered_by_both": len(eps(a) & eps(b))}


def _label(p):
    return "; ".join(f"ep{s['episode']:02d} {s['start_ts']}-{s['end_ts']}" for s in p["segments"])


def report(runs, gold, threshold):
    names = list(runs)
    out = {"threshold_iou": threshold, "runs": {}, "pairwise": {}}
    lines = ["# Evaluation report", "", f"Match rule: IoU >= {threshold}, one-to-one, highest overlap first.", ""]
    lines += ["## The runs", "", "| Run | Mode | Promos | Episodes covered | AI-judged | Model calls | Tokens in/out |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for name, (promos, log) in runs.items():
        s = (log.get("llm") or {})
        eps = len({x["episode"] for p in promos for x in p["segments"]})
        calls = s.get("calls", 0) + s.get("cached", 0) if s else 0
        tokens = f"{s.get('input_tokens', 0):,}/{s.get('output_tokens', 0):,}" if s else "0/0"
        lines.append(f"| {name} | {log.get('mode', '?')} | {len(promos)} | {eps} | "
                     f"{sum(bool(p.get('ai_judged')) for p in promos)} | {calls} | {tokens} |")
        out["runs"][name] = {"promos": len(promos), "episodes_covered": eps, "mode": log.get("mode")}
    if gold:
        eps = sorted({g["episode"] for g in gold})
        lines += ["", f"## Against {len(gold)} hand picks in episodes {eps}", "",
                  "| Run | Tool promos in those episodes | Matched | Precision | Recall | F1 | Mean IoU | Seconds covered |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, (promos, _) in runs.items():
            r = score_run(promos, gold, threshold)
            out["runs"][name]["vs_hand_picks"] = r
            lines.append(f"| {name} | {r['tool_promos_in_gold_episodes']} | {r['matched']} | {r['precision']:.2f} | "
                         f"{r['recall']:.2f} | {r['f1']:.2f} | {r['mean_iou_of_matches'] or '-'} | "
                         f"{r['share_of_hand_picked_seconds_covered']:.0%} |")
        for name in names:
            r = out["runs"][name]["vs_hand_picks"]
            lines += ["", f"### {name}: tool promos that match no hand pick (false positives)", ""]
            lines += [f"- rank {x['rank']} | {x['segments']} | score {x['score']} | best IoU {x['best_iou']}"
                      for x in r["false_positives"]] or ["- none"]
            lines += ["", f"### {name}: hand picks the tool missed", ""]
            lines += [f"- {x['hand_pick']} episode {x['episode']} {x['start']:.1f}-{x['end']:.1f}s "
                      f"({x['note'] or 'no note'}) | best IoU {x['best_iou']}" for x in r["missed"]] or ["- none"]
    else:
        lines += ["", "## Against hand picks", "",
                  "No gold.csv given, so precision and recall cannot be measured yet. Fill gold_template.csv "
                  "with promos you pick yourself and run again with --gold."]
    if len(names) >= 2:
        lines += ["", "## How much the runs agree", ""]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                c = compare_runs(runs[a][0], runs[b][0], threshold)
                out["pairwise"][f"{a} vs {b}"] = c
                lines.append(f"- {a} vs {b}: {c['overlapping_promos']} promos overlap (IoU >= {threshold}); "
                             f"episodes covered {c['episodes_covered_a']} vs {c['episodes_covered_b']}, "
                             f"{c['episodes_covered_by_both']} by both.")
    lines += ["", "More promos is not better by itself: only matches against hand picks show which run picks well."]
    return out, "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", help="CSV of hand-picked promos")
    ap.add_argument("--run", action="append", required=True, metavar="NAME=FOLDER",
                    help="an output folder from run.py; repeat for several runs")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--out", help="folder to write evaluation.md and evaluation.json")
    a = ap.parse_args()
    runs = {}
    for item in a.run:
        name, _, folder = item.partition("=")
        runs[name if folder else os.path.basename(item.rstrip("/"))] = read_run(folder or name)
    gold = read_gold(a.gold) if a.gold else []
    data, text = report(runs, gold, a.threshold)
    print(text)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "evaluation.md"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(a.out, "evaluation.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        print(f"Saved to {a.out}/")
