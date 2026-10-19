"""Local interaction checks for replay attribution and report rendering."""
import json
import unittest

import test_replayed_spend as support

from harness2.cli.report import _spend_and_fallbacks
from harness2.core.report import Summary


class ReplayAttributionTests(unittest.TestCase):
    def test_rerunning_one_mode_does_not_bill_replay_or_duplicate_other_mode(self):
        case = support.ReplayedSpendTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        prop = case.proposer()
        first = case.run_stream(prop, k=2, modes=("agg", "seq"))
        path = case.summary(k=2)
        summary = json.loads(path.read_text())
        kept_agg = summary["selections"]["agg"]
        del summary["selections"]["seq"]
        path.write_text(json.dumps(summary))
        before = len(prop._calls)
        case.run_stream(prop, k=2, modes=("seq",))
        replies = prop._calls[before:]
        self.assertTrue(replies)
        self.assertEqual(sum(reply.cost_usd for reply in replies), 0.0)
        self.assertEqual(sum(reply.replayed_usd for reply in replies), 2.0)
        self.assertEqual(json.loads(path.read_text())["selections"]["agg"], kept_agg)
        self.assertEqual(_spend_and_fallbacks(path.parents[1], ("agg", "seq"), 2)[1], first)
        before = len(prop._calls)
        case.run_stream(prop, k=2, modes=("seq",))
        self.assertEqual(len(prop._calls), before)
        self.assertEqual(_spend_and_fallbacks(path.parents[1], ("agg", "seq"), 2)[1], first)

    def test_all_ungraded_and_spend_render_together(self):
        result = Summary(mode="agg", k=2, n_ungraded=3, usd=1.25).line()
        self.assertEqual(result, "  parallel k=2: no graded task | ungraded 3 | spend $1.25")
