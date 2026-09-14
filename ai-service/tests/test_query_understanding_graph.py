import asyncio
import unittest

from langchain_core.messages import HumanMessage

from app.rag.query_understanding import (
    QueryUnderstandingStatus,
    create_query_understanding_graph,
    create_query_understanding_state,
)


class QueryUnderstandingGraphTests(unittest.TestCase):
    def test_graph_builds_plan_through_parallel_expansion_and_filter_nodes(self) -> None:
        query = "周杰伦 2000 到 2005 年的音乐风格如何变化"
        graph = create_query_understanding_graph()

        output = asyncio.run(
            graph.ainvoke(
                create_query_understanding_state(
                    query, messages=[HumanMessage(content=query)]
                )
            )
        )

        self.assertEqual(set(output), {"result"})
        result = output["result"]
        self.assertEqual(result.status, QueryUnderstandingStatus.READY)
        self.assertIn("artist:jay_chou", result.analysis.constraints.entity_ids)
        self.assertGreater(len(result.search_plan.queries), 1)
        self.assertIsNotNone(result.search_plan.metadata_filter.root)

    def test_graph_stops_before_analysis_when_entity_is_ambiguous(self) -> None:
        graph = create_query_understanding_graph()

        output = asyncio.run(
            graph.ainvoke(create_query_understanding_state("七里香值得了解吗"))
        )

        result = output["result"]
        self.assertEqual(result.status, QueryUnderstandingStatus.NEEDS_INPUT)
        self.assertIsNone(result.search_plan)

    def test_graph_exposes_expected_query_understanding_stages(self) -> None:
        graph = create_query_understanding_graph().get_graph()

        self.assertEqual(
            set(graph.nodes),
            {
                "__start__",
                "resolve_context",
                "request_context",
                "resolve_entities",
                "request_entity",
                "analyze_query",
                "expand_query",
                "build_filter",
                "plan_search",
                "__end__",
            },
        )


if __name__ == "__main__":
    unittest.main()
