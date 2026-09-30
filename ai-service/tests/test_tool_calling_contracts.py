import unittest

from pydantic import ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import PlayMode, RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling import ClientAction, ClientActionName
from app.agent.foundation.subgraphs.tool_calling.models import (
    EmptyActionArguments,
    JumpToQueueItemArguments,
    PlayAlbumArguments,
    SetPlayModeArguments,
    SetVolumeArguments,
)


class ToolCallingContractTests(unittest.TestCase):
    def test_routing_context_validates_player_snapshot(self) -> None:
        context = RoutingContext(
            has_current_song=True,
            current_song_id=185809,
            volume=0.5,
            play_mode=PlayMode.SHUFFLE,
            queue_size=3,
            current_index=1,
            current_time=30,
            duration=240,
        )

        self.assertEqual(context.current_song_id, 185809)
        self.assertEqual(context.play_mode, PlayMode.SHUFFLE)

        with self.assertRaises(ValidationError):
            RoutingContext(queue_size=1, current_index=1)
        with self.assertRaises(ValidationError):
            RoutingContext(current_time=20, duration=10)

    def test_client_action_rejects_arguments_for_another_action(self) -> None:
        with self.assertRaises(ValidationError):
            ClientAction(
                action_id="action-1",
                task_id="task-1",
                name=ClientActionName.PAUSE_PLAYER,
                arguments=SetVolumeArguments(volume=0.5),
            )

        action = ClientAction(
            action_id="action-2",
            task_id="task-1",
            name=ClientActionName.SET_PLAY_MODE,
            arguments=SetPlayModeArguments(mode=PlayMode.SINGLE_LOOP),
        )
        self.assertEqual(action.arguments.mode, PlayMode.SINGLE_LOOP)

    def test_needs_input_is_distinct_from_execution_failure(self) -> None:
        result = TaskResult(
            task_id="task-1",
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.NEEDS_INPUT,
            content="请先选择一首歌曲。",
        )

        self.assertIs(result.status, TaskStatus.NEEDS_INPUT)
        self.assertIsNone(result.error)

    def test_empty_arguments_are_valid_only_for_argumentless_action(self) -> None:
        action = ClientAction(
            action_id="action-3",
            task_id="task-1",
            name=ClientActionName.PLAY_NEXT,
            arguments=EmptyActionArguments(),
        )
        self.assertEqual(action.arguments.model_dump(), {})

        with self.assertRaises(ValidationError):
            ClientAction(
                action_id="action-4",
                task_id="task-1",
                name=ClientActionName.SET_VOLUME,
                arguments=EmptyActionArguments(),
            )

    def test_queue_actions_enforce_target_and_nonempty_collection(self) -> None:
        self.assertEqual(JumpToQueueItemArguments(index=5).index, 5)
        self.assertEqual(JumpToQueueItemArguments(song_id=185809).song_id, 185809)
        with self.assertRaises(ValidationError):
            JumpToQueueItemArguments()
        with self.assertRaises(ValidationError):
            JumpToQueueItemArguments(song_id=185809, index=1)
        with self.assertRaises(ValidationError):
            PlayAlbumArguments(album_id=18905, name="叶惠美", tracks=[])


if __name__ == "__main__":
    unittest.main()
