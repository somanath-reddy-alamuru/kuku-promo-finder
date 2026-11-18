import os, tempfile, unittest

import eval as ev


def p(rank, ep, s, e):
    return {"rank": rank, "score": 50, "segments": [{"episode": ep, "start": s, "end": e,
                                                     "start_ts": "", "end_ts": ""}]}


class EvalTests(unittest.TestCase):
    def test_iou(self):
        self.assertAlmostEqual(ev.iou((1, 0, 30), (1, 10, 40)), 0.5)
        self.assertEqual(ev.iou((1, 0, 30), (2, 0, 30)), 0.0)

    def test_one_to_one(self):
        gold = [{"id": "G1", "episode": 1, "start": 0, "end": 30, "note": ""},
                {"id": "G2", "episode": 1, "start": 2, "end": 32, "note": ""}]
        r = ev.score_run([p(1, 1, 0, 30)], gold, 0.5)       # one promo, two near-identical picks
        self.assertEqual((r["matched"], r["precision"], r["recall"]), (1, 1.0, 0.5))
        self.assertAlmostEqual(r["f1"], 0.667, places=3)

    def test_other_episodes_not_counted(self):
        gold = [{"id": "G1", "episode": 1, "start": 0, "end": 30, "note": ""}]
        r = ev.score_run([p(1, 1, 0, 30), p(2, 9, 0, 30)], gold, 0.5)
        self.assertEqual((r["tool_promos_in_gold_episodes"], r["precision"]), (1, 1.0))

    def test_gold_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "g.csv")
            open(path, "w").write("# comment\nepisode,start,end,note\n1,00:46.80,01:10.40,x\n")
            g = ev.read_gold(path)
            self.assertEqual((g[0]["start"], g[0]["end"]), (46.8, 70.4))
            open(path, "w").write(open(os.path.join(os.path.dirname(ev.__file__), "gold_template.csv")).read())
            self.assertEqual(ev.read_gold(path), [])
