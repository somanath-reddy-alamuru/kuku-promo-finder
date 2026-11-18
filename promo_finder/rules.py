"""Everything that has a right answer lives here, in code: cut points, lengths, overlaps, audits."""
import math

from .config import DEFAULT
from .parse import fmt

EPS = 0.05   # timestamps are given to 1/100 s; treat anything closer than this as "the same moment"


def spoken(ep):
    """Lines a clip can open or close on: dialogue (SAY) and on-screen action (SEE)."""
    return [b for b in ep["beats"] if b.kind in ("SAY", "SEE")]


def cuts_dialogue(beats, t):
    """True if cutting at time t would chop a spoken line in half."""
    return any(b.kind == "SAY" and b.start + EPS < t < b.end - EPS for b in beats)


def make_segment(ep, first, last, cfg=DEFAULT):
    """The clip that opens on beat `first` (the hook) and closes on beat `last` (the cliffhanger).

    The raw clip runs from first.start to last.end. Each edge may move to a nearby camera
    cut so the editor gets a clean cut, but ONLY if the move:
      - stays within cfg.start_snap / cfg.end_snap seconds,
      - does not chop a spoken line, and
      - brings no other spoken line or action into the clip. Otherwise the clip could open
        on something before the hook, or end on a reply that answers the cliffhanger.
    """
    beats, lines = ep["beats"], spoken(ep)
    start, end = first.start, last.end

    cams = [c for c in beats if c.kind == "CAM" and c.start < first.start - EPS
            and first.start - c.start <= cfg.start_snap + EPS and c.end > first.start]
    for cam in sorted(cams, key=lambda c: -c.start):            # nearest cut first
        # Do not pull any other dialogue/action into the opening, including a beat that
        # began before the camera cut but continues across it.
        intruder = any(
            b is not first and b.start < first.start - EPS and b.end > cam.start + EPS
            for b in lines
        )
        if not intruder and not cuts_dialogue(beats, cam.start):
            start = cam.start
            break

    cams = [c for c in beats if c.kind == "CAM" and c.end > last.end + EPS
            and c.end - last.end <= cfg.end_snap + EPS and c.start < last.end]
    for cam in sorted(cams, key=lambda c: c.end):               # nearest cut first
        # Do not extend beyond the cliffhanger if another dialogue/action beat starts
        # after it OR an overlapping beat continues beyond it.
        intruder = any(
            b is not last and b.start < cam.end - EPS and b.end > last.end + EPS
            for b in lines
        )
        if not intruder and not cuts_dialogue(beats, cam.end):
            end = cam.end
            break

    return {"episode": ep["episode"], "start": round(start, 2), "end": round(end, 2),
            "start_ts": fmt(start), "end_ts": fmt(end), "seconds": round(end - start, 2),
            "first_id": first.id, "last_id": last.id}


def legal(ep, seg, cfg=DEFAULT):
    """A single clip is legal if it is 20-60 seconds and does not chop a spoken line."""
    return (cfg.min_len - 1e-6 <= seg["seconds"] <= cfg.max_len + 1e-6
            and not cuts_dialogue(ep["beats"], seg["start"])
            and not cuts_dialogue(ep["beats"], seg["end"]))


def segment_beats(ep, seg):
    """Every beat that lies fully inside the segment."""
    return [b for b in ep["beats"] if b.start >= seg["start"] - 0.01 and b.end <= seg["end"] + 0.01]


def segment_lines(ep, seg):
    """Spoken lines and actions that START inside the segment (what the viewer sees begin)."""
    return [b for b in spoken(ep) if seg["start"] - 0.01 <= b.start < seg["end"] - 0.01]


def scene_changes(ep, seg):
    """How many times the scene heading changes inside the segment."""
    scenes = [b.scene for b in segment_beats(ep, seg)]
    return sum(1 for a, b in zip(scenes, scenes[1:]) if a != b)


def longest_silence(ep, seg):
    """Longest stretch inside the segment where no spoken line or action is under way."""
    spans = sorted((max(b.start, seg["start"]), min(b.end, seg["end"])) for b in spoken(ep)
                   if b.end > seg["start"] and b.start < seg["end"])
    gap, cursor = 0.0, seg["start"]
    for s, e in spans:
        gap = max(gap, s - cursor)
        cursor = max(cursor, e)
    return round(max(gap, seg["end"] - cursor), 2)


def _valid_segment(seg, episodes):
    ep = episodes.get(seg.get("episode"))
    if ep is None:
        return "segment points at an episode that was not loaded"
    s, e = seg.get("start"), seg.get("end")
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (s, e)):
        return "segment has a missing or non-numeric timestamp"
    if s < 0 or e > ep["duration"] + 0.01 or e <= s:
        return f"segment {fmt(max(s, 0))}-{fmt(max(e, 0))} lies outside episode {ep['episode']} or ends before it starts"
    return None


def audit(promo, episodes, cfg=DEFAULT):
    """Independent re-check of a finished promo.

    Returns (failures, warnings). Any failure means the promo breaks the brief and must
    not be ranked. Warnings are kept on the promo for the editor to see.
    """
    failures, warnings = [], []
    segs = promo.get("segments") or []
    if not segs:
        return ["promo has no segments"], warnings
    for s in segs:
        problem = _valid_segment(s, episodes)
        if problem:
            return [problem], warnings

    total = sum(s["end"] - s["start"] for s in segs)
    if not cfg.min_len - 1e-6 <= total <= cfg.max_len + 1e-6:
        failures.append(f"length {total:.1f}s is outside {cfg.min_len:.0f}-{cfg.max_len:.0f}s")

    first, last = segs[0], segs[-1]
    ep_first, ep_last = episodes[first["episode"]], episodes[last["episode"]]
    opening = segment_lines(ep_first, first)
    if not opening or opening[0].start - first["start"] > cfg.hook_window + 1e-6:
        failures.append(f"no spoken line or action in the first {cfg.hook_window:.0f} seconds")
    elif first.get("first_id"):
        hook = next((b for b in opening if b.id == first["first_id"]), None)
        if hook is None or hook.start - first["start"] > cfg.hook_window + 1e-6:
            failures.append(f"the chosen hook line {first['first_id']} is not in the first "
                            f"{cfg.hook_window:.0f} seconds")
        elif opening[0].start < hook.start - EPS:
            failures.append(f"opens on {opening[0].id} before the chosen hook line {hook.id}")

    for s in segs:
        for t in (s["start"], s["end"]):
            if cuts_dialogue(episodes[s["episode"]]["beats"], t):
                failures.append(f"cuts a spoken line in half at episode {s['episode']} {fmt(t)}")

    if last.get("last_id"):
        cliff = next((b for b in ep_last["beats"] if b.id == last["last_id"]), None)
        if cliff is None:
            failures.append(f"chosen cliffhanger line {last['last_id']} is not in the episode")
        elif not (last["start"] - EPS <= cliff.start and cliff.end <= last["end"] + EPS):
            failures.append(f"chosen cliffhanger line {last['last_id']} is not fully inside the final segment")
        else:
            later = [b for b in segment_lines(ep_last, last) if b is not cliff and
                     (b.start > cliff.start + EPS or b.end > cliff.end + EPS)]
            if later:
                failures.append(f"something happens after the chosen cliffhanger ({later[0].id}: "
                                f"{later[0].text[:50]}), so it may be answered inside the clip")

    cam_starts = {round(b.start, 2) for b in ep_first["beats"] if b.kind == "CAM"}
    cam_ends = {round(b.end, 2) for b in ep_last["beats"] if b.kind == "CAM"}
    if round(first["start"], 2) not in cam_starts:
        warnings.append("not on a camera cut: the start is mid-shot")
    if round(last["end"], 2) not in cam_ends:
        warnings.append("not on a camera cut: the end is mid-shot")
    talk = sum(min(b.end, s["end"]) - max(b.start, s["start"]) for s in segs
               for b in episodes[s["episode"]]["beats"]
               if b.kind == "SAY" and b.end > s["start"] and b.start < s["end"])
    if total and talk / total < 0.4:
        warnings.append(f"leans on visuals: only {talk / total:.0%} of the clip has dialogue, the rest is "
                        "action the script only describes")
    if opening and opening[0].kind == "SEE":
        warnings.append("action hook: the hook is an action, so its strength depends on how it is shot")
    return failures, warnings


def overlap(a, b):
    """Share of the shorter promo that is also inside the other one (0..1)."""
    shared = 0.0
    for s in a["segments"]:
        for t in b["segments"]:
            if s["episode"] == t["episode"]:
                shared += max(0.0, min(s["end"], t["end"]) - max(s["start"], t["start"]))
    shortest = min(sum(s["seconds"] for s in a["segments"]), sum(s["seconds"] for s in b["segments"]))
    return shared / shortest if shortest else 0.0
