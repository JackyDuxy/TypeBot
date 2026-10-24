import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402

TEXT = (
    "Hello world, this is a test of the typing engine. It should feel human!\n"
    "Second line with Numbers 12345 and symbols {}[]();\n"
    "    indented line\n"
    "中文测试，看看效果。"
)


def replay(plan):
    buf = []
    for st in plan.steps:
        if st.kind == "backspace":
            buf.pop()
        else:
            buf.append(st.ch)
    return "".join(buf)


class PlanTests(unittest.TestCase):
    def test_total_time_matches_wpm(self):
        for wpm in (20, 60, 150):
            s = engine.TypingSettings(wpm=wpm, typo_rate=0.05, thinking_rate=0.1, seed=1)
            plan = engine.build_plan(TEXT, s)
            total = sum(st.delay for st in plan.steps)
            expected = plan.total_units * 60 / (wpm * 5)
            self.assertAlmostEqual(total, expected, places=6)

    def test_typos_are_corrected(self):
        for seed in range(30):
            s = engine.TypingSettings(typo_rate=0.15, seed=seed)
            plan = engine.build_plan(TEXT, s)
            self.assertEqual(replay(plan), TEXT)
        self.assertTrue(any(st.kind == "backspace" for st in plan.steps))

    def test_skip_leading_whitespace(self):
        s = engine.TypingSettings(skip_leading_ws=True, typo_rate=0, seed=3)
        plan = engine.build_plan("def f():\n    return 1\n\tx", s)
        self.assertEqual(replay(plan), "def f():\nreturn 1\nx")
        self.assertEqual(plan.steps[-1].src, len(plan.text))

    def test_progress_monotonic_and_complete(self):
        plan = engine.build_plan(TEXT, engine.TypingSettings(typo_rate=0.1, seed=5))
        srcs = [st.src for st in plan.steps]
        self.assertEqual(srcs, sorted(srcs))
        self.assertEqual(srcs[-1], len(TEXT))

    def test_cjk_weight(self):
        self.assertEqual(engine.char_weight("中"), 3.0)
        self.assertEqual(engine.char_weight("，"), 1.0)
        self.assertEqual(engine.char_weight("a"), 1.0)

    def test_no_randomness_is_deterministic_shape(self):
        s = engine.TypingSettings(variability=0, typo_rate=0, thinking_rate=0, punctuation_pause=False)
        plan = engine.build_plan("aaaa", s)
        delays = [st.delay for st in plan.steps]
        self.assertTrue(all(d > 0 for d in delays))


class TypistTests(unittest.TestCase):
    def test_runs_at_target_speed(self):
        text = "the quick brown fox jumps over the lazy dog " * 2
        wpm = 300  # 快一点，测试不必等太久
        plan = engine.build_plan(text, engine.TypingSettings(wpm=wpm, seed=2))
        out, events = [], []
        kb = engine.PreviewKeyboard(lambda st: out.append(st))
        t = engine.Typist(plan, kb, emit=lambda *e: events.append(e), focus_guard=False, esc_stop=False)
        t0 = time.perf_counter()
        t.start()
        t.join(30)
        elapsed = time.perf_counter() - t0
        self.assertEqual(events[-1][:2], ("done", "completed"))
        self.assertEqual(len(out), len(plan.steps))
        self.assertLess(abs(elapsed - plan.est_seconds) / plan.est_seconds, 0.15)

    def test_pause_resume_stop(self):
        plan = engine.build_plan("x" * 200, engine.TypingSettings(wpm=120, seed=1))
        out, events = [], []
        kb = engine.PreviewKeyboard(lambda st: out.append(st))
        t = engine.Typist(plan, kb, emit=lambda *e: events.append(e), focus_guard=False, esc_stop=False)
        t.start()
        time.sleep(0.4)
        t.pause()
        time.sleep(0.2)
        n = len(out)
        time.sleep(0.4)
        self.assertEqual(len(out), n)  # 暂停期间不打字
        t.resume()
        time.sleep(0.4)
        self.assertGreater(len(out), n)
        t.stop()
        t.join(2)
        kinds = [e[0] for e in events]
        self.assertIn("paused", kinds)
        self.assertIn("resumed", kinds)
        self.assertEqual(events[-1][:2], ("done", "stopped"))


if __name__ == "__main__":
    unittest.main()
