import unittest

from promo_finder import rules
from promo_finder.config import Settings
from tests.helpers import by_text, episode

CAM = "CAMERA"


def long_scene(extra_after=None, extra_before=None):
    """A 30-second exchange. Camera shots start 0.5 s before the hook and end 1 s after the cliffhanger."""
    blocks = [(CAM, 9.5, 20, "Shot A."), ("ANNA (Furious)", 10, 12, '"You betrayed me!"'),
              ("BEN", 15, 17, '"I had no choice."'), (CAM, 20, 41, "Shot B."),
              ("ANNA", 30, 32, '"Then who ordered it?"'), ("ANNA", 38, 40, '"Who was it?"')]
    if extra_after:
        blocks.append(extra_after)
    if extra_before:
        blocks.append(extra_before)
    return episode(blocks)


class SegmentTests(unittest.TestCase):
    def test_snaps_to_camera_cuts_when_safe(self):
        ep = long_scene()
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        self.assertEqual((seg["start"], seg["end"]), (9.5, 41.0))

    def test_end_does_not_swallow_the_answer_to_the_cliffhanger(self):
        # the old code extended the end to the camera cut at 41.0 and so included this reply
        ep = long_scene(extra_after=("BEN", 40.2, 40.9, '"Your father."'))
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        self.assertEqual(seg["end"], 40.0)
        self.assertNotIn("Your father", [b.text for b in rules.segment_lines(ep, seg)])
        failures, _ = rules.audit({"segments": [seg]}, {1: ep})
        self.assertEqual(failures, [])

    def test_start_does_not_open_before_the_hook(self):
        ep = long_scene(extra_before=("ACTION", 9.6, 9.9, "Ben flinches."))
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        self.assertEqual(seg["start"], 10.0)

    def test_start_does_not_snap_across_an_action_already_in_progress(self):
        ep = long_scene(extra_before=("ACTION", 9.0, 10.5, "Ben is already reaching for the door."))
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        self.assertEqual(seg["start"], 10.0)

    def test_end_does_not_snap_past_an_action_already_in_progress(self):
        ep = long_scene(extra_after=("ACTION", 39.5, 40.8, "Ben raises the letter."))
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        self.assertEqual(seg["end"], 40.0)

    def test_no_snap_beyond_the_limit(self):
        ep = episode([(CAM, 8, 20, "A."), ("ANNA", 10, 12, '"x!"'), ("ANNA", 38, 40, '"y?"'), (CAM, 20, 43, "B.")])
        seg = rules.make_segment(ep, by_text(ep, "x!"), by_text(ep, "y?"))
        self.assertEqual((seg["start"], seg["end"]), (10.0, 40.0))   # 2 s and 3 s are too far

    def test_cuts_dialogue(self):
        ep = long_scene()
        self.assertTrue(rules.cuts_dialogue(ep["beats"], 11.0))
        self.assertFalse(rules.cuts_dialogue(ep["beats"], 12.0))
        self.assertFalse(rules.cuts_dialogue(ep["beats"], 13.0))


class LengthTests(unittest.TestCase):
    def seg(self, seconds):
        ep = episode([("ANNA", 0, 1, '"a"'), ("ANNA", seconds - 1, seconds, '"b"')])
        return ep, rules.make_segment(ep, ep["beats"][0], ep["beats"][1])

    def test_boundaries(self):
        for seconds, ok in ((19.9, False), (20.0, True), (60.0, True), (60.1, False)):
            ep, seg = self.seg(seconds)
            self.assertEqual(rules.legal(ep, seg), ok, seconds)


class AuditTests(unittest.TestCase):
    def test_reply_inside_clip_is_a_failure(self):
        ep = long_scene(extra_after=("BEN", 40.2, 40.9, '"Your father."'))
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        seg = dict(seg, end=41.0, seconds=31.0)              # force the old, wrong boundary
        failures, _ = rules.audit({"segments": [seg]}, {1: ep})
        self.assertTrue(any("after the chosen cliffhanger" in f for f in failures))

    def test_hook_must_be_first_and_early(self):
        ep = long_scene()
        seg = rules.make_segment(ep, by_text(ep, "no choice"), by_text(ep, "Who was it"))
        seg = dict(seg, start=10.0, seconds=seg["end"] - 10.0)   # opens 5 s before the chosen hook
        failures, _ = rules.audit({"segments": [seg]}, {1: ep})
        self.assertTrue(any("hook" in f for f in failures))

    def test_bad_timestamps_and_length(self):
        ep = long_scene()
        bad = {"episode": 1, "start": 5.0, "end": 999.0, "seconds": 994, "start_ts": "", "end_ts": ""}
        self.assertTrue(rules.audit({"segments": [bad]}, {1: ep})[0])
        self.assertTrue(rules.audit({"segments": [dict(bad, episode=9)]}, {1: ep})[0])
        self.assertTrue(rules.audit({"segments": [dict(bad, start=float("nan"))]}, {1: ep})[0])
        self.assertTrue(rules.audit({"segments": []}, {1: ep})[0])
        short = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "no choice"))
        self.assertTrue(any("outside" in f for f in rules.audit({"segments": [short]}, {1: ep})[0]))

    def test_dialogue_cut_is_a_failure(self):
        ep = long_scene()
        seg = rules.make_segment(ep, by_text(ep, "betrayed"), by_text(ep, "Who was it"))
        failures, _ = rules.audit({"segments": [dict(seg, end=39.0)]}, {1: ep})
        self.assertTrue(any("cuts a spoken line" in f for f in failures))

    def test_camera_cut_warning_is_not_a_failure(self):
        ep = episode([("ANNA", 10, 12, '"x!"'), ("ANNA", 38, 40, '"y?"')])
        seg = rules.make_segment(ep, ep["beats"][0], ep["beats"][1])
        failures, warnings = rules.audit({"segments": [seg]}, {1: ep})
        self.assertEqual(failures, [])
        self.assertIn("not on a camera cut: the start is mid-shot", warnings)


class SceneAndSilenceTests(unittest.TestCase):
    def test_counts_every_scene_change(self):
        ep = episode(None, scenes=[("A", 0, 10, [("ANNA", 0, 2, '"x!"')]),
                                   ("B", 10, 20, [("BEN", 11, 12, '"y"')]),
                                   ("A", 20, 30, [("ANNA", 25, 27, '"z?"')])])
        seg = rules.make_segment(ep, ep["beats"][0], ep["beats"][-1])
        self.assertEqual(rules.scene_changes(ep, seg), 2)

    def test_longest_silence(self):
        ep = episode([("ANNA", 0, 2, '"x"'), ("ANNA", 12, 14, '"y"'), ("ANNA", 20, 22, '"z"')])
        seg = rules.make_segment(ep, ep["beats"][0], ep["beats"][-1])
        self.assertEqual(rules.longest_silence(ep, seg), 10.0)


class OverlapTests(unittest.TestCase):
    def test_overlap(self):
        a = {"segments": [{"episode": 1, "start": 0, "end": 30, "seconds": 30}]}
        b = {"segments": [{"episode": 1, "start": 20, "end": 40, "seconds": 20}]}
        c = {"segments": [{"episode": 2, "start": 0, "end": 30, "seconds": 30}]}
        self.assertAlmostEqual(rules.overlap(a, b), 0.5)
        self.assertEqual(rules.overlap(a, c), 0.0)
