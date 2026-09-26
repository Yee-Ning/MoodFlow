"""Read existing PostgreSQL checkpoints for a real HTTP-created session.

This tool never creates sessions, writes checkpoints, sets up tables or restarts
PostgreSQL. The online application owns all session transitions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import connect
from psycopg.rows import dict_row

from app.config.settings import load_runtime_environment


def canonical_hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def read_evidence(session_id: str) -> dict:
    load_runtime_environment()
    if os.getenv("CHECKPOINT_BACKEND") != "postgres":
        raise ValueError("acceptance_requires_postgres_configuration")
    thread = "worldline:" + session_id
    with connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as connection:
        server = connection.execute("SELECT current_database() AS database, pg_postmaster_start_time() AS started_at").fetchone()
        checkpoints = connection.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata "
            "FROM checkpoints WHERE thread_id = %s ORDER BY checkpoint_id", (thread,),
        ).fetchall()
        blobs = connection.execute(
            "SELECT checkpoint_ns, channel, version, type, blob FROM checkpoint_blobs "
            "WHERE thread_id = %s ORDER BY checkpoint_ns, channel, version", (thread,),
        ).fetchall()
        blob_summary = [{k: v for k, v in row.items() if k != "blob"} | {
            "blob_sha256": sha256(bytes(row["blob"] or b"")).hexdigest(),
            "blob_bytes": len(row["blob"] or b""),
        } for row in blobs]
        writes = connection.execute(
            "SELECT checkpoint_id, task_id, idx, channel, type, blob FROM checkpoint_writes "
            "WHERE thread_id = %s ORDER BY checkpoint_id, task_id, idx", (thread,),
        ).fetchall()
        write_summary = [{k: v for k, v in row.items() if k != "blob"} | {
            "blob_sha256": sha256(bytes(row["blob"] or b"")).hexdigest(),
        } for row in writes]
        row = PostgresSaver(connection).get_tuple({"configurable": {"thread_id": thread}})
        if row is None:
            raise ValueError("online_session_checkpoint_missing")
        snapshot = row.checkpoint["channel_values"]["snapshot"]
        if snapshot["session_id"] != session_id:
            raise ValueError("online_session_checkpoint_identity_mismatch")
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
            "session_id": session_id, "thread_id": thread, "checkpoint_backend": "postgres",
            "database": server["database"], "postgres_started_at": server["started_at"].isoformat(),
            "checkpoint_count": len(checkpoints), "blob_count": len(blobs), "write_count": len(writes),
            "checkpoint_rows": checkpoints, "blob_summary": blob_summary, "write_summary": write_summary,
            "postgres_session_storage_sha256": canonical_hash([checkpoints, blob_summary, write_summary]),
            "session_state_sha256": canonical_hash(snapshot), "session_state": snapshot,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = read_evidence(args.session_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"checkpoint_count": result["checkpoint_count"], "revision": result["session_state"]["revision"], "read_only": True}))


if __name__ == "__main__":
    main()
