"""The whole flow in one place.

  1. parse      (code)  screenplay files -> timestamped lines            (inputs.py, parse.py)
  2. tag        (AI)    rate every line: how good as an OPENING (hook), how good as an ENDING (cliff)
  3. search     (code)  build EVERY legal 20-60 s clip that opens on a strong hook line and closes
                        on a strong cliff line; score them; shortlist the best per episode
  4. trailers   (AI)    join a few top moments from different episodes; code checks the result
  5. judge      (AI)    a "stranger" who sees only the clip rates hook / cliffhanger / standalone
  6. audit      (code)  re-check every promo; anything that breaks the brief is rejected, not ranked
  7. rank       (code)  final score, then a posting order that mixes angles and episodes

AI makes the taste calls. Code does the searching, the timing, the checking and the maths.
"""
import csv, io, json, math, os, re, time
from collections import Counter
from itertools import accumulate

from . import prompts, rules
from .config import DEFAULT
from .inputs import parse_all
from .parse import transcript

NOT_JUDGED = "not judged"


# ---------------------------------------------------------------- step 2: tag every line
def tag_with_ai(ep, llm):
    """One call per episode. Returns {line_id: (hook, cliff)}, or None if the answer was unusable."""
    user = f"EPISODE {ep['episode']} (runs {ep['duration']:.0f} seconds)\n\n{transcript(ep['beats'])}"
    out = llm.ask_json(prompts.TAG_SYSTEM, user, tag=f"tag_ep{ep['episode']:03d}")
    if not isinstance(out, dict) or not isinstance(out.get("lines"), list):
        return None
    valid = {b.id for b in rules.spoken(ep)}
    tags = {}
    for row in out["lines"]:
        if isinstance(row, dict) and row.get("id") in valid:
            hook, cliff = _mark(row.get("hook")), _mark(row.get("cliff"))
            if hook is not None and cliff is not None:
                tags[row["id"]] = (hook, cliff)
    # A reply that names no valid line at all is treated as a failure, not as "nothing is good".
    return tags if tags or not out["lines"] else None


HOOK_WORDS = r"slap|strik|shov|seiz|grab|blood|dead|die|kill|poison|pregnan|marry|liar|thief|arrest|guards|secret|betray|how dare|get out|furious|scream|shout|cruel|venom|mock|gloat|accus|outrag|!"
CLIFF_WORDS = r"\?|\.\.\.|gasp|freez|stunned|shock|horrif|reveal|bursts?|threat|who are you|what have you"
WRITER_CLIFF = r"cliffhang|ominous|suspens|tense|tension|dread|sting|abrupt|freez|shock|gasp|dramatic"


def tag_with_keywords(ep):
    """The no-AI tagger: count drama words in the line and in the script's own emotion label
    (e.g. "Outraged, Accusing"). Crude on purpose: it is the baseline the AI has to beat,
    and the fallback that keeps a run going when the AI fails."""
    tags = {}
    for b in rules.spoken(ep):
        text = (b.speaker + " " + b.text).lower()
        tags[b.id] = (min(3, len(re.findall(HOOK_WORDS, text))), min(3, len(re.findall(CLIFF_WORDS, text))))
    # Evidence-based ending rule: if the writer marks suspense in the episode's last 6 seconds
    # ("swells to an ominous cliffhanger peak", "sting", ...), the final spoken line gets +1 cliff.
    # A neutral final line (0) only reaches 1, which is still too weak to close a clip.
    tail = " ".join(b.text.lower() for b in ep["beats"]
                    if b.kind in ("CAM", "SFX") and b.end >= ep["duration"] - 6)
    lines = rules.spoken(ep)
    if lines and re.search(WRITER_CLIFF, tail):
        hook, cliff = tags[lines[-1].id]
        tags[lines[-1].id] = (hook, min(3, cliff + 1))
    return tags


# ---------------------------------------------------------------- step 3: search every legal clip
def search_episode(ep, tags, cfg=DEFAULT):
    """Try every (opening line, closing line) pair.
    Returns (shortlist, number of legal clips, number of strong clips)."""
    lines = rules.spoken(ep)
    heat = [max(tags.get(b.id, (0, 0))) for b in lines]
    prefix = [0] + list(accumulate(heat))           # prefix[k] = heat of lines[0..k-1]
    n_legal, strong = 0, []
    for i, first in enumerate(lines):
        for j in range(i, len(lines)):
            last = lines[j]
            if last.end - first.start > cfg.max_len + cfg.end_snap + cfg.start_snap:
                break
            seg = rules.make_segment(ep, first, last, cfg)
            if not rules.legal(ep, seg, cfg):
                continue
            n_legal += 1
            hook, cliff = tags.get(first.id, (0, 0))[0], tags.get(last.id, (0, 0))[1]
            if hook < cfg.strong or cliff < cfg.strong:
                continue
            middle = (prefix[j + 1] - prefix[i]) / (j - i + 1)
            changes = rules.scene_changes(ep, seg)
            silence = rules.longest_silence(ep, seg)
            penalty = changes * cfg.scene_change_penalty + \
                (cfg.dead_air_penalty if silence >= cfg.dead_air_seconds else 0)
            score = hook + cliff + cfg.middle_weight * middle - penalty
            why = f"Opens on a line tagged hook {hook}/3, closes on a line tagged cliff {cliff}/3"
            if changes:
                why += f", crosses {changes} scene change{'s' if changes > 1 else ''}"
            if silence >= cfg.dead_air_seconds:
                why += f", has {silence:.0f}s with nothing said or done"
            strong.append({"segments": [seg], "type": "single", "search_score": round(score, 2),
                           "search_parts": {"hook": hook, "cliff": cliff, "middle": round(middle, 3),
                                            "scene_changes": changes, "longest_silence": silence,
                                            "penalty": penalty},
                           "hook_tag": hook, "cliff_tag": cliff,
                           "hook": _short(first), "cliffhanger": _short(last), "why": why})
    strong.sort(key=lambda w: (-w["search_score"], w["segments"][0]["seconds"]))   # ties: shorter clip
    shortlist = []
    for w in strong:
        if len(shortlist) >= cfg.per_episode:
            break
        if all(rules.overlap(w, k) <= cfg.max_overlap for k in shortlist):
            shortlist.append(w)
    return shortlist, n_legal, len(strong)


# ---------------------------------------------------------------- step 4: multi-episode trailers
def find_moments(episodes, all_tags, cfg=DEFAULT):
    """Each episode's single strongest line (a tag of 3) plus what follows within 9 seconds."""
    moments = {}
    for ep in episodes.values():
        tags = all_tags[ep["episode"]]
        top = [b for b in rules.spoken(ep) if max(tags.get(b.id, (0, 0))) == 3]
        if not top:
            continue
        first = max(top, key=lambda b: sum(tags[b.id]))
        after = [b for b in rules.spoken(ep) if b.start >= first.start and b.end <= first.start + 9]
        seg = rules.make_segment(ep, first, after[-1] if after else first, cfg)
        if 3 <= seg["seconds"] <= 15 and not rules.cuts_dialogue(ep["beats"], seg["start"]) \
                and not rules.cuts_dialogue(ep["beats"], seg["end"]):
            moments[f"E{ep['episode']:02d}"] = seg
    return moments


def build_trailers(episodes, moments, llm, cfg, rejected):
    """One AI call decides which moments to join; code checks ids, length and story order."""
    if len(moments) < 2 or cfg.trailers < 1:
        return []
    listing = "\n\n".join(f"{mid} | {s['seconds']:.0f}s\n{clip_text(episodes, [s], mark=None)}"
                          for mid, s in moments.items())
    out = llm.ask_json(prompts.MONTAGE_SYSTEM.replace("MAX_MONTAGES", str(cfg.trailers)),
                       listing, tag="montage") or {}
    promos = []
    for m in (out.get("montages") or [])[:cfg.trailers]:
        ids = m.get("moments") if isinstance(m, dict) else None
        if not isinstance(ids, list) or not all(i in moments for i in ids) \
                or not 2 <= len(ids) <= 4 or len(set(ids)) != len(ids):
            reason = "unknown or repeated moment id, or not 2-4 moments"
        else:
            segs = [moments[i] for i in ids]
            total = sum(s["seconds"] for s in segs)
            if not cfg.min_len <= total <= cfg.max_len:
                reason = f"total length {total:.1f}s is outside {cfg.min_len:.0f}-{cfg.max_len:.0f}s"
            elif segs != sorted(segs, key=lambda s: s["episode"]):
                reason = "moments not in story order"
            else:
                promos.append({"segments": [dict(s) for s in segs], "type": "trailer",
                               "search_score": None, "search_parts": None,
                               "hook_tag": None, "cliff_tag": None, "hook": str(m.get("hook", ""))[:200],
                               "cliffhanger": str(m.get("cliffhanger", ""))[:200],
                               "why": str(m.get("why", ""))[:300]})
                continue
        rejected.append({"stage": "trailer", "reason": reason, "proposal": m})
    return promos


# ---------------------------------------------------------------- step 5: the stranger test
def clip_text(episodes, segments, mark=DEFAULT.hook_window):
    """Exactly what a viewer of the clip would get, with times counted from the clip's start."""
    parts, offset = [], 0.0
    for n, s in enumerate(segments):
        beats = rules.segment_beats(episodes[s["episode"]], s)
        lines = transcript(beats, t0=s["start"] - offset, mark_first=mark if n == 0 else None)
        parts.append(re.sub(r"(?m)^(>> )?L\d+ \| ", r"\1", lines))
        offset += s["seconds"]
    return "\n[CUT]\n".join(parts)


def valid_judgement(c):
    """A judge answer is used only if all three marks are numbers from 1 to 10."""
    if not isinstance(c, dict):
        return None
    marks = [_score(c.get(k)) for k in ("hook", "cliffhanger", "standalone")]
    if None in marks:
        return None
    angle = str(c.get("angle", "")).strip().lower()
    return {"hook": marks[0], "cliffhanger": marks[1], "standalone": marks[2],
            "retell": str(c.get("retell", "")).strip()[:300],
            "confusion": str(c.get("confusion", "")).strip()[:300],
            "angle": angle if angle in prompts.ANGLES else "other"}


def judge(episodes, promos, llm, cfg=DEFAULT, progress=None):
    """Each clip goes to the stranger on its own (cfg.judge_batch = 1), so the model cannot use
    what it read in another clip. A clip whose answer is missing or invalid is asked once more."""
    size = max(1, cfg.judge_batch)
    for attempt in ("judge", "rejudge"):
        todo = [p for p in promos if not p.get("_judge")]
        for j in range(0, len(todo), size):
            batch = todo[j:j + size]
            user = "\n\n".join(f"=== CLIP C{i} ({sum(s['seconds'] for s in p['segments']):.0f}s) ===\n" +
                               clip_text(episodes, p["segments"], cfg.hook_window)
                               for i, p in enumerate(batch, 1))
            tag = f"{attempt}_" + "_".join(_clip_key(p) for p in batch)[:80]
            out = llm.ask_json(prompts.JUDGE_SYSTEM, user, tag=tag) or {}
            got = {str(c.get("clip", "")).upper(): c for c in out.get("clips") or [] if isinstance(c, dict)}
            if size == 1 and len(got) == 1 and "C1" not in got:      # model renamed the only clip
                got = {"C1": next(iter(got.values()))}
            for i, p in enumerate(batch, 1):
                p["_judge"] = valid_judgement(got.get(f"C{i}"))
            if progress:
                progress({"stage": "judge", "done": min(j + size, len(todo)), "total": len(todo),
                          "attempt": attempt})


def final_score(hook, cliffhanger, standalone, cfg=DEFAULT):
    """0-100. Weighted geometric mean of the three marks (equal weights by default).
    A promo is only as good as its weakest part: 9, 9, 2 scores 54.5 while 7, 7, 7 scores 70.0."""
    w = (cfg.w_hook, cfg.w_cliffhanger, cfg.w_standalone)
    logs = w[0] * math.log(hook) + w[1] * math.log(cliffhanger) + w[2] * math.log(standalone)
    return round(10 * math.exp(logs / sum(w)), 1)


# ---------------------------------------------------------------- step 7: posting order
def posting_order(promos, cfg=DEFAULT):
    """AI-judged promos first, best score first, but do not post the same angle twice in a row
    or the same episode within the last few posts: a feed of near-identical clips wastes most of them."""
    left = sorted(promos, key=lambda p: (-p["ai_judged"], -p["score"], p["segments"][0]["episode"]))
    plan = []
    while left:
        def value(p):
            repeat_angle = plan and p["angle"] == plan[-1]["angle"] and p["angle"] not in ("other", NOT_JUDGED)
            recent = {s["episode"] for q in plan[-cfg.recent_posts:] for s in q["segments"]}
            repeat_episode = any(s["episode"] in recent for s in p["segments"])
            return (p["ai_judged"], p["score"] - cfg.repeat_angle_penalty * bool(repeat_angle)
                    - cfg.repeat_episode_penalty * repeat_episode)
        pick = max(left, key=value)             # max() keeps the first of equals: the higher raw score
        if not plan:
            pick["post_reason"] = "Highest score of all promos"
        elif pick is left[0]:
            pick["post_reason"] = f"Best remaining score (angle: {pick['angle']})"
        else:
            pick["post_reason"] = ("Moved ahead of higher-scoring clips that would repeat the previous "
                                   f"angle or a recent episode (angle: {pick['angle']})")
        if not pick["ai_judged"]:
            pick["post_reason"] += "; score estimated from line tags, not judged by the AI"
        left.remove(pick)
        plan.append(pick)
    for n, p in enumerate(plan, 1):
        p["rank"] = n
    return plan


# ---------------------------------------------------------------- run everything
def run(source, llm=None, cfg=DEFAULT, progress=lambda event: None):
    """source: folder, .zip, .txt, or [(name, text or bytes)]. llm: an LLM, or None for keywords only.
    progress: called with dicts like {"stage": "tag", "done": 3, "total": 50, "episode": 3}."""
    t0 = time.time()
    progress({"stage": "parse", "done": 0, "total": 1})
    episodes, parse_report = parse_all(source)
    progress({"stage": "parse", "done": 1, "total": 1, "report": parse_report})

    all_tags, fallback, rejected, promos = {}, [], [], []
    n_legal = n_strong = 0
    for n, ep in enumerate(episodes.values(), 1):
        tags = tag_with_ai(ep, llm) if llm else None
        if tags is None:
            if llm:
                fallback.append(ep["episode"])
            tags = tag_with_keywords(ep)
        all_tags[ep["episode"]] = tags
        shortlist, legal, strong = search_episode(ep, tags, cfg)
        n_legal, n_strong = n_legal + legal, n_strong + strong
        promos += shortlist
        progress({"stage": "tag", "done": n, "total": len(episodes), "episode": ep["episode"],
                  "fallback": list(fallback), "failed_calls": llm.stats["failed"] if llm else 0})

    progress({"stage": "trailers", "done": 0, "total": 1})
    if llm:
        promos += build_trailers(episodes, find_moments(episodes, all_tags, cfg), llm, cfg, rejected)
    progress({"stage": "trailers", "done": 1, "total": 1})

    if llm and promos:
        judge(episodes, promos, llm, cfg, progress)

    progress({"stage": "audit", "done": 0, "total": 1})
    checked = []
    for p in promos:
        j = p.pop("_judge", None)
        p["ai_judged"] = j is not None
        if j:
            marks = [j["hook"], j["cliffhanger"], j["standalone"]]
            p.update(retell=j["retell"], confusion=j["confusion"], angle=j["angle"],
                     scored_by="AI judge")
        else:
            marks = [1 + 3 * max(1, p["hook_tag"] or 1), 1 + 3 * max(1, p["cliff_tag"] or 1), 5.0]
            p.update(retell="", confusion="", angle=NOT_JUDGED,
                     scored_by="estimate from line tags (not AI-judged)")
        p["hook_score"], p["cliffhanger_score"], p["standalone_score"] = marks
        p["score"] = final_score(*marks, cfg)
        p["seconds"] = round(sum(s["seconds"] for s in p["segments"]), 2)
        failures, warnings = rules.audit(p, episodes, cfg)
        if j and j["standalone"] <= cfg.low_standalone:
            warnings.append(f"low standalone: a stranger may not follow it ({j['standalone']:.0f}/10)")
        if j and j["confusion"] and j["standalone"] <= cfg.low_standalone + 1:
            warnings.append(f"context dependent: {j['confusion'][:120]}")
        if not j and llm:
            warnings.append("not AI-judged: the judge gave no valid answer, so the score is an estimate")
        p["warnings"] = warnings
        if failures:
            rejected.append({"stage": "audit", "reason": "; ".join(failures),
                             "proposal": {k: p[k] for k in ("type", "segments", "hook", "cliffhanger")}})
        else:
            checked.append(p)

    # one final de-duplication: a trailer or single that mostly repeats a better promo is dropped
    checked.sort(key=lambda p: (-p["ai_judged"], -p["score"]))
    kept = []
    for p in checked:
        dup = next((k for k in kept if rules.overlap(p, k) > cfg.final_max_overlap), None)
        if dup:
            rejected.append({"stage": "duplicate", "reason": "mostly repeats a higher-scoring promo",
                             "proposal": {k: p[k] for k in ("type", "segments", "hook")}})
        else:
            kept.append(p)
    progress({"stage": "audit", "done": 1, "total": 1})

    plan = posting_order(kept, cfg)
    log = {"mode": f"AI: {llm.model}" if llm else "keyword baseline (no AI)",
           "provider": llm.base_url if llm else None,
           "episodes": len(episodes), "beats": parse_report["beats"],
           "show_seconds": round(sum(e["duration"] for e in episodes.values()), 1),
           "legal_clips_considered": n_legal, "clips_with_strong_open_and_close": n_strong,
           "shortlisted": len([p for p in promos if p["type"] == "single"]),
           "trailers_proposed": len([p for p in promos if p["type"] == "trailer"]),
           "rejected_by_audit": sum(r["stage"] == "audit" for r in rejected),
           "promos": len(plan), "ai_judged": sum(p["ai_judged"] for p in plan),
           "episodes_with_no_promo": [e for e in episodes
                                      if not any(s["episode"] == e for p in plan for s in p["segments"])],
           "episodes_tagged_by_keywords_after_ai_failed": fallback,
           "parse": {k: v for k, v in parse_report.items()},
           "settings": cfg.as_dict(),
           "wall_seconds": round(time.time() - t0, 1), "llm": dict(llm.stats) if llm else None}
    return {"promos": plan, "rejected": rejected, "log": log, "episodes": episodes, "tags": all_tags}


def _mark(v):
    """A line tag must be a whole number 0-3."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(min(3, max(0, round(f)))) if math.isfinite(f) else None


def _score(v):
    """A judge mark must be a number 1-10."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return float(min(10, max(1, round(f)))) if math.isfinite(f) else None


def _clip_key(p):
    s = p["segments"][0]
    return f"e{s['episode']:02d}_{int(s['start'] * 10)}"


def _short(b):
    name = " ".join(w.capitalize() for w in b.speaker.split(" (")[0].split())
    return (f"{name}: \"{b.text}\"" if b.kind == "SAY" else f"[Action] {b.text}")[:160]


# ---------------------------------------------------------------- outputs and quality report
COLUMNS = ["rank", "score", "type", "segments", "seconds", "angle", "hook_score", "cliffhanger_score",
           "standalone_score", "scored_by", "hook", "cliffhanger", "why", "post_reason",
           "stranger_retell", "stranger_confusion", "warnings", "search_score"]


def seg_label(p):
    return "; ".join(f"ep{s['episode']:02d} {s['start_ts']}-{s['end_ts']}" for s in p["segments"])


def rows(promos):
    for p in promos:
        yield {"rank": p["rank"], "score": p["score"], "type": p["type"], "segments": seg_label(p),
               "seconds": p["seconds"], "angle": p["angle"], "hook_score": p["hook_score"],
               "cliffhanger_score": p["cliffhanger_score"], "standalone_score": p["standalone_score"],
               "scored_by": p["scored_by"], "hook": p["hook"], "cliffhanger": p["cliffhanger"],
               "why": p["why"], "post_reason": p["post_reason"], "stranger_retell": p["retell"],
               "stranger_confusion": p["confusion"], "warnings": " | ".join(p["warnings"]),
               "search_score": p["search_score"]}


def to_csv(promos):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows(promos))
    return buf.getvalue()


def quality_report(result):
    """Plain facts about this run, written by code: the 'where does it pick badly' page."""
    promos, log = result["promos"], result["log"]
    n = len(promos)
    judged = [p for p in promos if p["ai_judged"]]
    warn = Counter(w.split(":")[0] for p in promos for w in p["warnings"])
    rejected = Counter(r["stage"] for r in result["rejected"])
    pr = log["parse"]
    out = ["# Quality report", "", f"Mode: {log['mode']}", "",
           "## Input", "",
           f"- Files read: {pr['files_read']}; episodes parsed: {pr['parsed']}; "
           f"screenplay blocks: {pr['blocks']}; beats kept: {pr['beats']}",
           f"- Unusable files: {pr['unusable'] or 'none'}; duplicates: {pr['duplicates'] or 'none'}; "
           f"parse warnings: {pr['warnings'] or 'none'}",
           "", "## Search", "",
           f"- Show length: {log['show_seconds'] / 60:.0f} minutes",
           f"- Legal 20-60 s clips considered: {log['legal_clips_considered']:,}",
           f"- Clips with a strong opening and a strong ending: {log['clips_with_strong_open_and_close']:,}",
           f"- Shortlisted single clips: {log['shortlisted']}; trailers proposed: {log['trailers_proposed']}",
           f"- Rejected by the audit (broke a rule of the brief): {rejected.get('audit', 0)}; "
           f"dropped as duplicates: {rejected.get('duplicate', 0)}; trailer proposals rejected: "
           f"{rejected.get('trailer', 0)}",
           f"- Promos in the final list: {n}",
           f"- Episodes with no promo: {log['episodes_with_no_promo'] or 'none'}",
           "", "## Rule checks", "",
           f"- Every promo in the final list passed the mandatory checks (20-60 s, hook line in the first "
           f"3 s, no spoken line cut, nothing after the cliffhanger, valid timestamps): {n}/{n}",
           "- Quality warnings (promo kept, editor should look):"]
    out += [f"    - {k}: {v}" for k, v in warn.most_common()] or ["    - none"]
    out += ["", "## Scores", "",
            f"- Judged by the AI stranger: {len(judged)}/{n}"
            + ("" if len(judged) == n else " (the rest carry an estimate from line tags and are ranked after the judged ones)"),
            f"- Score 70 or more: {sum(p['score'] >= 70 for p in promos)}; 50 to 70: "
            f"{sum(50 <= p['score'] < 70 for p in promos)}; under 50: {sum(p['score'] < 50 for p in promos)}",
            f"- Angle mix: {dict(Counter(p['angle'] for p in promos).most_common())}"]
    singles = [p for p in judged if p["type"] == "single"]
    agree = _rank_agreement([p["search_score"] for p in singles], [p["score"] for p in singles])
    if agree is not None:
        out.append(f"- Rank agreement between the code's search score and the AI judge (Spearman, -1 to 1): "
                   f"{agree}. Near 0 means the line tags and the stranger disagree on what is good.")
    weak = [p for p in judged if p["standalone_score"] <= DEFAULT.low_standalone]
    out += ["", f"## Promos a stranger may not follow (standalone {DEFAULT.low_standalone:.0f} or less): {len(weak)}", ""]
    out += [f"- {seg_label(p)}: {p['confusion'] or 'no reason given'}" for p in weak[:15]] or ["- none"]
    out += ["", "## The five lowest-scoring promos", ""]
    out += [f"- score {p['score']} | {seg_label(p)} | hook {p['hook_score']:.0f}, cliffhanger "
            f"{p['cliffhanger_score']:.0f}, standalone {p['standalone_score']:.0f} | {p['scored_by']}"
            for p in sorted(promos, key=lambda p: p["score"])[:5]] or ["- none"]
    out += ["", "## Rejected by the audit", ""]
    out += [f"- {r['reason']}" for r in result["rejected"] if r["stage"] == "audit"][:15] or ["- none"]
    out += ["", "## Things that went wrong in this run", "",
            f"- Episodes where the AI tagger failed and keyword tags were used: "
            f"{log['episodes_tagged_by_keywords_after_ai_failed'] or 'none'}"]
    if log["llm"]:
        s = log["llm"]
        out += [f"- Model calls that failed after all retries: {s['failed']}; retries: {s.get('retries', 0)}; "
                f"invalid JSON answers: {s['bad_json']}",
                "", "## Cost of this run", "",
                f"- Model: {log['mode']} at {log['provider']}",
                f"- Model calls: {s['calls'] + s['cached']} ({s['calls']} sent, {s['cached']} reused from cache)",
                f"- Tokens: {s['input_tokens']:,} in, {s['output_tokens']:,} out",
                f"- Time in model calls: {s['seconds']:.0f} s; whole run: {log['wall_seconds']:.0f} s"]
    else:
        out += [f"- Whole run: {log['wall_seconds']:.1f} s, no model calls"]
    return "\n".join(out) + "\n"


def _rank_agreement(xs, ys):
    """Spearman rank correlation, -1..1 (ties get their average rank)."""
    if len(xs) < 3:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r, i = [0.0] * len(v), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    a, b = ranks(xs), ranks(ys)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / den, 2) if den else None


def save(result, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    def dump(name, obj):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    dump("promos_ranked.json", result["promos"])
    dump("rejected.json", result["rejected"])
    dump("run_log.json", result["log"])
    dump("line_tags.json", {f"episode {e}": {k: {"hook": v[0], "cliff": v[1]} for k, v in t.items() if max(v)}
                            for e, t in result["tags"].items()})
    with open(os.path.join(out_dir, "promos_ranked.csv"), "w", encoding="utf-8", newline="") as f:
        f.write(to_csv(result["promos"]))
    with open(os.path.join(out_dir, "quality_report.md"), "w", encoding="utf-8") as f:
        f.write(quality_report(result))


def outputs_zip(result, llm_cache_dir=None):
    """Every output file of a run (plus the raw model calls, if any) as the bytes of one .zip."""
    import tempfile, zipfile
    with tempfile.TemporaryDirectory() as tmp:
        save(result, tmp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(tmp)):
                z.write(os.path.join(tmp, name), f"outputs/{name}")
            if llm_cache_dir and os.path.isdir(llm_cache_dir):
                for name in sorted(os.listdir(llm_cache_dir)):
                    z.write(os.path.join(llm_cache_dir, name), f"outputs/llm_calls/{name}")
        return buf.getvalue()
