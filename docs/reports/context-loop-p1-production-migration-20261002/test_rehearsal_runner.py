import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("rehearsal_runner", HERE/"rehearsal_runner.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class Guards(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        (root/"manifests").mkdir()
        (root/"postgres").mkdir()
        manifest_path = root/"manifests"/"20261001T234103Z-3a11237c.json"
        dump = root/"postgres"/(manifest_path.stem+".dump")
        raw = b"PGDMPfixture-no-production-data"
        dump.write_bytes(raw)
        item = {"status":"complete", "restore_verified":True,
                "postgres_metadata":{"database":"moodflow_agent", "database_size_bytes":r.EXPECTED_DATABASE_BYTES},
                "postgres":{"path":str(dump),"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest()}}
        manifest_path.write_text(json.dumps(item), encoding="utf-8")
        return root, manifest_path, dump, item

    def test_valid_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            root, manifest, dump, _ = self.fixture(folder)
            self.assertEqual(r.checked_backup(manifest, root)[1], dump)

    def test_backup_failure_guards(self):
        for problem in ("failed","database","size","sha256","missing_dump","outside"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as folder:
                root, manifest, dump, item = self.fixture(folder)
                if problem == "failed": item["status"] = "failed"
                if problem == "database": item["postgres_metadata"]["database"] = "wrong"
                if problem == "size": item["postgres"]["bytes"] += 1
                if problem == "sha256": item["postgres"]["sha256"] = "0"*64
                if problem == "missing_dump": dump.unlink()
                if problem == "outside": item["postgres"]["path"] = str(root/"outside.dump")
                manifest.write_text(json.dumps(item), encoding="utf-8")
                with self.assertRaises(r.Stop): r.checked_backup(manifest, root)

    def test_source_hashes_match_and_corruption_stops(self):
        for name, raw in r.decoded_migrations().items():
            self.assertEqual(raw, (ROOT/"ai-service/migrations/postgresql"/name).read_bytes())
        changed = copy.deepcopy(r.MIGRATIONS)
        changed[next(iter(changed))]["sha256"] = "0"*64
        with patch.object(r, "MIGRATIONS", changed), self.assertRaises(r.Stop):
            r.decoded_migrations()

    def test_payload_is_exact_runner_and_has_valid_shell_syntax(self):
        shell = (HERE/"gate2-isolated-rehearsal.sh").read_text(encoding="utf-8")
        inner = shell.split("<<'MF_ISOLATED_REHEARSAL'\n",1)[1].rsplit("\nMF_ISOLATED_REHEARSAL",1)[0]+"\n"
        self.assertEqual(inner, (HERE/"rehearsal_runner.py").read_text(encoding="utf-8"))
        compile(inner, "rehearsal-runner", "exec")
        result = r.run(["docker","exec","-i","moodflow-rehearsal-20260928-postgres-1","sh","-n"], data=shell.encode())
        self.assertEqual(result.returncode, 0)

    def test_cleanup_refuses_production_and_mismatched_owner(self):
        with patch.object(r, "text") as command:
            with self.assertRaises(r.Stop):
                r.cleanup_resources("moodflow-production-postgres-1","123456789abc",True,True)
            command.assert_not_called()
        name = "mf-context-rehearsal-123456789abc"
        with patch.object(r,"text",return_value=json.dumps({r.LABEL:"different"})) as command:
            with self.assertRaises(r.Stop): r.cleanup_resources(name,"123456789abc",True,True)
            self.assertEqual(command.call_count, 1)

    def test_cleanup_owned_resources_uses_exact_names(self):
        name, token = "mf-context-rehearsal-123456789abc", "123456789abc"
        with patch.object(r, "text", side_effect=[json.dumps({r.LABEL:token}),name,json.dumps({r.LABEL:token}),name]) as command:
            r.cleanup_resources(name, token, True, True)
            self.assertEqual(command.call_args_list[1].args[0], ["docker","rm","-f",name])
            self.assertEqual(command.call_args_list[3].args[0], ["docker","volume","rm",name])

    def test_restored_scale_and_schema_mismatch_stop(self):
        valid = {"database":"moodflow_agent","history_rows":328,"ledger":r.OLD_LEDGER,
                 "execution_exists":False,"event_seq_exists":False,"vector":"0.8.6",**{t:0 for t in r.CORE}}
        manifest = {"postgres_restore":{"pgvector_version":"0.8.6",**{t:0 for t in r.CORE}}}
        r.check_scale(valid, manifest)
        for field, value in (("history_rows",327),("ledger",r.NEW_LEDGER),("execution_exists",True),("event_seq_exists",True),("vector","wrong"),("checkpoints",1)):
            with self.subTest(field=field):
                invalid = {**valid,field:value}
                with self.assertRaises(r.Stop): r.check_scale(invalid,manifest)

    def test_low_resources_do_not_create_docker_resources(self):
        for resource in ("disk", "memory"):
            with self.subTest(resource=resource), tempfile.TemporaryDirectory() as folder:
                meminfo = Path(folder)/"meminfo"
                meminfo.write_text("MemAvailable: 1 kB\n",encoding="utf-8")
                def task_path(value):
                    return meminfo if str(value) == "/proc/meminfo" else Path(value)
                free = 0 if resource == "disk" else 20*1024**3
                with patch.object(r.os,"geteuid",return_value=0,create=True), patch.object(r,"checked_backup",return_value=({},Path(folder))), patch.object(r,"text",side_effect=[r.IMAGE,"fixture-image-id"]) as command, patch.object(r.shutil,"disk_usage",return_value=SimpleNamespace(free=free)), patch.object(r,"Path",task_path):
                    with self.assertRaises(r.Stop): r.main()
                    self.assertEqual(command.call_count,2)
                    self.assertTrue(all(call.args[0][1] in ("inspect","image") for call in command.call_args_list))


class IsolatedDockerFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="moodflow-rehearsal-local-validation-")
        cls.root = Path(cls.temp.name)
        (cls.root/"manifests").mkdir()
        (cls.root/"postgres").mkdir()
        cls.token = uuid.uuid4().hex[:12]
        cls.name = "mf-context-rehearsal-"+cls.token
        cls.created_volume = cls.created_container = False
        try:
            r.text(["docker","volume","create","--label",r.LABEL+"="+cls.token,cls.name])
            cls.created_volume = True
            r.text(["docker","run","-d","--pull","never","--name",cls.name,"--network","none",
                    "--label",r.LABEL+"="+cls.token,"--cpus","0.5","--memory","1g",
                    "--mount","type=volume,source="+cls.name+",target=/var/lib/postgresql/data",
                    "--env","POSTGRES_HOST_AUTH_METHOD=trust","--env","POSTGRES_USER=moodflow",
                    "--env","POSTGRES_DB=moodflow_agent",r.IMAGE])
            cls.created_container = True
            cls.source = r.Lab(cls.name,cls.token)
            for _ in range(45):
                if cls.source.command("sh","-c",'[ "$(cat /proc/1/comm)" = postgres ] && pg_isready -U moodflow -d moodflow_agent',check=False).returncode == 0: break
                time.sleep(1)
            else: raise AssertionError("fixture PG not ready")
            cls.source.command("mkdir","-m","700",r.INNER)
            cls.source.write("service.conf",b"[moodflow-approved-migration]\nhost=/var/run/postgresql\ndbname=moodflow_agent\nuser=moodflow\n")
            cls.source.write("empty.pgpass",b"")
            schema = (ROOT/"ai-service/migrations/postgresql/001_agent_continuity.sql").read_text(encoding="utf-8")
            schema += (ROOT/"ai-service/migrations/postgresql/002_long_term_memory.sql").read_text(encoding="utf-8")
            schema += """
CREATE TABLE agent_schema_migration(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now());
INSERT INTO agent_schema_migration(version) VALUES ('001_agent_continuity.sql'),('002_long_term_memory.sql');
CREATE TABLE checkpoints(id INT); CREATE TABLE checkpoint_blobs(id INT); CREATE TABLE checkpoint_writes(id INT);
INSERT INTO agent_task(task_id,conversation_id,created_at) VALUES ('fixture-task','fixture-conversation',now());
INSERT INTO agent_session(session_id,task_id,conversation_id,created_at) VALUES ('fixture-session','fixture-task','fixture-conversation',now());
INSERT INTO agent_history_event(event_id,task_id,session_id,event_type,event_timestamp,node,source,body)
SELECT 'fixture-event-'||g,'fixture-task','fixture-session','fixture',now(),'fixture','fixture','{}'::jsonb FROM generate_series(1,328) g;
"""
            cls.source.sql(schema)
            cls.meta = cls.source.metadata()
            cls.dump = cls.root/"postgres"/"20261001T234103Z-3a11237c.dump"
            archive = cls.source.command("pg_dump","-Fc","--no-owner","--no-privileges","-U","moodflow","-d","moodflow_agent").stdout
            cls.dump.write_bytes(archive)
            cls.manifest_path = cls.root/"manifests"/"20261001T234103Z-3a11237c.json"
            item = {"status":"complete","restore_verified":True,
                    "postgres_metadata":{"database":"moodflow_agent","database_size_bytes":cls.meta["database_bytes"]},
                    "postgres":{"path":str(cls.dump),"bytes":len(archive),"sha256":hashlib.sha256(archive).hexdigest()},
                    "postgres_restore":{"pgvector_version":cls.meta["vector"],**{t:cls.meta[t] for t in r.CORE}}}
            cls.manifest_path.write_text(json.dumps(item),encoding="utf-8")
            cls.meminfo = cls.root/"meminfo"
            cls.meminfo.write_text("MemAvailable: 6291456 kB\n",encoding="utf-8")
        except BaseException:
            r.cleanup_resources(cls.name,cls.token,cls.created_container,cls.created_volume)
            cls.temp.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        r.cleanup_resources(cls.name,cls.token,cls.created_container,cls.created_volume)
        cls.temp.cleanup()

    def execute(self, migrations=None):
        reports = self.root/("reports-"+uuid.uuid4().hex[:6])
        real_checked = r.checked_backup
        real_disk_usage = shutil.disk_usage
        def task_path(value):
            return self.meminfo if str(value) == "/proc/meminfo" else Path(value)
        def disk_usage(value):
            return real_disk_usage(self.root if str(value) == "/var/lib/docker" else value)
        output = io.StringIO()
        with patch.object(r,"PRODUCTION",self.name), patch.object(r,"EXPECTED_DATABASE_BYTES",self.meta["database_bytes"]), patch.object(r,"REPORTS",reports), patch.object(r,"Path",task_path), patch.object(r,"checked_backup",side_effect=lambda:real_checked(self.manifest_path,self.root)), patch.object(r.os,"geteuid",return_value=0,create=True), patch.object(r.shutil,"disk_usage",side_effect=disk_usage), patch.object(r,"MIGRATIONS",migrations or r.MIGRATIONS), contextlib.redirect_stdout(output):
            exit_code = r.main()
        report_path = next(reports.glob("*/report.json"))
        return exit_code, json.loads(report_path.read_text(encoding="utf-8")), output.getvalue()

    def test_normal_restore_migrate_postcheck_and_cleanup(self):
        code, report, output = self.execute()
        self.assertEqual(code,0)
        self.assertEqual(report["status"],"passed")
        self.assertEqual(report["cleanup"],"passed")
        self.assertEqual(report["restored_before"]["history_rows"],328)
        self.assertEqual(report["restored_after"]["ledger"],r.NEW_LEDGER)
        self.assertEqual(report["readonly_postchecks"]["missing_sequence"],0)
        self.assertTrue(report["readonly_postchecks"]["immutable_trigger_enabled"])
        self.assertGreater(report["metrics"]["wal_bytes"],0)
        self.assertGreater(report["metrics"]["lock_window_upper_bound_ms"],0)
        self.assertIn("ISOLATED_REHEARSAL_FINISHED",output)
        self.assertEqual(self.source.metadata()["ledger"],r.OLD_LEDGER)
        self.assertFalse(self.source.metadata()["event_seq_exists"])

    def test_error_mid_migration_rolls_back_schema_and_ledger(self):
        changed = copy.deepcopy(r.MIGRATIONS)
        name = next(iter(changed))
        raw = base64.b64decode(changed[name]["base64"]) + b"SELECT 1/0;\n"
        changed[name] = {"base64":base64.b64encode(raw).decode(),"sha256":hashlib.sha256(raw).hexdigest()}
        code, report, output = self.execute(changed)
        self.assertEqual(code,1)
        self.assertEqual(report["status"],"failed")
        self.assertEqual(report["cleanup"],"passed")
        self.assertEqual(report["rollback_ledger_check"]["ledger"],r.OLD_LEDGER)
        self.assertFalse(report["rollback_ledger_check"]["event_seq_exists"])
        self.assertFalse(report["rollback_ledger_check"]["execution_exists"])
        self.assertNotIn("ISOLATED_REHEARSAL_FINISHED",output)
        self.assertIn("no_retry",report["failure_reason"])
        self.assertEqual(self.source.metadata()["ledger"],r.OLD_LEDGER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
