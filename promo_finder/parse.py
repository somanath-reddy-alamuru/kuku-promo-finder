"""Step 1 (pure code, no AI): turn a screenplay .txt into a list of timestamped beats.

A "beat" is one block of the script: a camera shot, an action, a sound effect or a
line of dialogue. Every beat keeps the exact start/end timestamps from the script,
so nothing downstream ever has to invent a timestamp.

Expected block layout (one blank line between blocks):

    **LADY JULIANA (Heartbroken):**
    Start: (00:35.40) | End: (00:37.70)
    "Why are you holding her hand?"

Anything that does not fit is reported as a warning, never silently dropped.
"""
import re
from dataclasses import dataclass, asdict

# mm:ss.ff, optionally h:mm:ss.ff, optionally in brackets: (00:35.40)
TIME = r"\(?\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2}(?:\.\d+)?)\s*\)?"
HEADER = re.compile(r"^\*\*(.+?):\*\*\s*$")
TIMES = re.compile(rf"^Start:\s*{TIME}\s*\|\s*End:\s*{TIME}\s*$", re.I)
SCENE_END = re.compile(rf"^Scene end:\s*{TIME}", re.I)
KIND = {"CAMERA": "CAM", "ACTION": "SEE", "SOUND FX": "SFX", "SFX": "SFX", "SOUND": "SFX"}
KIND_NAMES = {"CAM": "camera shot", "SEE": "action", "SFX": "sound", "SAY": "dialogue"}


@dataclass
class Beat:
    id: str        # "L001", unique inside one episode
    kind: str      # CAM (camera shot) | SEE (action) | SFX | SAY (dialogue)
    speaker: str   # only for SAY, e.g. "LADY JULIANA (Desperate)"
    start: float   # seconds from episode start
    end: float
    text: str
    scene: str     # scene heading, e.g. "INT. CATHEDRAL – DAY"


def to_seconds(hours, minutes, seconds):
    """('', '01', '05.50') -> 65.5. Hours are optional."""
    return int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)


def fmt(t):
    """seconds -> the script's own mm:ss.ff format, e.g. 65.5 -> '01:05.50'"""
    t = round(t, 2)
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def parse_episode(text, filename=""):
    """Returns a dict:
        episode   int    episode number (from the header, else from the file name, else 0)
        logline   str
        beats     [Beat] sorted by time, with ids L001, L002, ...
        duration  float  seconds (end of the last beat)
        warnings  [str]  everything that did not parse cleanly
        blocks    int    block headers found in the file (to prove none were lost)
    """
    text = text.lstrip("﻿")
    lines = text.splitlines()
    warnings = []

    m_head = re.search(r"EPISODE NUMBER:\s*Episode\s*(\d+)", text, re.I)
    m_file = re.search(r"episode[-_ ]?(\d+)", filename, re.I)
    if m_head and m_file and int(m_head.group(1)) != int(m_file.group(1)):
        warnings.append(f"episode number {m_head.group(1)} in the header but {m_file.group(1)} "
                        f"in the file name; using the header")
    m = m_head or m_file
    episode = int(m.group(1)) if m else 0
    if not m:
        warnings.append("no episode number found in the header or the file name")
    m_log = re.search(r"EPISODE LOGLINE:\s*(.+)", text)
    logline = m_log.group(1).strip() if m_log else ""

    beats, scene, scene_ends, blocks = [], "", [], 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.lower().startswith("scene heading:"):
            scene = line.split(":", 1)[1].strip()
        se = SCENE_END.match(line)
        if se:
            scene_ends.append(to_seconds(*se.groups()))
        head = HEADER.match(line)
        if not head:
            i += 1
            continue
        blocks += 1
        name = head.group(1).strip()
        t = TIMES.match(lines[i + 1].strip()) if i + 1 < len(lines) else None
        if not t:
            warnings.append(f"line {i + 1}: block '{name}' has no 'Start: ... | End: ...' line; skipped")
            i += 1
            continue
        body, j = [], i + 2
        while j < len(lines) and lines[j].strip() and not HEADER.match(lines[j].strip()) \
                and not lines[j].strip().startswith(("SCENE ", "--- SCRIPT", "Scene heading:")):
            body.append(lines[j].strip())
            j += 1
        kind = KIND.get(name.upper(), "SAY")
        start, end = to_seconds(*t.groups()[:3]), to_seconds(*t.groups()[3:])
        if end < start:
            warnings.append(f"line {i + 1}: block '{name}' ends before it starts; end set to start")
            end = start
        if not body:
            warnings.append(f"line {i + 1}: block '{name}' has no text")
        beats.append(Beat("", kind, name if kind == "SAY" else "", round(start, 2), round(end, 2),
                          " ".join(body).strip().strip('"'), scene))
        i = j

    beats.sort(key=lambda b: (b.start, b.end))
    for n, b in enumerate(beats, 1):
        b.id = f"L{n:03d}"
    duration = max((b.end for b in beats), default=0.0)
    if not beats:
        warnings.append("no screenplay blocks found: unsupported format (expected '**NAME:**' "
                        "followed by 'Start: (mm:ss.ff) | End: (mm:ss.ff)')")
    elif scene_ends and abs(max(scene_ends) - duration) > 1.0:
        warnings.append(f"last block ends at {fmt(duration)} but the last scene ends at "
                        f"{fmt(max(scene_ends))}")
    return {"episode": episode, "logline": logline, "beats": beats, "duration": duration,
            "warnings": warnings, "blocks": blocks, "file": filename}


def transcript(beats, t0=0.0, mark_first=None):
    """The compact view of the script that we show the model. Times are plain seconds
    so the model never has to read mm:ss. Lines starting within `mark_first` seconds
    of t0 are prefixed with '>>' (used to show the judge the first 3 seconds)."""
    out = []
    for b in beats:
        who = f"SAY {b.speaker}: \"{b.text}\"" if b.kind == "SAY" else f"{b.kind}: {b.text}"
        flag = ">> " if mark_first is not None and b.start - t0 <= mark_first else ""
        out.append(f"{flag}{b.id} | {b.start - t0:.1f}-{b.end - t0:.1f} | {who}")
    return "\n".join(out)


def beats_as_dicts(beats):
    return [asdict(b) for b in beats]
