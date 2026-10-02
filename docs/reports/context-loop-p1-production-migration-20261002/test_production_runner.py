import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import production_runner as p
import test_rehearsal_runner as fixtures


def valid_gate():
    return dict(database='moodflow_agent',role='moodflow',readonly='on',standby=False,
                ledger=p.r.OLD_LEDGER,history_rows=328,database_bytes=p.r.EXPECTED_DATABASE_BYTES,
                execution_exists=False,event_seq_exists=False,immutable_enabled=True,
                ai_listeners=1,**{k:0 for k in ('active_sessions','advisory_locks','active_runs',
                'pending_runs','unknown_runs','latest_pending_writes','legacy_pending_sends',
                'pending_controls','unknown_checkpoints','replication_senders','replication_slots',
                'ai_inbound_established')})


class Guards(unittest.TestCase):
    def test_activity_and_unknown_state_stop(self):
        p.check_gate(valid_gate())
        for field in ('active_sessions','advisory_locks','active_runs','pending_runs','unknown_runs',
                      'latest_pending_writes','legacy_pending_sends','pending_controls','unknown_checkpoints',
                      'ai_inbound_established','replication_senders','replication_slots'):
            with self.subTest(field=field),self.assertRaises(p.r.Stop):
                p.check_gate({**valid_gate(),field:1})
        for field,value in (('ai_listeners',0),('ledger',p.r.NEW_LEDGER),('history_rows',329),
                            ('immutable_enabled',False),('role','wrong'),('database','wrong'),('readonly','off')):
            with self.subTest(field=field),self.assertRaises(p.r.Stop):
                p.check_gate({**valid_gate(),field:value})

    def test_bad_rehearsal_stops_before_production_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            path.write_text(json.dumps({'status':'failed'}),encoding='utf-8')
            with self.assertRaises(p.r.Stop): p.checked_rehearsal(path)

    def test_network_probe_uses_only_counts(self):
        self.assertNotIn('environ',p.NETWORK_PROBE)
        self.assertNotIn('cmdline',p.NETWORK_PROBE)
        self.assertIn("int(fields[1].split(':')[1],16)==8001",p.NETWORK_PROBE)

    def test_standalone_payload_matches_reviewed_sources_and_shell_parses(self):
        here=Path(__file__).resolve().parent
        shell=(here/'gate3-production-migration.sh').read_text(encoding='utf-8')
        support=(here/'rehearsal_runner.py').read_text(encoding='utf-8').split('\ndef main():')[0]
        self.assertIn(repr(base64.b64encode(support.encode()).decode()),shell)
        self.assertTrue(shell.endswith((here/'production_runner.py').read_text(encoding='utf-8')+'\nMF_APPROVED_PRODUCTION\n'))
        # Syntax only; the script is never executed against a local or remote production name.
        p.r.run(['docker','run','--rm','--pull','never','--network','none','--entrypoint','sh',p.r.IMAGE,'-n'],data=shell.encode())


class IsolatedProductionFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.IsolatedDockerFlow.setUpClass.__func__(cls)
        cls.source.sql("""
ALTER TABLE checkpoints ADD COLUMN thread_id TEXT, ADD COLUMN checkpoint_ns TEXT,
 ADD COLUMN checkpoint_id TEXT, ADD COLUMN parent_checkpoint_id TEXT, ADD COLUMN checkpoint JSONB;
ALTER TABLE checkpoint_blobs ADD COLUMN thread_id TEXT, ADD COLUMN checkpoint_ns TEXT,
 ADD COLUMN channel TEXT, ADD COLUMN version TEXT, ADD COLUMN type TEXT, ADD COLUMN blob BYTEA;
ALTER TABLE checkpoint_writes ADD COLUMN thread_id TEXT, ADD COLUMN checkpoint_ns TEXT,
 ADD COLUMN checkpoint_id TEXT, ADD COLUMN channel TEXT;
""")
        cls.meta=cls.source.metadata()

    @classmethod
    def tearDownClass(cls):
        fixtures.IsolatedDockerFlow.tearDownClass.__func__(cls)

    def test_1_pending_latest_and_consumed_old_writes(self):
        def query(prefix):
            output=self.source.sql('BEGIN;'+prefix+p.GATE_QUERY+'ROLLBACK;').stdout.decode()
            return json.loads(next(l for l in output.splitlines() if l.startswith('{')))
        base="""INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,checkpoint)
VALUES('fake','', '002','001','{"v":4,"channel_versions":{},"versions_seen":{}}');
INSERT INTO checkpoint_writes(thread_id,checkpoint_ns,checkpoint_id,channel)
VALUES('fake','','001','messages');"""
        consumed=query(base)
        self.assertEqual(consumed['latest_pending_writes'],0)
        pending=query(base+"INSERT INTO checkpoint_writes(thread_id,checkpoint_ns,checkpoint_id,channel) VALUES('fake','','002','messages');")
        self.assertEqual(pending['latest_pending_writes'],1)
        branch="""INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('fake','','002','{"v":4,"channel_versions":{"branch:to:agent":"0001"},"versions_seen":{}}');
INSERT INTO checkpoint_blobs(thread_id,checkpoint_ns,channel,version,type,blob)
VALUES('fake','','branch:to:agent','0001','msgpack','\\xc0');"""
        self.assertEqual(query(branch)['pending_controls'],1)
        self.assertEqual(query(branch+"UPDATE checkpoints SET checkpoint=jsonb_set(checkpoint,'{versions_seen}','{\"agent\":{\"branch:to:agent\":\"0001\"}}');")['pending_controls'],0)
        self.assertEqual(query(branch+"UPDATE checkpoint_blobs SET type='empty',blob=NULL;")['pending_controls'],0)
        self.assertEqual(query(branch+"DELETE FROM checkpoint_blobs;")['unknown_checkpoints'],1)
        progress="""INSERT INTO agent_history_event(event_id,task_id,session_id,event_type,event_timestamp,node,source,body)
VALUES('test-run-progress','fixture-task','fixture-session','run_progress',now(),'fixture','fixture',
'{"payload":{"run":{"run_id":"test-run","status":"running","pending_task_ids":["one"]}}}');"""
        state=query(progress)
        self.assertEqual((state['active_runs'],state['pending_runs']),(1,1))
        self.assertEqual(self.source.metadata()['history_rows'],328)

    def test_2_error_rolls_back_and_success_commits(self):
        sql=p.migration_sql(fixtures.r.INNER)
        for name,raw in p.r.decoded_migrations().items():
            self.source.write(name,raw)
        with patch.object(p.r,'PRODUCTION',self.name):
            db=p.Production(fixtures.r.INNER)
            failed=db.sql(sql.replace('\\i '+fixtures.r.INNER+'/004_continuity_execution.sql',
                                     'SELECT 1/0;\n\\i '+fixtures.r.INNER+'/004_continuity_execution.sql'),check=False)
            self.assertIn('PRODUCTION_TRANSACTION_ROLLED_BACK',failed.stdout.decode())
            self.assertEqual(db.ledger()['ledger'],p.r.OLD_LEDGER)
            self.assertFalse(db.ledger()['event_seq_exists'])
            real_run=p.r.run
            def fixture_command(args,**kwargs):
                if args[:4]==['docker','exec','-i',p.AI]:
                    return subprocess.CompletedProcess(args,0,b'{"ai_listeners":1,"ai_inbound_established":0}',b'')
                return real_run(args,**kwargs)
            with tempfile.TemporaryDirectory() as root,patch.object(p,'REPORTS',Path(root)),\
                 patch.object(p.r,'checked_backup',return_value=({},Path(root))),\
                 patch.object(p,'checked_rehearsal',return_value={}),\
                 patch.object(p.r,'EXPECTED_DATABASE_BYTES',self.meta['database_bytes']),\
                 patch.object(p.os,'geteuid',return_value=0,create=True),\
                 patch.object(p.r,'run',side_effect=fixture_command),patch.object(p.time,'sleep'):
                # Busy state stops before BEGIN READ WRITE, preserves schema and ledger,
                # and cleans only this attempt's temporary profile.
                output=io.StringIO()
                with patch.object(p.Production,'gate',return_value={**valid_gate(),'active_runs':1}),contextlib.redirect_stdout(output):
                    self.assertEqual(p.main(),1)
                stopped=json.loads(next(Path(root).glob('*/report.json')).read_text(encoding='utf-8'))
                self.assertFalse(stopped['production_transaction_attempted'])
                self.assertEqual(stopped['temporary_profile_cleanup'],'passed')
                self.assertEqual(db.ledger()['ledger'],p.r.OLD_LEDGER)
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(p.main(),0)
                reports=[json.loads(f.read_text(encoding='utf-8')) for f in Path(root).glob('*/report.json')]
                passed=next(item for item in reports if item['status']=='passed')
                self.assertTrue(passed['production_commit_confirmed'])
                self.assertEqual(passed['temporary_profile_cleanup'],'passed')
                self.assertEqual(len(passed['gate_3_observations']),6)
                self.assertIn('3.1_LEDGER',output.getvalue())
                self.assertIn('PRODUCTION_TRANSACTION_COMMITTED',output.getvalue())
            self.assertEqual(db.ledger()['ledger'],p.r.NEW_LEDGER)
            checks=db.sql(p.r.POSTCHECK_SQL).stdout.decode()
            p.check_postchecks(json.loads(next(l for l in reversed(checks.splitlines()) if l.startswith('{'))))
            # An accidental second invocation must stop at gate 3, without DDL.
            again=db.sql(sql,check=False)
            self.assertIn('PRODUCTION_GATE_3_STOPPED_NO_MIGRATION',again.stdout.decode())
            self.assertNotIn('PRODUCTION_TRANSACTION_COMMITTED',again.stdout.decode())


if __name__=='__main__':
    unittest.main(verbosity=2)
