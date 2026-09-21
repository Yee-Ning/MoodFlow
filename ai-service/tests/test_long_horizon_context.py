import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.agent.foundation.long_horizon.models import (
    HandoffDraft,
    HistoryEventType,
    HistoryPointer,
    RolloverReason,
)
from app.agent.foundation.long_horizon.service import LongHorizonContextService, RolloverPolicy
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.streaming import stream_foundation_assistant
from app.schemas.chat import FinalStreamEvent


class Clock:
    def __init__(self):
        self.value = datetime(2026, 9, 21, tzinfo=UTC)

    def __call__(self):
        self.value += timedelta(seconds=1)
        return self.value


class LongHorizonContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteContinuityStore(Path(self.temp.name) / "continuity.sqlite")
        self.clock = Clock()
        self.service = LongHorizonContextService(
            self.store,
            policy=RolloverPolicy(active_context_threshold=100, handoff_token_budget=300),
            now=self.clock,
        )
        self.session1 = self.service.open_task("task:playlist")

    def tearDown(self):
        self.temp.cleanup()

    def event(self, text: str):
        return self.service.record_event(
            task_id=self.session1.task_id, session_id=self.session1.session_id,
            event_type=HistoryEventType.USER_MESSAGE, node="input", source="user",
            payload={"text": text},
        )

    def draft(self, ref, *, summary="progress " * 100, constraints=None):
        return HandoffDraft(
            goal="建立不含现场版的学习歌单", summary=summary,
            constraints=constraints or ["不要 Live Version", "只保留可播放歌曲"],
            constraint_refs={
                (constraints or ["不要 Live Version"])[0]: [
                    HistoryPointer(kind="event", target_id=ref.event_id)
                ]
            },
            preferences=["偏好录音室版本"], decisions=["按相关性排序"],
            current_stage="候选已生成，等待确认", completed_steps=["搜索候选"],
            pending_items=["确认前十首"], next_actions=["让用户确认候选"],
            important_entities=[{"type": "playlist", "name": "学习"}],
            key_message_refs=[HistoryPointer(kind="event", target_id=ref.event_id)],
            evidence_refs=[HistoryPointer(kind="event", target_id=ref.event_id)],
            revalidate_fields=["current_song_id", "player_state", "queue_size"],
            last_known_state={"current_song_id": 1, "player_state": "playing", "queue_size": 5},
        )

    def test_rollover_preserves_constraints_history_lineage_and_revalidates(self):
        constraint = self.event("不要 Live Version")
        result = self.service.rollover(
            self.session1.session_id, self.draft(constraint),
            RolloverReason.TOKEN_THRESHOLD,
        )
        self.assertEqual(result.new_session.previous_session_id, self.session1.session_id)
        self.assertEqual(result.new_session.inherited_handoff_id, result.handoff.handoff_id)
        self.assertIn("不要 Live Version", result.handoff.constraints)
        self.assertEqual(
            result.handoff.constraint_refs["不要 Live Version"][0].target_id,
            constraint.event_id,
        )
        self.assertLessEqual(result.handoff.token_count, result.handoff.token_budget)
        self.assertEqual(self.service.get_history_event(constraint.event_id).payload["text"],
                         "不要 Live Version")

        cold = self.service.cold_start(
            result.new_session.session_id,
            live_state={"current_song_id": 2, "player_state": "paused", "queue_size": 5},
        )
        self.assertEqual(cold.historical_state["current_song_id"], 1)
        self.assertEqual(cold.revalidated_state["current_song_id"], 2)
        self.assertEqual(set(cold.changed_fields), {"current_song_id", "player_state"})
        self.assertEqual(cold.key_events[0].event_id, constraint.event_id)
        pack = self.service.build_active_context(
            RouteKey.CURRENT_SONG, session_id=result.new_session.session_id,
            query="继续分析", system="rules",
            live_state={"current_song_id": 2, "player_state": "paused", "queue_size": 5},
        )
        system_text = str(pack.messages[0].content)
        self.assertIn('"current_song_id":2', system_text)
        self.assertIn('"current_song_id":1', system_text)

    def test_large_candidates_cross_session_only_as_artifact_projection(self):
        constraint = self.event("候选不要现场版")
        songs = [{"song_id": index, "name": "候选" * 100} for index in range(100)]
        artifact = self.service.create_artifact(
            task_id=self.session1.task_id, session_id=self.session1.session_id,
            artifact_type="candidate_song_set", payload=songs,
            projection={"count": 100, "top_ids": [0, 1, 2]},
        )
        draft = self.draft(constraint).model_copy(update={"artifact_refs": [artifact.ref]})
        rollover = self.service.rollover(
            self.session1.session_id, draft, RolloverReason.ARTIFACT_CREATED,
        )
        self.assertEqual(rollover.handoff.artifact_refs, [artifact.ref])
        self.assertNotIn("候选候选候选", rollover.handoff.model_dump_json())
        cold = self.service.cold_start(rollover.new_session.session_id, live_state={})
        self.assertEqual(cold.artifacts[0].projection["count"], 100)
        self.assertEqual(len(self.service.get_artifact(artifact.ref).payload), 100)

    def test_drilldown_reenters_phase1_budget(self):
        original = self.event("精确约束：不要 Live Version。" + "背景" * 10000)
        rollover = self.service.rollover(
            self.session1.session_id, self.draft(original),
            RolloverReason.EXPLICIT_CHECKPOINT,
        )
        rows = self.service.drill_down(
            task_id=self.session1.task_id,
            pointers=[HistoryPointer(kind="event", target_id=original.event_id)],
        )
        pack = self.service.build_active_context(
            RouteKey.GENERAL_CHAT, session_id=rollover.new_session.session_id,
            query="刚才的限制是什么？", system="回答任务", live_state={},
            drilldown=rows,
        )
        self.assertLessEqual(pack.total_tokens, pack.budget)
        trace = pack.trace.for_item("history_drilldown:0")
        self.assertTrue(any(item.stage in {"dropped", "compressed", "final"} for item in trace))
        self.assertTrue(all(item.task_id == self.session1.task_id for item in pack.trace.entries))
        self.assertTrue(all(item.session_id == rollover.new_session.session_id
                            for item in pack.trace.entries))
        self.assertTrue(self.service.query_task_history(
            self.session1.task_id, event_type=HistoryEventType.CONTEXT_TRACE))

    def test_three_rollovers_keep_original_constraint_and_pointer(self):
        original = self.event("永远不要 Live Version")
        current = self.service.rollover(
            self.session1.session_id,
            self.draft(original, constraints=["永远不要 Live Version"]),
            RolloverReason.EXPLICIT_CHECKPOINT,
        )
        first_handoff = current.handoff
        for index in range(2):
            new_event = self.service.record_event(
                task_id=self.session1.task_id, session_id=current.new_session.session_id,
                event_type=HistoryEventType.ASSISTANT_MESSAGE,
                node="final_response", source="assistant", payload={"text": f"阶段 {index}"},
            )
            next_draft = HandoffDraft(
                goal="建立不含现场版的学习歌单", summary=f"阶段 {index}",
                constraints=[], current_stage=f"阶段 {index + 2}",
                next_actions=["继续"],
                key_message_refs=[HistoryPointer(kind="event", target_id=new_event.event_id)],
                revalidate_fields=["current_song_id"], last_known_state={"current_song_id": index + 2},
            )
            current = self.service.rollover(
                current.new_session.session_id, next_draft,
                RolloverReason.EXPLICIT_CHECKPOINT,
            )
        self.assertEqual(len(self.store.sessions_for_task(self.session1.task_id)), 4)
        self.assertIn("永远不要 Live Version", current.handoff.constraints)
        pointer_ids = {
            ref.target_id
            for refs in current.handoff.constraint_refs.values()
            for ref in refs
        }
        self.assertIn(original.event_id, pointer_ids)
        self.assertIsNotNone(self.store.get_handoff(first_handoff.handoff_id))
        self.assertIsNotNone(self.service.get_history_event(original.event_id))

    def test_handoff_budget_drops_summary_before_required_fields(self):
        original = self.event("保留这个约束")
        handoff = self.service.build_handoff(
            self.session1.session_id,
            self.draft(original, summary="低价值摘要" * 1000, constraints=["保留这个约束"]),
        )
        self.assertLessEqual(handoff.token_count, 300)
        self.assertIn("summary", handoff.dropped_fields)
        self.assertEqual(handoff.constraints, ["保留这个约束"])
        self.assertTrue(handoff.next_actions)
        self.assertTrue(handoff.current_stage)

    def test_unresolvable_pointer_is_rejected(self):
        draft = HandoffDraft(
            goal="目标", current_stage="开始", next_actions=["继续"],
            evidence_refs=[HistoryPointer(kind="event", target_id="evt:missing")],
        )
        with self.assertRaisesRegex(ValueError, "unresolvable"):
            self.service.build_handoff(self.session1.session_id, draft)
        with self.assertRaisesRegex(ValueError, "unresolvable storage_ref"):
            self.service.record_event(
                task_id=self.session1.task_id, session_id=self.session1.session_id,
                event_type=HistoryEventType.STRUCTURED_OUTPUT,
                node="test", source="test", storage_ref="artifact:missing",
            )

    def test_history_is_database_immutable(self):
        event = self.event("原始消息")
        connection = sqlite3.connect(self.store.path)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            connection.execute("UPDATE history_events SET source='changed' WHERE event_id=?",
                               (event.event_id,))
        connection.close()

    def test_history_and_lineage_survive_store_reopen(self):
        event = self.event("持久消息")
        reopened = SQLiteContinuityStore(self.store.path)
        self.assertEqual(reopened.get_history_event(event.event_id).payload["text"], "持久消息")
        self.assertEqual(reopened.active_session(self.session1.task_id).session_id,
                         self.session1.session_id)

    def test_rollover_trigger_uses_tokens_or_explicit_checkpoint(self):
        self.assertIsNone(self.service.should_rollover(active_context_tokens=99))
        self.assertEqual(self.service.should_rollover(active_context_tokens=100),
                         RolloverReason.TOKEN_THRESHOLD)
        self.assertEqual(self.service.should_rollover(active_context_tokens=1, explicit_checkpoint=True),
                         RolloverReason.EXPLICIT_CHECKPOINT)


class LongHorizonRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_foundation_runtime_uses_session_window_and_records_new_events(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteContinuityStore(Path(root) / "runtime.sqlite")
            service = LongHorizonContextService(store, now=Clock())
            session1 = service.open_task("thread:runtime")
            message = service.record_event(
                task_id=session1.task_id, session_id=session1.session_id,
                event_type=HistoryEventType.USER_MESSAGE,
                node="input", source="user", payload={"text": "不要现场版"},
            )
            rollover = service.rollover(
                session1.session_id,
                HandoffDraft(
                    goal="继续选歌", constraints=["不要现场版"],
                    constraint_refs={"不要现场版": [HistoryPointer(
                        kind="event", target_id=message.event_id)]},
                    current_stage="等待下一轮", next_actions=["继续选歌"],
                    revalidate_fields=["current_song_id"],
                    last_known_state={"current_song_id": 1},
                ),
                RolloverReason.EXPLICIT_CHECKPOINT,
            )

            class Graph:
                input = None
                config = None

                async def astream(self, graph_input, config, **kwargs):
                    self.input, self.config = graph_input, config
                    yield {"type": "updates", "data": {
                        "final_response": {"final_response": "继续处理。"}
                    }}

            graph = Graph()
            with patch(
                "app.agent.foundation.streaming.get_long_horizon_service",
                return_value=service,
            ), patch(
                "app.agent.foundation.streaming.get_foundation_graph",
                return_value=graph,
            ):
                events = [event async for event in stream_foundation_assistant(
                    "继续", "thread:runtime",
                    RoutingContext(has_current_song=True, current_song_id=2,
                                   queue_size=1, current_index=0),
                )]

            self.assertEqual(
                graph.config["configurable"]["thread_id"],
                rollover.new_session.session_id,
            )
            continuity = graph.input["continuity_context"]
            self.assertEqual(continuity["handoff"]["handoff_id"], rollover.handoff.handoff_id)
            self.assertEqual(continuity["changed_fields"], ["current_song_id"])
            final = next(event for event in events if isinstance(event, FinalStreamEvent))
            self.assertEqual(final.response.message, "继续处理。")
            session2_events = service.query_task_history(
                "thread:runtime", session_id=rollover.new_session.session_id,
            )
            self.assertIn(HistoryEventType.USER_MESSAGE,
                          {event.event_type for event in session2_events})
            self.assertIn(HistoryEventType.ASSISTANT_MESSAGE,
                          {event.event_type for event in session2_events})


if __name__ == "__main__":
    unittest.main()
