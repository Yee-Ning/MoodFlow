import unittest

from pydantic import ValidationError

from app.agent.foundation.routing import UnresolvedSegment
from app.agent.foundation.rule_routing import RuleRouter


class RoutingSegmentTests(unittest.TestCase):
    def test_repeated_fragments_keep_distinct_original_offsets(self):
        query = "  讲讲这首歌，然后暂停一下，再讲讲这首歌  "
        result = RuleRouter().route(query)
        segments = result.unresolved_segments
        self.assertGreaterEqual(len(segments), 2)
        for index, segment in enumerate(segments):
            self.assertEqual(segment.order, index)
            self.assertEqual(query[segment.start:segment.end], segment.text)
        self.assertLess(segments[0].start, segments[-1].start)
        self.assertEqual(result.unresolved_text, "；".join(s.text for s in segments))

    def test_rule_resolved_input_has_no_segments(self):
        self.assertEqual(RuleRouter().route("暂停一下").unresolved_segments, [])

    def test_span_length_mismatch_rejected(self):
        with self.assertRaises(ValidationError):
            UnresolvedSegment(text="你好", start=1, end=2, order=0)
