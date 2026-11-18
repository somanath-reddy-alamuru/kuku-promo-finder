"""Every tunable number in one place, each with the reason it was chosen.

None of these were fitted to real promo performance data (we have none). They are
judgement calls, kept here so they are easy to see, question and change.
"""
from dataclasses import dataclass, asdict


@dataclass
class Settings:
    # ---- what a legal promo is (from the brief) ----
    min_len: float = 20.0          # brief: 20-60 second clips
    max_len: float = 60.0
    hook_window: float = 3.0       # brief: the hook must land in the first 3 seconds

    # ---- clean cut points ----
    start_snap: float = 1.0        # move the start back to a camera cut at most this far (s)
    end_snap: float = 1.5          # move the end forward to a camera cut at most this far (s)
    # Never if it would bring another spoken line or action into the clip (see rules.make_segment).

    # ---- line tags (0-3) ----
    strong: int = 2                # a line needs hook >= 2 to open a clip, cliff >= 2 to close one

    # ---- search score ----
    middle_weight: float = 0.5     # the opening and the ending matter most; a lively middle helps,
                                   # but at half weight so it cannot outvote a weak opening or ending
    scene_change_penalty: float = 1.0   # per scene change inside the clip: a jump in place/time
                                        # is the most common way a stranger loses the thread
    dead_air_seconds: float = 8.0  # a gap this long with nobody speaking or acting...
    dead_air_penalty: float = 1.0  # ...costs this much: in a 20-60 s promo it is a scroll-away moment
    per_episode: int = 2           # shortlist size per episode (keeps one episode from flooding the list)
    max_overlap: float = 0.3       # two shortlisted clips may share at most 30% of the shorter one

    # ---- final score (weighted geometric mean of the stranger's three marks) ----
    w_hook: float = 1.0            # equal weights: the brief lists hook, cliffhanger and
    w_cliffhanger: float = 1.0     # standalone as three requirements without ranking them
    w_standalone: float = 1.0

    # ---- posting order ----
    repeat_angle_penalty: float = 10.0    # points off for the same angle as the previous post
    repeat_episode_penalty: float = 10.0  # points off for an episode used in the recent posts
    recent_posts: int = 3

    # ---- de-duplication across the final list ----
    final_max_overlap: float = 0.5

    # ---- quality warnings ----
    low_standalone: float = 4.0    # standalone at or below this = "a stranger may not follow"

    # ---- model calls ----
    judge_batch: int = 1           # clips per judge call. 1 = every clip judged on its own
    trailers: int = 3

    def as_dict(self):
        return asdict(self)


DEFAULT = Settings()
