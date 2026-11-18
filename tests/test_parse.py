import glob, os, re, unittest

from promo_finder.parse import fmt, parse_episode, to_seconds, transcript
from tests.helpers import PACK, episode, script


class TimeTests(unittest.TestCase):
    def test_to_seconds(self):
        self.assertEqual(to_seconds(None, "01", "05.50"), 65.5)
        self.assertEqual(to_seconds("1", "00", "00"), 3600)

    def test_fmt_round_trip(self):
        self.assertEqual(fmt(65.5), "01:05.50")
        self.assertEqual(fmt(0), "00:00.00")
        self.assertEqual(fmt(59.999), "01:00.00")


class ParseTests(unittest.TestCase):
    def test_kinds_speakers_and_ids(self):
        ep = episode([("CAMERA", 0, 3, "Wide shot."), ("ACTION", 0.5, 2, "She enters."),
                      ("LADY JULIANA (Heartbroken)", 1, 2.5, '"Why?"'), ("SOUND FX", 2, 3, "Drone.")])
        self.assertEqual([b.kind for b in ep["beats"]], ["CAM", "SEE", "SAY", "SFX"])
        say = ep["beats"][2]
        self.assertEqual((say.speaker, say.text, say.start, say.end), ("LADY JULIANA (Heartbroken)", "Why?", 1.0, 2.5))
        self.assertEqual([b.id for b in ep["beats"]], ["L001", "L002", "L003", "L004"])
        self.assertEqual(ep["beats"][0].scene, "INT. ROOM - DAY")
        self.assertEqual(ep["duration"], 3.0)
        self.assertEqual(ep["warnings"], [])

    def test_missing_timestamp_is_reported_not_lost_silently(self):
        text = script([("CAMERA", 0, 3, "Shot.")]).replace("--- SCRIPT END ---",
                                                          "**BOB:**\nno times here\n\n--- SCRIPT END ---")
        ep = parse_episode(text, "episode-001.txt")
        self.assertEqual(ep["blocks"], 2)
        self.assertEqual(len(ep["beats"]), 1)
        self.assertTrue(any("BOB" in w and "no 'Start" in w for w in ep["warnings"]))

    def test_end_before_start(self):
        ep = episode([("CAMERA", 5, 3, "Shot.")])
        self.assertEqual(ep["beats"][0].end, 5.0)
        self.assertTrue(any("ends before it starts" in w for w in ep["warnings"]))

    def test_unsupported_format(self):
        ep = parse_episode("just some prose, no blocks", "notes.txt")
        self.assertEqual(ep["beats"], [])
        self.assertTrue(any("unsupported format" in w for w in ep["warnings"]))

    def test_episode_number_from_header_beats_file_name(self):
        ep = parse_episode(script([("CAMERA", 0, 3, "x")], episode=7), "episode-009.txt")
        self.assertEqual(ep["episode"], 7)
        self.assertTrue(any("header" in w for w in ep["warnings"]))

    def test_transcript_marks_first_seconds(self):
        ep = episode([("BOB", 10, 11, '"A"'), ("BOB", 14, 15, '"B"')])
        lines = transcript(ep["beats"], t0=10, mark_first=3).splitlines()
        self.assertTrue(lines[0].startswith(">> L001 | 0.0-1.0"))
        self.assertFalse(lines[1].startswith(">>"))


@unittest.skipUnless(os.path.isdir(PACK), "the confidential input pack is not available")
class RealPackTests(unittest.TestCase):
    def test_all_50_files_parse_with_no_lost_blocks(self):
        files = sorted(glob.glob(os.path.join(PACK, "*.txt")))
        self.assertEqual(len(files), 50)
        numbers, total = set(), 0
        for f in files:
            text = open(f, encoding="utf-8").read()
            ep = parse_episode(text, os.path.basename(f))
            raw = len(re.findall(r"(?m)^\*\*.+?:\*\*\s*$", text))
            self.assertEqual(len(ep["beats"]), raw, f)          # every block became a beat
            self.assertEqual(ep["warnings"], [], f)
            self.assertEqual(len({b.id for b in ep["beats"]}), len(ep["beats"]))
            self.assertTrue(all(0 <= b.start <= b.end for b in ep["beats"]))
            numbers.add(ep["episode"])
            total += len(ep["beats"])
        self.assertEqual(numbers, set(range(1, 51)))
        self.assertEqual(total, 3727)
