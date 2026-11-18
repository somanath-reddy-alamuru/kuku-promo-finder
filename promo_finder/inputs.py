"""Reading input files safely and turning them into parsed episodes, with a report.

Inputs can be a folder, a .zip, a single .txt, or a list of (filename, text or bytes)
pairs (what the web app passes after an upload). ZIP files are read in memory and never
extracted to disk, so a malicious path such as '../../etc/passwd' inside a zip cannot
write anywhere. Size limits stop a "zip bomb" (a tiny zip that expands to gigabytes).
"""
import io, os, zipfile

from .parse import parse_episode

MAX_FILES = 500                    # .txt files accepted from one input
MAX_FILE_BYTES = 2 * 1024 * 1024   # 2 MB per screenplay (the real ones are 5-16 KB)
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB uncompressed in total


class InputError(Exception):
    """The input cannot be used at all (bad zip, too big, no screenplays)."""


def _decode(data):
    if isinstance(data, str):
        return data
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _skip(name):
    base = os.path.basename(name)
    return (not base.lower().endswith(".txt") or base.startswith(".")
            or "__MACOSX" in name.replace("\\", "/").split("/"))


def read_zip(data):
    """data: bytes of a .zip. Returns ([(name, text)], [skipped messages])."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InputError("the uploaded file is not a valid .zip")
    files, skipped, total = [], [], 0
    for info in z.infolist():
        if info.is_dir():
            continue
        if _skip(info.filename):
            if not info.filename.endswith("/") and "__MACOSX" not in info.filename:
                skipped.append(f"{info.filename}: not a .txt screenplay; ignored")
            continue
        if info.file_size > MAX_FILE_BYTES:
            skipped.append(f"{info.filename}: larger than {MAX_FILE_BYTES // 1024} KB; ignored")
            continue
        total += info.file_size
        if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
            raise InputError(f"the zip holds more than {MAX_FILES} files or "
                             f"{MAX_TOTAL_BYTES // 1024 // 1024} MB of text")
        try:
            files.append((os.path.basename(info.filename), _decode(z.read(info))))
        except (zipfile.BadZipFile, RuntimeError, OSError) as e:
            skipped.append(f"{info.filename}: could not be read ({type(e).__name__}); ignored")
    return files, skipped


def read_source(source):
    """Any supported input -> ([(name, text)], [skipped messages]).

    Apply limits across the whole upload, not just inside each individual ZIP. This
    matters when the public app receives several TXT files or multiple ZIP files.
    """
    if isinstance(source, list):
        files, skipped, total = [], [], 0
        for name, data in source:
            name = str(name)
            if name.lower().endswith(".zip"):
                more, more_skipped = read_zip(data if isinstance(data, bytes) else data.encode())
                skipped += more_skipped
                for sub_name, text in more:
                    size = len(text.encode("utf-8"))
                    total += size
                    if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                        raise InputError(f"the upload contains more than {MAX_FILES} screenplays or "
                                         f"{MAX_TOTAL_BYTES // 1024 // 1024} MB of text")
                    files.append((sub_name, text))
            elif _skip(name):
                skipped.append(f"{name}: not a .txt screenplay; ignored")
            else:
                size = len(data.encode("utf-8")) if isinstance(data, str) else len(data)
                if size > MAX_FILE_BYTES:
                    skipped.append(f"{name}: larger than {MAX_FILE_BYTES // 1024} KB; ignored")
                    continue
                total += size
                if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise InputError(f"the upload contains more than {MAX_FILES} screenplays or "
                                     f"{MAX_TOTAL_BYTES // 1024 // 1024} MB of text")
                files.append((os.path.basename(name), _decode(data)))
        return files, skipped

    if os.path.isdir(source):
        files, skipped, total = [], [], 0
        for root, _, names in os.walk(source):
            for name in sorted(names):
                path = os.path.join(root, name)
                if _skip(path):
                    continue
                size = os.path.getsize(path)
                if size > MAX_FILE_BYTES:
                    skipped.append(f"{name}: larger than {MAX_FILE_BYTES // 1024} KB; ignored")
                    continue
                total += size
                if total > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise InputError(f"the input contains more than {MAX_FILES} screenplays or "
                                     f"{MAX_TOTAL_BYTES // 1024 // 1024} MB of text")
                with open(path, "rb") as f:
                    files.append((name, _decode(f.read())))
        return files, skipped

    if not os.path.exists(source):
        raise InputError(f"{source} does not exist")
    size = os.path.getsize(source)
    if size > MAX_TOTAL_BYTES:
        raise InputError(f"the input file exceeds {MAX_TOTAL_BYTES // 1024 // 1024} MB")
    with open(source, "rb") as f:
        data = f.read()
    if source.lower().endswith(".zip") or zipfile.is_zipfile(source):
        return read_zip(data)
    if size > MAX_FILE_BYTES:
        raise InputError(f"the screenplay exceeds {MAX_FILE_BYTES // 1024} KB")
    return [(os.path.basename(source), _decode(data))], []


def parse_all(source):
    """Returns (episodes, report).

    episodes: {episode number: parsed episode}, sorted by number
    report:   files_read, parsed, unusable [(file, why)], duplicates [(file, episode, kept file)],
              skipped [messages], warnings {episode: [messages]}, beats, blocks
    """
    files, skipped = read_source(source)
    if not files:
        raise InputError("no .txt screenplay files found in the input")
    if len(files) > MAX_FILES:
        raise InputError(f"more than {MAX_FILES} files")
    episodes, unusable, duplicates, warnings = {}, [], [], {}
    for name, text in sorted(files):
        ep = parse_episode(text, name)
        if not ep["beats"]:
            unusable.append((name, "; ".join(ep["warnings"])))
            continue
        if ep["episode"] == 0:
            unusable.append((name, "no episode number in the header or the file name"))
            continue
        if ep["episode"] in episodes:
            duplicates.append((name, ep["episode"], episodes[ep["episode"]]["file"]))
            continue
        episodes[ep["episode"]] = ep
        if ep["warnings"]:
            warnings[ep["episode"]] = ep["warnings"]
    episodes = dict(sorted(episodes.items()))
    report = {"files_read": len(files), "parsed": len(episodes), "unusable": unusable,
              "duplicates": duplicates, "skipped": skipped, "warnings": warnings,
              "beats": sum(len(e["beats"]) for e in episodes.values()),
              "blocks": sum(e["blocks"] for e in episodes.values()),
              "kinds": {k: sum(b.kind == k for e in episodes.values() for b in e["beats"])
                        for k in ("CAM", "SEE", "SAY", "SFX")}}
    if not episodes:
        raise InputError("none of the files could be parsed: " +
                         "; ".join(f"{n}: {why}" for n, why in unusable[:5]))
    return episodes, report
