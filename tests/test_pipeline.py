import csv, io, json, os, tempfile, unittest, zipfile

from promo_finder import pipeline
from promo_finder.config import Settings
from tests.helpers import PACK, episode, script


class FakeLLM:
    """Stands in for a model inside the process. Answers are scripted, NOT real AI output."""
    model, base_url = "fake", "fake://"

    def __init__(self, answers):
        self.answers = answers          # function(system, user, tag) -> dict | None
        self.stats = {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0,
                      "bad_json": 0, "failed": 0, "retries": 0, "seconds": 0.0, "errors": []}

    def ask_json(self, system, user, tag=""):
        self.stats["calls"] += 1
        return self.answers(system, user, tag)


def drama(ep_no=1, ending='"Fine."', ending_sfx="Soft music."):
    blocks = [("CAMERA", 0, 70, "Shot."), ("ANNA (Outraged, Accusing)", 1, 3, '"You betrayed me!"'),
              ("BEN", 6, 8, '"I did what I had to."'), ("ANNA", 12, 14, '"Who paid you?"'),
              ("BEN (Shocked)", 20, 22, '"How do you know about that...?"'), ("BEN", 26, 28, ending),
              ("SOUND FX", 66, 70, ending_sfx)]
    return episode(blocks, ep_no)


class KeywordTagTests(unittest.TestCase):
    def test_neutral_last_line_is_not_upgraded_to_strong(self):
        ep = drama(ending='"Fine."', ending_sfx="Ominous drone swells to a cliffhanger peak.")
        last = [b for b in ep["beats"] if b.kind in ("SAY", "SEE")][-1]
        self.assertEqual(pipeline.tag_with_keywords(ep)[last.id][1], 1)       # 0 + 1, still weak

    def test_no_writer_marker_means_no_bonus(self):
        ep = drama(ending='"Fine."', ending_sfx="Soft music.")
        last = [b for b in ep["beats"] if b.kind in ("SAY", "SEE")][-1]
        self.assertEqual(pipeline.tag_with_keywords(ep)[last.id][1], 0)

    def test_marker_lifts_a_line_with_some_evidence(self):
        ep = drama(ending='"Who are you?"', ending_sfx="Ominous sting.")
        last = [b for b in ep["beats"] if b.kind in ("SAY", "SEE")][-1]
        self.assertEqual(pipeline.tag_with_keywords(ep)[last.id][1], 3)       # ? + who are you, +1, capped


class SearchTests(unittest.TestCase):
    def test_shortlist_respects_limits(self):
        ep = drama()
        tags = {b.id: (3, 3) for b in ep["beats"] if b.kind in ("SAY", "SEE")}
        cfg = Settings(per_episode=2)
        shortlist, legal, strong = pipeline.search_episode(ep, tags, cfg)
        self.assertLessEqual(len(shortlist), 2)
        self.assertGreater(legal, 0)
        for p in shortlist:
            self.assertTrue(20 <= p["segments"][0]["seconds"] <= 60)
        if len(shortlist) == 2:
            from promo_finder.rules import overlap
            self.assertLessEqual(overlap(*shortlist), cfg.max_overlap)

    def test_weak_lines_give_no_promo(self):
        ep = drama()
        shortlist, legal, strong = pipeline.search_episode(ep, {}, Settings())
        self.assertEqual((shortlist, strong), ([], 0))


class ScoreTests(unittest.TestCase):
    def test_geometric_mean(self):
        self.assertEqual(pipeline.final_score(7, 7, 7), 70.0)
        self.assertEqual(pipeline.final_score(9, 9, 2), 54.5)
        self.assertEqual(pipeline.final_score(10, 7, 5), 70.5)

    def test_weights(self):
        self.assertGreater(pipeline.final_score(10, 5, 5, Settings(w_hook=2)), pipeline.final_score(10, 5, 5))

    def test_valid_judgement(self):
        ok = pipeline.valid_judgement({"hook": "8", "cliffhanger": 7.4, "standalone": 12, "angle": "Revenge"})
        self.assertEqual((ok["hook"], ok["cliffhanger"], ok["standalone"], ok["angle"]), (8, 7, 10, "revenge"))
        self.assertIsNone(pipeline.valid_judgement({"hook": "great", "cliffhanger": 5, "standalone": 5}))
        self.assertIsNone(pipeline.valid_judgement({"hook": 5, "cliffhanger": 5}))
        self.assertIsNone(pipeline.valid_judgement("not a dict"))
        self.assertEqual(pipeline.valid_judgement({"hook": 5, "cliffhanger": 5, "standalone": 5,
                                                   "angle": "nonsense"})["angle"], "other")


def promo(ep_no, score, angle, judged=True):
    return {"segments": [{"episode": ep_no, "start": 0, "end": 30, "seconds": 30}], "score": score,
            "angle": angle, "ai_judged": judged}


class PostingOrderTests(unittest.TestCase):
    def test_mixes_angles_and_episodes(self):
        plan = pipeline.posting_order([promo(1, 90, "betrayal"), promo(2, 89, "betrayal"),
                                       promo(3, 85, "revenge")])
        self.assertEqual([p["segments"][0]["episode"] for p in plan], [1, 3, 2])
        self.assertIn("Moved ahead", plan[1]["post_reason"])

    def test_same_episode_not_back_to_back(self):
        plan = pipeline.posting_order([promo(1, 90, "a"), promo(1, 88, "b"), promo(2, 85, "c")])
        self.assertEqual([p["segments"][0]["episode"] for p in plan], [1, 2, 1])

    def test_ai_judged_before_estimates(self):
        plan = pipeline.posting_order([promo(1, 99, "not judged", judged=False), promo(2, 40, "revenge")])
        self.assertEqual(plan[0]["segments"][0]["episode"], 2)
        self.assertIn("not judged by the AI", plan[1]["post_reason"])


def fake_answers(bad_judge_for=()):
    def answer(system, user, tag):
        if tag.startswith("tag_ep"):
            ids = [l.split(" | ")[0] for l in user.splitlines() if " | SAY " in l or " | SEE " in l]
            return {"lines": [{"id": i, "hook": 3, "cliff": 3} for i in ids] + [{"id": "L999", "hook": 3, "cliff": 3}]}
        if tag == "montage":
            return {"montages": [{"moments": ["E01", "E02"], "hook": "h", "cliffhanger": "c", "why": "w"},
                                 {"moments": ["E02", "E01"]}, {"moments": ["E77", "E01"]}]}
        if any(k in tag for k in bad_judge_for):
            return {"clips": [{"clip": "C1", "hook": "very good"}]}
        return {"clips": [{"clip": "C1", "hook": 8, "cliffhanger": 7, "standalone": 6,
                           "retell": "A woman confronts a man.", "confusion": "", "angle": "betrayal"}]}
    return answer


class RunTests(unittest.TestCase):
    def source(self):
        return [("episode-001.txt", script_for(1)), ("episode-002.txt", script_for(2))]

    def test_ai_path_with_fake_model(self):
        llm = FakeLLM(fake_answers())
        result = pipeline.run(self.source(), llm, Settings(trailers=3))
        self.assertTrue(result["promos"])
        for p in result["promos"]:
            self.assertTrue(p["ai_judged"])
            self.assertEqual(p["scored_by"], "AI judge")
        stages = {r["stage"] for r in result["rejected"]}
        self.assertIn("trailer", stages)                      # bad trailer proposals rejected
        self.assertEqual(result["log"]["episodes_tagged_by_keywords_after_ai_failed"], [])

    def test_invalid_judge_answer_is_never_shown_as_ai(self):
        llm = FakeLLM(fake_answers(bad_judge_for=("e01",)))
        result = pipeline.run(self.source(), llm, Settings(trailers=0))
        unjudged = [p for p in result["promos"] if p["segments"][0]["episode"] == 1]
        self.assertTrue(unjudged)
        for p in unjudged:
            self.assertFalse(p["ai_judged"])
            self.assertIn("not AI-judged", p["scored_by"])
            self.assertEqual(p["angle"], pipeline.NOT_JUDGED)
        self.assertEqual(result["promos"][-1]["ai_judged"], False)   # estimates ranked last

    def test_tagger_failure_falls_back_to_keywords(self):
        def answer(system, user, tag):
            return None if tag == "tag_ep002" else fake_answers()(system, user, tag)
        result = pipeline.run(self.source(), FakeLLM(answer), Settings(trailers=0))
        self.assertEqual(result["log"]["episodes_tagged_by_keywords_after_ai_failed"], [2])

    def test_empty_output_still_writes_valid_files(self):
        text = script([("CAMERA", 0, 30, "Calm."), ("ANNA", 1, 2, '"Hello."'), ("BEN", 25, 26, '"Hi."')])
        result = pipeline.run([("episode-001.txt", text)], None)
        self.assertEqual(result["promos"], [])
        with tempfile.TemporaryDirectory() as d:
            pipeline.save(result, d)
            self.assertEqual(json.load(open(os.path.join(d, "promos_ranked.json"))), [])
            self.assertIn("rank", open(os.path.join(d, "promos_ranked.csv")).read())
            self.assertIn("Promos in the final list: 0", open(os.path.join(d, "quality_report.md")).read())

    def test_csv_json_and_zip(self):
        result = pipeline.run(self.source(), FakeLLM(fake_answers()), Settings(trailers=0))
        rows = list(csv.DictReader(io.StringIO(pipeline.to_csv(result["promos"]))))
        self.assertEqual(len(rows), len(result["promos"]))
        self.assertEqual(rows[0]["rank"], "1")
        z = zipfile.ZipFile(io.BytesIO(pipeline.outputs_zip(result)))
        names = set(z.namelist())
        for f in ("promos_ranked.csv", "promos_ranked.json", "quality_report.md", "run_log.json",
                  "rejected.json", "line_tags.json"):
            self.assertIn(f"outputs/{f}", names)
        self.assertEqual(json.loads(z.read("outputs/promos_ranked.json"))[0]["rank"], 1)


def script_for(n):
    return script([("CAMERA", 0, 70, "Shot."), ("ANNA (Outraged)", 1, 3, '"You betrayed me!"'),
                   ("BEN", 6, 8, '"I did what I had to."'), ("ANNA", 12, 14, '"Who paid you?"'),
                   ("BEN (Shocked)", 20, 22, '"How do you know...?"'), ("ANNA", 26, 28, '"Tell me!"'),
                   ("BEN", 33, 35, '"You will regret this?"'), ("ANNA", 50, 52, '"Who are you?"')], n)


@unittest.skipUnless(os.path.isdir(PACK), "the confidential input pack is not available")
class RealPackSmokeTest(unittest.TestCase):
    def test_keyword_run_on_all_50(self):
        result = pipeline.run(PACK, None)
        log = result["log"]
        self.assertEqual((log["episodes"], log["beats"]), (50, 3727))
        self.assertGreater(len(result["promos"]), 0)
        self.assertEqual(log["rejected_by_audit"], 0)
        from promo_finder import rules
        for p in result["promos"]:
            self.assertEqual(rules.audit(p, result["episodes"])[0], [])

    def test_fake_model_run_on_all_50(self):
        # plumbing only: the fake answers are scripted, not real AI judgements
        result = pipeline.run(PACK, FakeLLM(fake_answers()), Settings(trailers=1))
        from promo_finder import rules
        self.assertTrue(result["promos"])
        for p in result["promos"]:
            self.assertEqual(rules.audit(p, result["episodes"])[0], [])
