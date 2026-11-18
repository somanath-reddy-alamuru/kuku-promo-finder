"""Small builders so tests can describe a screenplay in a few lines."""
import os

from promo_finder.parse import parse_episode

PACK = os.environ.get("KUKU_PACK", os.path.join(os.path.dirname(__file__), "..", "..", "input_pack",
                                                "I Married a Broken Soldier. He was a Duke"))


def ts(t):
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def script(blocks, episode=1, scenes=None):
    """blocks: [(label, start, end, text)]. label CAMERA / ACTION / SOUND FX / a speaker name.
    scenes: optional [(heading, start, end, [blocks])] to build several scenes."""
    out = [f"EPISODE NUMBER: Episode {episode}", "EPISODE LOGLINE: test", "", "--- SCRIPT START ---", ""]
    for n, (heading, s0, s1, bl) in enumerate(scenes or [("INT. ROOM - DAY", 0, 0, blocks)], 1):
        out += [f"SCENE {n:02d}", f"Scene heading: {heading}", f"Scene start: {ts(s0)}",
                f"Scene end: {ts(s1 or max(b[2] for b in bl))}", "", "Actions and dialogue:", ""]
        for label, s, e, text in bl:
            out += [f"**{label}:**", f"Start: ({ts(s)}) | End: ({ts(e)})", text, ""]
    out.append("--- SCRIPT END ---")
    return "\n".join(out)


def episode(blocks, episode=1, scenes=None):
    return parse_episode(script(blocks, episode, scenes), f"episode-{episode:03d}.txt")


def by_text(ep, words):
    return next(b for b in ep["beats"] if words in b.text)
