"""A public intent receipt gates writes; uncertain effects are never replayed."""

import json
import re
from contextvars import ContextVar
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from .history_writer import digest, _check_public_text
from .models import ArtifactRecord, HistoryEventType, HistoryPointer
from .public_events import PublicTool
from .execution import ContinuityConflict
from .write_reconciliation import (
    MusicWriteReconciler, ReconciliationEvidence, definite_failure, public_failure,
)

active_operations = ContextVar("continuity_operations", default=None)
WRITES = {"create_playlist_tool", "add_song_to_playlist", "remove_song_from_playlist_tool", "set_song_liked_tool",
          "delete_playlist_tool"}


def safe_tool_result(name, content):
    from app.schemas.music import (CreatedPlaylistResult, AddSongToPlaylistResult, RemoveSongFromPlaylistResult,
        SongLikeResult, MusicSearchResult, EntitySearchResult, SongLyricsResult, UserPlaylistsResult,
        PlaylistDetailResult, AlbumDetailResult, LikedSongsResult, DeletedPlaylistResult)
    from app.agent.handoff.song_context import SongContextRead
    from app.schemas.popularity import SongPopularityResult

    schemas = {"create_playlist_tool": CreatedPlaylistResult, "add_song_to_playlist": AddSongToPlaylistResult,
               "remove_song_from_playlist_tool": RemoveSongFromPlaylistResult, "set_song_liked_tool": SongLikeResult,
               "search_music": MusicSearchResult, "search_entities_tool": EntitySearchResult,
               "get_song_lyrics": SongLyricsResult, "get_user_playlists": UserPlaylistsResult,
               "get_playlist_detail_tool": PlaylistDetailResult, "get_album_detail_tool": AlbumDetailResult,
               "get_current_song_context": SongContextRead, "get_liked_songs": LikedSongsResult,
               "delete_playlist_tool": DeletedPlaylistResult,
               "get_song_popularity_tool": SongPopularityResult}
    if name not in schemas:
        raise ValueError("tool result has no artifact whitelist")
    value = schemas[name].model_validate_json(content).model_dump(mode="json")
    _check_public_text(value)
    return {"schema_version": 1, "name": name, "result": value}


class OperationJournal:
    def __init__(self, writer, accounting, *, read_client=None):
        self.writer, self.accounting = writer, accounting
        self.reconciler = MusicWriteReconciler(read_client) if read_client is not None else None

    @property
    def identity(self):
        return self.accounting.identity

    def unknown_writes(self, run_id):
        proposed, completed, cursor = set(), set(), 0
        for kind, target in ((HistoryEventType.TOOL_CALL, proposed), (HistoryEventType.TOOL_RESULT, completed)):
            cursor = 0
            while True:
                rows = self.writer.store.query_task_history(self.identity.continuity_task_id, event_type=kind, after_seq=cursor, limit=100)
                if not rows:
                    break
                for event in rows:
                    body = PublicTool.model_validate(event.payload)
                    if body.run_id == run_id and body.effect == "write":
                        if kind == HistoryEventType.TOOL_CALL or body.status in {"success", "failed"}:
                            target.add(body.operation_id)
                    cursor = event.event_seq
        return proposed - completed

    def write_receipts(self, proposal):
        """A namespace change never makes a previously dispatched write new."""
        prior, result = None, None
        for kind in (HistoryEventType.TOOL_CALL, HistoryEventType.TOOL_RESULT):
            cursor = 0
            while True:
                rows = self.writer.store.query_task_history(self.identity.continuity_task_id,
                    event_type=kind, after_seq=cursor, limit=100)
                if not rows:
                    break
                for event in rows:
                    body = PublicTool.model_validate(event.payload)
                    if body.run_id == proposal.run_id and body.operation_id == proposal.operation_id:
                        if (body.name, body.parameters, body.effect) != (proposal.name, proposal.parameters, "write"):
                            raise ContinuityConflict("operation receipt parameters disagree")
                        if kind == HistoryEventType.TOOL_CALL:
                            prior = event
                        elif body.status == "success":
                            result = event
                    cursor = event.event_seq
        return prior, result

    def write_events(self, operation_id=None):
        rows = []
        for kind in (HistoryEventType.TOOL_CALL, HistoryEventType.TOOL_RESULT):
            cursor = 0
            while True:
                batch = self.writer.store.query_task_history(self.identity.continuity_task_id,
                    event_type=kind, after_seq=cursor, limit=100)
                if not batch:
                    break
                for event in batch:
                    body = PublicTool.model_validate(event.payload)
                    if body.effect == "write" and (operation_id is None or body.operation_id == operation_id):
                        rows.append(event)
                    cursor = event.event_seq
        return sorted(rows, key=lambda event: event.event_seq)

    def state(self, operation_id):
        """Scope is the server-owned continuity task, never a caller-supplied task."""
        events = self.write_events(operation_id)
        proposals = [row for row in events if row.event_type is HistoryEventType.TOOL_CALL]
        if not proposals:
            raise ContinuityConflict("write operation not found in this task")
        proposal = PublicTool.model_validate(proposals[0].payload)
        if proposal.auth_scope is not None and proposal.auth_scope != getattr(self.accounting.lease, "scope", None):
            raise ContinuityConflict("write reconciliation auth scope mismatch")
        for row in events:
            body = PublicTool.model_validate(row.payload)
            if (body.run_id, body.name, body.parameters) != (proposal.run_id, proposal.name, proposal.parameters):
                raise ContinuityConflict("operation receipt parameters disagree")
        terminal = [row for row in events if row.payload["status"] in {"success", "failed"}]
        if {row.payload["status"] for row in terminal} == {"success", "failed"}:
            raise ContinuityConflict("operation terminal receipts disagree")
        latest = terminal[-1] if terminal else events[-1]
        status = latest.payload["status"]
        return {"operation_id": operation_id, "run_id": proposal.run_id,
                "name": proposal.name, "parameters": proposal.parameters,
                "state": "confirmed" if status == "success" else "failed" if status == "failed" else "unknown",
                "confirmation_source": latest.payload.get("confirmation_source", "response") if status == "success" else None,
                "error_code": latest.payload.get("error_code"), "error_message": latest.payload.get("error_message"),
                "proposal_ref": HistoryPointer(kind="event", target_id=proposals[0].event_id).model_dump(mode="json"),
                "receipt_ref": HistoryPointer(kind="event", target_id=latest.event_id).model_dump(mode="json"),
                "result_ref": latest.payload.get("result_ref"),
                "reconciliation_ref": latest.payload.get("reconciliation_ref")}

    def receipt_identity(self, event):
        return self.identity.model_copy(update={"session_id": event.session_id, "run_id": event.payload["run_id"]})

    def artifact(self, operation_id, payload, artifact_type="public_tool:v1"):
        _check_public_text(payload)
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 256000:
            raise ContinuityConflict("reconciliation artifact exceeds safe budget")
        artifact_id = digest([operation_id, payload])[7:]
        ref = HistoryPointer(kind="artifact", target_id="artifact:" + artifact_id)
        existing = self.writer.store.get_artifact(ref.target_id)
        if existing is None:
            self.writer.store.put_artifact(ArtifactRecord(artifact_id=artifact_id,
                task_id=self.identity.continuity_task_id, session_id=self.identity.session_id,
                artifact_type=artifact_type, created_at=datetime.now(UTC), content_hash=digest(payload),
                payload=payload, projection={"operation_id": operation_id}, metadata={}))
        elif existing.task_id != self.identity.continuity_task_id or digest(existing.payload) != existing.content_hash:
            raise ContinuityConflict("operation artifact integrity unproven")
        return ref

    def read_artifact(self, ref):
        ref = HistoryPointer.model_validate(ref)
        artifact = self.writer.store.get_artifact(ref.target_id)
        if artifact is None or artifact.task_id != self.identity.continuity_task_id or digest(artifact.payload) != artifact.content_hash:
            raise ContinuityConflict("operation artifact integrity unproven")
        return artifact.payload

    async def reconcile(self, operation_id):
        self.accounting.lease.check()
        state = self.state(operation_id)
        if state["state"] != "unknown":
            return state
        events = self.write_events(operation_id)
        original = next(row for row in events if row.event_type is HistoryEventType.TOOL_CALL)
        proposal = PublicTool.model_validate(original.payload)
        identity = self.receipt_identity(original)
        key = "operation:" + operation_id
        if events[-1].payload["status"] == "proposed":
            self.writer.append_public_event(identity, key + ":unknown", HistoryEventType.TOOL_RESULT,
                proposal.model_copy(update={"status": "unknown", "error_code": "missing_receipt",
                    "error_message": "写操作已有提案，但没有可确认的完成回执。"}))
        evidence = None
        evidence_ref = HistoryPointer.model_validate(state["reconciliation_ref"]) if state["reconciliation_ref"] else None
        if evidence_ref:
            evidence = ReconciliationEvidence.model_validate(self.read_artifact(evidence_ref))
            if (evidence.operation_id, evidence.proposal_ref.target_id) != (operation_id, original.event_id):
                raise ContinuityConflict("reconciliation evidence scope mismatch")
        if evidence is None or evidence.verdict != "confirmed":
            if self.reconciler is None:
                return self.state(operation_id)
            baseline = self.read_artifact(proposal.baseline_ref) if proposal.baseline_ref else None
            evidence = await self.reconciler.inspect(proposal,
                HistoryPointer(kind="event", target_id=original.event_id), baseline)
            self.accounting.lease.check()
            evidence_ref = self.artifact(operation_id, evidence.model_dump(mode="json"), "write_reconciliation:v1")
            previous = PublicTool.model_validate(self.write_events(operation_id)[-1].payload)
            self.writer.append_public_event(identity, key + ":check:" + digest(evidence.model_dump(mode="json"))[7:],
                HistoryEventType.TOOL_RESULT, previous.model_copy(update={"status": "unknown",
                    "reconciliation_ref": evidence_ref}), refs=(HistoryPointer(kind="event", target_id=original.event_id),))
        if evidence.verdict == "confirmed":
            payload = safe_tool_result(proposal.name, json.dumps(evidence.result, ensure_ascii=False))
            # The evidence is not allowed to change the original target.
            for field in ("playlist_id", "song_id", "liked", "name"):
                if field in proposal.parameters and payload["result"].get(field) != proposal.parameters[field]:
                    raise ContinuityConflict("reconciliation result target mismatch")
            self.accounting.lease.check()
            ref = self.artifact(operation_id, payload)
            state = self.state(operation_id)
            self.writer.append_public_event(identity, key + ":success", HistoryEventType.TOOL_RESULT,
                proposal.model_copy(update={"status": "success", "result_ref": ref,
                    "reconciliation_ref": evidence_ref, "confirmation_source": "readback"}),
                refs=(HistoryPointer.model_validate(state["receipt_ref"]),))
        return self.state(operation_id)

    async def reconcile_run(self, run_id):
        for operation_id in sorted(self.unknown_writes(run_id)):
            await self.reconcile(operation_id)

    async def answer_query(self, text):
        explicit = re.search(r"op:[0-9a-f]{64}", text)
        proposals = [row for row in self.write_events() if row.event_type is HistoryEventType.TOOL_CALL]
        if explicit:
            operation_id = explicit.group()
        elif proposals:
            names = ("add_song_to_playlist",) if re.search(r"加上|加进|加入|添加", text) else (
                ("set_song_liked_tool",) if re.search(r"收藏|红心|喜欢", text) else (
                ("delete_playlist_tool", "remove_song_from_playlist_tool") if re.search(r"移除|删除", text) else ("create_playlist_tool",)))
            candidates = [row for row in proposals if row.payload["name"] in names]
            if not candidates:
                return "当前会话没有对应写操作的可核对记录，无法确认外部效果。"
            operation_id = candidates[-1].payload["operation_id"]
        else:
            return "当前会话没有对应写操作的可核对记录，无法确认外部效果。"
        try:
            view = await self.reconcile(operation_id)
        except ContinuityConflict:
            return "该操作不属于当前会话，或证据完整性无法验证；无法确认结果，也不会重复写入。"
        target = "、".join(f"{field}={value}" for field, value in view["parameters"].items() if field in {"playlist_id", "song_id"})
        if view["state"] == "failed":
            answer = "后端已明确拒绝这次写操作，没有成功记账。"
        elif view["state"] == "unknown":
            answer = "这次写操作的效果仍未知，尚未读回确认；不会重复写入。"
        elif view["confirmation_source"] == "readback":
            answer = "只读核对已确认目标效果，并已补记成功回执；原始失败记录保留。"
        else:
            answer = "写操作已有后端成功回执；这条确认来自原始响应，未另行读回。"
        return f"{answer}（{target or view['name']}；{operation_id}）"

    async def call(self, request, execute):
        self.accounting.lease.check()
        call = request.tool_call
        name, parameters = call["name"], call["args"]
        run_id = self.identity.run_id
        from app.agent.foundation.subgraphs.tool_calling.client_tools import CLIENT_ACTION_TOOLS
        action_names = {tool.name for tool in CLIENT_ACTION_TOOLS}
        effect = "write" if name in WRITES else "client_action" if name in action_names else "read"
        # Same logical write gets the same id even if a model invents a new call_id.
        if effect == "write":
            parsed = PublicTool(run_id=run_id, operation_id="pending", name=name, parameters=parameters,
                                status="proposed", effect=effect)
            parameters = parsed.parameters
        operation_id = "op:" + digest([run_id, name, parameters, "write" if effect == "write" else call["id"]])[7:]
        proposal = PublicTool(run_id=run_id, operation_id=operation_id, name=name, parameters=parameters,
                              status="proposed", effect=effect,
                              auth_scope=getattr(self.accounting.lease, "scope", None) if effect == "write" else None)
        if effect == "write":
            # Adopt legacy IDs made before default arguments were canonicalized.
            # An unresolved identical write from an earlier run is also not new
            # permission to dispatch the same external request again.
            for event in reversed(self.write_events()):
                body = PublicTool.model_validate(event.payload)
                if event.event_type is HistoryEventType.TOOL_CALL and (body.name, body.parameters) == (name, parameters):
                    if body.run_id == run_id or self.state(body.operation_id)["state"] == "unknown":
                        proposal, operation_id = body, body.operation_id
                        break
        key = "operation:" + operation_id
        prior, result = self.write_receipts(proposal) if effect == "write" else (None, None)
        if effect == "write" and prior:
            state = await self.reconcile(operation_id)
            if state["state"] == "failed":
                from app.tools.music import MusicServiceError
                error = MusicServiceError(state["error_code"] or "write_rejected",
                                          state["error_message"] or "写操作已被明确拒绝。")
                error.operation_id, error.write_state = operation_id, "failed"
                raise error
            prior, result = self.write_receipts(proposal)
            if result and result.payload.get("result_ref"):
                ref = HistoryPointer.model_validate(result.payload["result_ref"])
                artifact = self.writer.store.get_artifact(ref.target_id)
                if artifact is None or artifact.task_id != self.identity.continuity_task_id or digest(artifact.payload) != artifact.content_hash:
                    raise ContinuityConflict("operation result integrity unproven")
                safe = safe_tool_result(name, json.dumps(artifact.payload["result"], ensure_ascii=False))
                return ToolMessage(content=json.dumps(safe["result"], ensure_ascii=False), name=name, tool_call_id=call["id"])
            error = ContinuityConflict("write effect unknown; read-only reconciliation required")
            error.operation_id, error.write_state = operation_id, "unknown"
            raise error
        if effect == "write" and name == "create_playlist_tool" and self.reconciler is not None:
            try:
                baseline = await self.reconciler.create_baseline()
            except Exception:
                baseline = None
            self.accounting.lease.check()
            if baseline is not None:
                proposal = proposal.model_copy(update={"baseline_ref": self.artifact(operation_id, baseline, "write_baseline:v1")})
        if name == "delete_playlist_tool":
            try:
                if parameters["confirmed"] is not True:
                    raise ValueError("delete not confirmed")
                if self.reconciler is None:
                    raise ValueError("delete read client missing")
                baseline = await self.reconciler.delete_baseline(parameters["playlist_id"])
            except ContinuityConflict:
                raise
            except Exception as exc:
                from app.tools.music import MusicServiceError
                self.accounting.lease.check()
                self.writer.append_public_event(self.identity, key + ":proposed", HistoryEventType.TOOL_CALL, proposal)
                failure = {"error_code": "http_422", "error_message": "删除前无法确认当前账号自建歌单，未执行删除。"}
                self.writer.append_public_event(self.identity, key + ":failed", HistoryEventType.TOOL_RESULT,
                    proposal.model_copy(update={"status": "failed", **failure}))
                error = MusicServiceError(failure["error_code"], failure["error_message"])
                error.operation_id, error.write_state = operation_id, "failed"
                raise error from exc
            self.accounting.lease.check()
            proposal = proposal.model_copy(update={"baseline_ref": self.artifact(operation_id, baseline, "write_baseline:v1")})
        self.writer.append_public_event(self.identity, key + ":proposed", HistoryEventType.TOOL_CALL, proposal)
        try:
            response = await execute(request)
        except Exception as exc:
            if effect != "write":
                raise
            self.accounting.lease.check()
            failure = public_failure(error=exc)
            status = "failed" if definite_failure(failure) else "unknown"
            self.writer.append_public_event(self.identity, key + ":" + status, HistoryEventType.TOOL_RESULT,
                proposal.model_copy(update={"status": status, **failure}))
            if status == "unknown":
                state = await self.reconcile(operation_id)
                if state["state"] == "confirmed":
                    payload = self.read_artifact(state["result_ref"])
                    return ToolMessage(content=json.dumps(payload["result"], ensure_ascii=False), name=name,
                                       tool_call_id=call["id"])
            exc.operation_id, exc.write_state = operation_id, status
            raise
        self.accounting.lease.check()
        success = isinstance(response, ToolMessage) and getattr(response, "status", "success") == "success"
        ref = None
        if success and effect != "client_action":
            try:
                payload = safe_tool_result(name, response.content)
                if effect == "write" and payload["result"].get("status") == "forbidden":
                    raise ValueError("write not accepted")
            except ValueError:
                success = False
            else:
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 256000:
                    raise ContinuityConflict("write result exceeds safe artifact budget")
                artifact_id = digest([operation_id, payload])[7:]
                ref = HistoryPointer(kind="artifact", target_id="artifact:" + artifact_id)
                if self.writer.store.get_artifact(ref.target_id) is None:
                    self.writer.store.put_artifact(ArtifactRecord(artifact_id=artifact_id,
                        task_id=self.identity.continuity_task_id, session_id=self.identity.session_id,
                        artifact_type="public_tool:v1", created_at=datetime.now(UTC), content_hash=digest(payload),
                        payload=payload, projection={"name": name, "operation_id": operation_id}, metadata={}))
        failure = public_failure(content=response.content if isinstance(response, ToolMessage) else None) if not success and effect == "write" else {}
        status = "success" if success else (
            "failed" if definite_failure(failure, response) else "unknown"
        ) if effect == "write" else "failed"
        if success and name == "delete_playlist_tool":
            # A response alone never commits a deletion receipt. The shared A
            # reconciler owns the read evidence and the sole success append.
            status = "unknown"
            failure = {"error_code": "readback_required", "error_message": "删除响应已收到，等待只读核对。"}
        self.writer.append_public_event(self.identity, key + ":" + status, HistoryEventType.TOOL_RESULT,
            proposal.model_copy(update={"status": status, "result_ref": ref, **failure,
                "confirmation_source": "response" if status == "success" and effect == "write" else None}))
        if effect == "write" and status == "unknown":
            state = await self.reconcile(operation_id)
            if state["state"] == "confirmed":
                payload = self.read_artifact(state["result_ref"])
                return ToolMessage(content=json.dumps(payload["result"], ensure_ascii=False), name=name, tool_call_id=call["id"])
        if effect == "write" and status != "success":
            from app.schemas.music import ToolFailure
            message = failure["error_message"]
            if status == "unknown":
                message = "原写请求没有有效成功回执，实际效果仍未知；只允许读回核对，禁止重复写入。原始错误：" + message
            return ToolMessage(content=ToolFailure(error=failure["error_code"], message=message,
                operation_id=operation_id, write_state=status).model_dump_json(exclude_none=True),
                name=name, tool_call_id=call["id"], status="error")
        return response


async def journal_tool_call(request, execute):
    operations = active_operations.get()
    return await operations.call(request, execute) if operations is not None else await execute(request)
