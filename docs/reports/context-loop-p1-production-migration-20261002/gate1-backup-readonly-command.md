# 门控 1：备份只读检查命令

此命令由请求方在 ECS root 终端执行，检查现有 systemd 定时器、最新三条备份状态及最新完成且标记恢复验证成功的 PostgreSQL 备份。仅输出白名单字段、行数和校验结论；不读取凭据文件，不调用备份、恢复或迁移。

依据现有 `deploy/backup.py`：PostgreSQL 使用 custom-format pg_dump；备份清单只有在隔离恢复、核心表计数和文件完整性核验完成后才写入 `restore_verified=true / status=complete`。本命令重新核对 dump 文件大小、custom 文件头和 SHA-256，并查看清单中的恢复证据；没有再次运行恢复。文件缺失、损坏、目标库错误或证据不全均输出 STOP。

输出需由代理审阅备份时间、最近尝试状态及恢复证据，不能凭结束标记自动认定门控通过；尚未核对的恢复负责人也不能当作已确认。本命令未在生产执行。

```bash
systemctl show moodflow-backup.timer --no-pager \
  -p LoadState -p ActiveState -p UnitFileState \
  -p LastTriggerUSec -p NextElapseUSecRealtime
systemctl show moodflow-backup.service --no-pager \
  -p ActiveState -p Result -p ExecMainStatus \
  -p ExecMainStartTimestamp -p ExecMainExitTimestamp
python3 - <<'MF_BACKUP_CHECK'
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path

root = Path("/secure/moodflow/backups")
manifests = root / "manifests"
safe_stamp = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
tables = ("agent_session", "agent_task", "agent_long_term_memory",
          "checkpoints", "checkpoint_blobs", "checkpoint_writes")

def stop(reason):
    print("STOP: " + reason)
    raise SystemExit(1)

try:
    paths = sorted(manifests.glob("*.json"))
    if not paths:
        stop("no_backup_manifests")
    records = []
    for path in paths:
        if (path.is_symlink() or not path.is_file()
                or not safe_stamp.fullmatch(path.stem)
                or not path.resolve().is_relative_to(manifests.resolve())):
            stop("unsafe_manifest_path")
        item = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(item, dict):
            stop("invalid_manifest")
        records.append((path, item))
    now = datetime.now(timezone.utc)
    print("CHECKED_AT_UTC=" + now.isoformat())
    for path, item in records[-3:]:
        status = item.get("status")
        if status not in ("running", "failed", "complete"):
            status = "invalid"
        print("RECENT_BACKUP=" + json.dumps({
            "manifest": path.name, "status": status,
            "restore_verified": item.get("restore_verified") is True}))
    candidates = [(path, item) for path, item in records
                  if item.get("status") == "complete"
                  and item.get("restore_verified") is True]
    if not candidates:
        stop("no_complete_restore_verified_backup")
    path, item = candidates[-1]
    started = datetime.fromisoformat(item["timestamp_utc"])
    completed = datetime.fromisoformat(item["completed_at_utc"])
    if (started.tzinfo is None or completed.tzinfo is None
            or started > completed or completed > now):
        stop("invalid_backup_times")
    print("SELECTED_MANIFEST=" + path.name)
    print("BACKUP_STARTED_UTC=" + started.isoformat())
    print("BACKUP_COMPLETED_UTC=" + completed.isoformat())
    print("BACKUP_AGE_HOURS=" + str(round((now-started).total_seconds()/3600, 2)))
    if item["postgres_metadata"]["database"] != "moodflow_agent":
        stop("backup_target_database_mismatch")
    print("BACKUP_DATABASE=moodflow_agent")
    entry = item["postgres"]
    dump = Path(entry["path"])
    if (dump.is_symlink() or not dump.is_file()
            or not dump.resolve().is_relative_to((root/"postgres").resolve())
            or dump.name != path.stem + ".dump"):
        stop("unsafe_or_missing_postgres_dump")
    size = dump.stat().st_size
    if size <= 0 or size != entry["bytes"]:
        stop("postgres_dump_size_mismatch")
    digest = hashlib.sha256()
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            stop("postgres_dump_not_custom_format")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != entry["sha256"]:
        stop("postgres_dump_sha256_mismatch")
    restored = item.get("postgres_restore", {})
    if (not restored.get("pgvector_version")
            or any(type(restored.get(t)) is not int or restored[t] < 0 for t in tables)):
        stop("postgres_restore_evidence_incomplete")
    print("POSTGRES_DUMP_BYTES=" + str(size))
    print("POSTGRES_DUMP_SHA256_MATCH=true")
    print("POSTGRES_DUMP_FORMAT=custom")
    print("RECORDED_RESTORE_VERIFIED=true")
    print("RECORDED_RESTORE_TABLE_COUNTS=" + json.dumps({t: restored[t] for t in tables}))
    print("BACKUP_DISK_FREE_BYTES=" + str(shutil.disk_usage(root).free))
    print("BACKUP_READONLY_INSPECTION_FINISHED; no_new_restore_run; migration_not_executed")
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    stop("backup_inspection_failed_no_valid_backup_confirmed")
MF_BACKUP_CHECK
```
