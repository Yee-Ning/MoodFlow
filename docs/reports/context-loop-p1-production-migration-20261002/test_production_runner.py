import base64
import contextlib
import gzip
import hashlib
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
    def test_channel_availability_and_versions_match_installed_scheduler(self):
        # Synthetic channels only: no app import, configuration or checkpoint payload.
        python=Path(__file__).resolve().parents[3]/'ai-service/.venv/Scripts/python.exe'
        source="""import json
from langgraph.channels.ephemeral_value import EphemeralValue
from langgraph.channels.named_barrier_value import NamedBarrierValue
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.pregel._algo import _triggers
from langgraph.pregel._read import PregelNode
name='branch:to:agent'
proc=PregelNode(channels=[name],triggers=[name])
channel=EphemeralValue(type(None)).from_checkpoint(None)
result={'inline_none_available':channel.is_available(),
 'missing_unavailable':not EphemeralValue(type(None)).is_available(),
 'empty_join_unavailable':not NamedBarrierValue(str,{'a','b'}).from_checkpoint(set()).is_available(),
 'empty_set_hex':JsonPlusSerializer().dumps_typed(set())[1].hex(),
 'versions':[_triggers({name:channel},{name:v},{name:s},type(v)(),proc)
  for v,s in [('02','01'),('01','01'),('01','02'),(2,1),(1,1),(1,2)]],
 'missing_seen_node':_triggers({name:channel},{name:''},None,'',proc)}
print(json.dumps(result))
"""
        item=json.loads(subprocess.run([str(python),'-I','-c',source],check=True,capture_output=True).stdout)
        self.assertTrue(all(item[k] for k in ('inline_none_available','missing_unavailable','empty_join_unavailable','missing_seen_node')))
        self.assertEqual(item['empty_set_hex'],'c70f0093a86275696c74696e73a373657490')
        self.assertEqual(item['versions'],[True,False,False,True,False,False])

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

    def test_saved_pause_exception_is_explicit_and_exact(self):
        digest='a'*64
        item={**valid_gate(),'pending_controls':1,'preserved_pause':
              {'eligible':True,'fingerprint':p.PAUSED_FINGERPRINT,'state_sha256':digest}}
        with self.assertRaises(p.r.Stop): p.check_gate(item)
        self.assertEqual(p.check_gate(item,allow_saved_pause=True),digest)
        self.assertEqual(p.check_gate(item,allow_saved_pause=True,paused_digest=digest),digest)
        for changed in ({'pending_controls':0},{'pending_controls':2},{'active_runs':1},
                        {'latest_pending_writes':1},{'unknown_checkpoints':1},
                        {'preserved_pause':{}},
                        {'preserved_pause':{**item['preserved_pause'],'eligible':False}},
                        {'preserved_pause':{**item['preserved_pause'],'fingerprint':'different'}},
                        {'preserved_pause':{**item['preserved_pause'],'state_sha256':'b'*64}}):
            with self.subTest(changed=changed),self.assertRaises(p.r.Stop):
                p.check_gate({**item,**changed},allow_saved_pause=True,paused_digest=digest)
        with self.assertRaises(p.r.Stop): p.migration_sql('/tmp/fixture',paused_digest="'unsafe")

    def test_bad_rehearsal_stops_before_production_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            path.write_text(json.dumps({'status':'failed'}),encoding='utf-8')
            with self.assertRaises(p.r.Stop): p.checked_rehearsal(path)

    def test_report_discovery_validates_identity_instead_of_directory_spelling(self):
        checks={k:True for k in ('immutable_trigger_enabled','sequence_usage','execution_select','execution_insert','execution_update')}
        checks.update({k:0 for k in ('missing_sequence','duplicate_key_groups','duplicate_sequence_groups')})
        report={'status':'passed','cleanup':'passed','backup_manifest':p.r.MANIFEST.name,'image':p.r.IMAGE,
                'container':p.REHEARSAL_CONTAINER,'completed_at_utc':p.REHEARSAL_COMPLETED,
                'migration_sha256':{n:v['sha256'] for n,v in p.r.MIGRATIONS.items()},
                'lock_timeout':'5s','statement_timeout':'60s',
                'restored_before':{'history_rows':328,'ledger':p.r.OLD_LEDGER},
                'restored_after':{'ledger':p.r.NEW_LEDGER},
                'metrics':{'lock_window_upper_bound_ms':85.249,'transaction_elapsed_ms':87.304},
                'readonly_postchecks':checks}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def put(name,item):
                folder=root/name
                folder.mkdir()
                path=folder/'report.json'
                path.write_text(json.dumps(item),encoding='utf-8')
                return path
            actual=put('context-loop-20261002-y0pxiydv',report)
            put('context-loop-20261002-other-run',{**report,'container':'different'})
            put('context-loop-20261002-bad',{'status':'failed'})
            self.assertEqual(p.checked_rehearsal(root=root)['_evidence_path'],str(actual))
            duplicate=put('context-loop-20261002-duplicate',report)
            with self.assertRaises(p.r.Stop): p.checked_rehearsal(root=root)
            duplicate.unlink()
            actual.write_text(json.dumps({**report,'migration_sha256':{}}),encoding='utf-8')
            with self.assertRaises(p.r.Stop): p.checked_rehearsal(root=root)
            with self.assertRaises(p.r.Stop): p.checked_rehearsal(root=root/'missing')

    def test_network_probe_uses_only_counts(self):
        self.assertNotIn('environ',p.NETWORK_PROBE)
        self.assertNotIn('cmdline',p.NETWORK_PROBE)
        self.assertIn("int(fields[1].split(':')[1],16)==8001",p.NETWORK_PROBE)

    def test_standalone_payload_matches_reviewed_sources_and_shell_parses(self):
        here=Path(__file__).resolve().parent
        shell=(here/'gate3-production-migration.sh').read_text(encoding='utf-8')
        support=(here/'rehearsal_runner.py').read_text(encoding='utf-8').split('\ndef main():')[0]
        packed=shell.split('_packed = """\n',1)[1].split('\n"""',1)[0]
        source=gzip.decompress(base64.b64decode(packed)).decode()
        self.assertIn(repr(support),source)
        self.assertTrue(source.endswith((here/'production_runner.py').read_text(encoding='utf-8')))
        self.assertIn(hashlib.sha256(source.encode()).hexdigest(),shell)
        self.assertLess(max(map(len,shell.splitlines())),150)
        self.assertNotIn('\nset -',shell)
        # Syntax only; the script is never executed against a local or remote production name.
        p.r.run(['docker','run','--rm','--pull','never','--network','none','--entrypoint','sh',p.r.IMAGE,'-n'],data=shell.encode())

    def test_failed_command_does_not_exit_parent_login_shell(self):
        prefix=['docker','run','--rm','--pull','never','--network','none','--entrypoint','sh',p.r.IMAGE,'-c']
        old=p.r.run(prefix+['set -e; false; printf LOGIN_SURVIVED'],check=False)
        self.assertEqual(old.returncode,1)
        self.assertNotIn(b'LOGIN_SURVIVED',old.stdout)
        new=p.r.run(prefix+["set -e; if sh <<'CHILD'\nset -eu\nfalse\nCHILD\nthen :; else printf 'STOP\\n'; fi\nprintf LOGIN_SURVIVED"],check=False)
        self.assertEqual(new.returncode,0)
        self.assertIn(b'LOGIN_SURVIVED',new.stdout)

    def test_specific_stop_reason_is_visible_without_exception_contents(self):
        source=Path(p.__file__).read_text(encoding='utf-8').split("if __name__ == '__main__':",1)[1]
        source='if True:'+source
        for failure,expected in ((p.r.Stop('backup_not_verified'),'backup_not_verified'),
                                 (ValueError('sensitive fixture content'),'ValueError')):
            def fail(**kwargs): raise failure
            output=io.StringIO()
            with contextlib.redirect_stdout(output),patch.object(p.os,'umask'),self.assertRaises(SystemExit):
                exec(source,{'os':p.os,'r':p.r,'re':p.re,'subprocess':subprocess,'main':fail})
            self.assertIn(expected,output.getvalue())
            self.assertNotIn('sensitive fixture content',output.getvalue())


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

    def test_0_interruption_check_is_readonly_and_cleans_temp_profile(self):
        source=(Path(__file__).resolve().parent/'gate3-interrupted-readonly.sh').read_text(encoding='utf-8')
        body=source.split("<<'MF_STATE_CHECK'\n",1)[1].split('\nMF_STATE_CHECK\n',1)[0]
        before=self.source.metadata()
        result=self.source.command('sh','-s',data=body.encode())
        self.assertIn('001_agent_continuity.sql',result.stdout.decode())
        self.assertNotIn('003_context_loop_history.sql',result.stdout.decode())
        self.assertIn('ROLLBACK',result.stdout.decode())
        self.assertEqual(self.source.metadata(),before)
        remaining=self.source.command('find','/tmp','-maxdepth','1','-name','mf-state-check-*').stdout
        self.assertEqual(remaining,b'')

    def test_0_checkpoint_diagnostic_preserves_database_and_classifies_storage(self):
        shell=(Path(__file__).resolve().parent/'gate3-checkpoint-readonly.sh').read_text(encoding='utf-8')
        sql=shell.split("<<'MF_CP_SQL'\n",1)[1].split('\nMF_CP_SQL\n',1)[0]
        rows="""INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('diagnostic','','001','{"v":4,"channel_versions":{"branch:to:idle":"01","branch:to:ready":"01","join:a+b:done":"02"},"versions_seen":{"done":{"join:a+b:done":"01"}},"channel_values":{"branch:to:ready":null}}');
INSERT INTO checkpoint_blobs(thread_id,checkpoint_ns,channel,version,type,blob)
VALUES('diagnostic','','join:a+b:done','02','msgpack',decode('c70f0093a86275696c74696e73a373657490','hex'));
"""
        before={k:v for k,v in self.source.metadata().items() if k!='database_bytes'}
        # Run synthetic inserts and inspection within a rollback-only fixture transaction.
        output=self.source.sql(sql.replace('BEGIN READ ONLY;','BEGIN;\n'+rows)).stdout.decode()
        result=json.loads(next(line for line in output.splitlines() if line.startswith('{')))
        self.assertEqual(result['malformed_checkpoint_shapes'],0)
        self.assertEqual(result['missing_blob_without_inline'],1)
        self.assertEqual(result['missing_blob_with_inline'],1)
        self.assertEqual(result['inline_unseen_controls'],1)
        self.assertEqual(len(result['old_pending_candidates']),1)
        self.assertTrue(result['old_pending_candidates'][0]['known_empty_builtin_set'])
        self.assertEqual(result['old_pending_candidates'][0]['family'],'join')
        self.assertEqual({k:v for k,v in self.source.metadata().items() if k!='database_bytes'},before)
        body=shell.split("<<'MF_CP_DIAGNOSTIC'\n",1)[1].split('\nMF_CP_DIAGNOSTIC\n',1)[0]
        actual=self.source.command('sh','-s',data=body.encode()).stdout.decode()
        self.assertIn('"read_only": "on"',actual)
        self.assertEqual({k:v for k,v in self.source.metadata().items() if k!='database_bytes'},before)
        self.assertEqual(self.source.command('find','/tmp','-maxdepth','1','-name','mf-cp-check-*').stdout,b'')

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
        missing=query(branch+"DELETE FROM checkpoint_blobs;")
        self.assertEqual((missing['unknown_checkpoints'],missing['pending_controls']),(0,0))
        broken=query(branch+"UPDATE checkpoint_blobs SET blob=NULL;")
        self.assertEqual(broken['unknown_checkpoints'],1)
        progress="""INSERT INTO agent_history_event(event_id,task_id,session_id,event_type,event_timestamp,node,source,body)
VALUES('test-run-progress','fixture-task','fixture-session','run_progress',now(),'fixture','fixture',
'{"payload":{"run":{"run_id":"test-run","status":"running","pending_task_ids":["one"]}}}');"""
        state=query(progress)
        self.assertEqual((state['active_runs'],state['pending_runs']),(1,1))
        self.assertEqual(self.source.metadata()['history_rows'],328)

    def test_1_inline_none_empty_join_and_strict_versions(self):
        def query(checkpoint,blobs=''):
            data=json.dumps(checkpoint).replace("'","''")
            output=self.source.sql("BEGIN; INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint) "
                                   "VALUES('fixture','','001','"+data+"');"+blobs+p.GATE_QUERY+'ROLLBACK;').stdout.decode()
            return json.loads(next(l for l in output.splitlines() if l.startswith('{')))
        for current,seen,expected in (('02','01',1),('01','01',0),('01','02',0),(2,1,1),(1,1,0),(1,2,0)):
            checkpoint={'v':4,'channel_values':{'branch:to:agent':None},
                        'channel_versions':{'branch:to:agent':current},
                        'versions_seen':{'agent':{'branch:to:agent':seen}}}
            item=query(checkpoint)
            self.assertEqual((item['pending_controls'],item['unknown_checkpoints']),(expected,0))
        checkpoint={'v':4,'channel_values':{'branch:to:agent':None},
                    'channel_versions':{'branch:to:agent':'01'},'versions_seen':{}}
        self.assertEqual(query(checkpoint)['pending_controls'],1)
        for invalid in ({'agent':None},{'agent':{'branch:to:agent':1}}):
            self.assertEqual(query({**checkpoint,'versions_seen':invalid})['unknown_checkpoints'],1)
        empty_join="""INSERT INTO checkpoint_blobs(thread_id,checkpoint_ns,channel,version,type,blob)
VALUES('fixture','','join:a+b:done','02','msgpack',decode('c70f0093a86275696c74696e73a373657490','hex'));"""
        joined={'v':4,'channel_values':{},'channel_versions':{'join:a+b:done':'02'},
                'versions_seen':{'done':{'join:a+b:done':'01'}}}
        item=query(joined,empty_join)
        self.assertEqual((item['pending_controls'],item['unknown_checkpoints']),(0,0))
        # An unrecognised nonempty barrier is never silently declared quiet.
        self.assertEqual(query(joined,empty_join.replace('c70f0093a86275696c74696e73a373657490','91a161'))['pending_controls'],1)
        # A barrier with no named predecessors is available even for an empty set.
        empty_names={'v':4,'channel_values':{},'channel_versions':{'join::done':'02'},
                     'versions_seen':{'done':{'join::done':'01'}}}
        self.assertEqual(query(empty_names,empty_join.replace('join:a+b:done','join::done'))['pending_controls'],1)
        self.assertEqual(query({'channel_versions':[],'versions_seen':{}})['unknown_checkpoints'],1)

    def test_1_inline_diagnostic_matches_classifier_and_is_readonly(self):
        shell=(Path(__file__).resolve().parent/'gate3-inline-control-readonly.sh').read_text(encoding='utf-8')
        sql=shell.split("<<'MF_INLINE_SQL'\n",1)[1].split('\nMF_INLINE_SQL\n',1)[0]
        self.assertIn(p.CHECKPOINT_CTES,sql)
        data="""INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('fixture','','001','{"v":4,"channel_values":{"branch:to:agent":null},"channel_versions":{"branch:to:agent":"02"},"versions_seen":{"agent":{"branch:to:agent":"01"}}}');"""
        before={k:v for k,v in self.source.metadata().items() if k!='database_bytes'}
        result=self.source.sql(sql.replace('BEGIN READ ONLY;','BEGIN;'+data)).stdout.decode()
        item=json.loads(next(l for l in result.splitlines() if l.startswith('{')))
        self.assertEqual((item['pending_controls'],item['unknown_checkpoints']),(1,0))
        self.assertEqual(item['inline_controls'][0]['node_static_name'],'agent')
        self.assertTrue(item['inline_controls'][0]['pending'])
        self.assertTrue(item['inline_controls'][0]['namespace_is_root'])
        self.assertNotIn('thread_id',item['inline_controls'][0])
        body=shell.split("<<'MF_INLINE_DIAGNOSTIC'\n",1)[1].split('\nMF_INLINE_DIAGNOSTIC\n',1)[0]
        output=self.source.command('sh','-s',data=body.encode()).stdout.decode()
        self.assertIn('INLINE_CONTROL_READONLY_FINISHED; no_migration_or_resume',output)
        self.assertEqual({k:v for k,v in self.source.metadata().items() if k!='database_bytes'},before)
        self.assertEqual(self.source.command('find','/tmp','-maxdepth','1','-name','mf-inline-check-*').stdout,b'')

    def test_1_session_namespace_root_is_not_a_child(self):
        for namespace,root in (('fixture-session',True),('fixture-session|general_chat_subgraph:child',False)):
            sql="""BEGIN;
INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('fixture-conversation','"""+namespace+"""','001','{"v":4,"channel_values":{"branch:to:general_chat_subgraph":null},"channel_versions":{"branch:to:general_chat_subgraph":"01"},"versions_seen":{}}');
"""+p.CHECKPOINT_CTES+"SELECT jsonb_build_object('root',namespace_is_root,'pending',pending) FROM classified; ROLLBACK;"
            output=self.source.sql(sql).stdout.decode()
            item=json.loads(next(l for l in output.splitlines() if l.startswith('{')))
            self.assertEqual((item['root'],item['pending']),(root,True))

    def test_1_approved_ddl_preserves_paused_checkpoint_and_session(self):
        for name,raw in p.r.decoded_migrations().items():
            self.source.write(name,raw)
        # A real SQL transaction in a new isolated fixture. Never resume a graph.
        sql="""BEGIN;
INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('fixture-conversation','fixture-session','001','{"v":4,"ts":"2026-09-29T02:47:54.115908+00:00","channel_values":{"branch:to:general_chat_subgraph":null},"channel_versions":{"branch:to:general_chat_subgraph":"01"},"versions_seen":{}}');
CREATE TEMP TABLE preserved_state AS SELECT
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoints c) AS checkpoints,
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoint_blobs c) AS blobs,
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoint_writes c) AS writes,
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM agent_session c) AS sessions,
 (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM agent_task c) AS tasks;
"""
        for name in p.r.MIGRATIONS:
            sql+='\\i '+fixtures.r.INNER+'/'+name+'\n'
        sql+="""INSERT INTO agent_schema_migration(version) VALUES('003_context_loop_history.sql'),('004_continuity_execution.sql');
SELECT jsonb_build_object(
 'checkpoints_unchanged',checkpoints IS NOT DISTINCT FROM (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoints c),
 'blobs_unchanged',blobs IS NOT DISTINCT FROM (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoint_blobs c),
 'writes_unchanged',writes IS NOT DISTINCT FROM (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM checkpoint_writes c),
 'sessions_unchanged',sessions IS NOT DISTINCT FROM (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM agent_session c),
 'tasks_unchanged',tasks IS NOT DISTINCT FROM (SELECT jsonb_agg(to_jsonb(c) ORDER BY to_jsonb(c)::text) FROM agent_task c),
 'execution_exists',to_regclass('public.agent_execution') IS NOT NULL,
 'event_columns',(SELECT count(*) FROM information_schema.columns WHERE table_name='agent_history_event' AND column_name IN ('event_key','event_seq'))=2
) FROM preserved_state;
ROLLBACK;
"""
        output=self.source.sql(sql).stdout.decode()
        item=json.loads(next(l for l in output.splitlines() if l.startswith('{')))
        self.assertTrue(all(item.values()),item)
        self.assertEqual(self.source.metadata()['ledger'],p.r.OLD_LEDGER)

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
            self.source.sql("""INSERT INTO checkpoints(thread_id,checkpoint_ns,checkpoint_id,checkpoint)
VALUES('fixture-conversation','fixture-session','001','{"v":4,"ts":"2026-09-29T02:47:54.115908+00:00","channel_values":{"branch:to:general_chat_subgraph":null},"channel_versions":{"branch:to:general_chat_subgraph":"01"},"versions_seen":{}}');""")
            fixture_fingerprint=self.source.sql("SELECT md5(jsonb_build_array('fixture-conversation'::text,'fixture-session'::text,'001'::text)::text);").stdout.decode().strip()
            old_fingerprint=p.PAUSED_FINGERPRINT
            real_run=p.r.run
            def fixture_command(args,**kwargs):
                if args[:4]==['docker','exec','-i',p.AI]:
                    return subprocess.CompletedProcess(args,0,b'{"ai_listeners":1,"ai_inbound_established":0}',b'')
                return real_run(args,**kwargs)
            with tempfile.TemporaryDirectory() as root,patch.object(p,'REPORTS',Path(root)),\
                 patch.object(p.r,'checked_backup',return_value=({},Path(root))),\
                 patch.object(p,'checked_rehearsal',return_value={'_evidence_path':'fixture_verified_report.json'}),\
                 patch.object(p.r,'EXPECTED_DATABASE_BYTES',self.meta['database_bytes']),\
                 patch.object(p.os,'geteuid',return_value=0,create=True),\
                 patch.object(p.r,'run',side_effect=fixture_command),patch.object(p.time,'sleep'),\
                 patch.object(p,'PAUSED_FINGERPRINT',fixture_fingerprint),\
                 patch.object(p,'PAUSED_CTE',p.PAUSED_CTE.replace(old_fingerprint,fixture_fingerprint)),\
                 patch.object(p,'PAUSED_CHECK',p.PAUSED_CHECK.replace(old_fingerprint,fixture_fingerprint)),\
                 patch.object(p,'GATE_QUERY',p.GATE_QUERY.replace(old_fingerprint,fixture_fingerprint)):
                # Busy state stops before BEGIN READ WRITE, preserves schema and ledger,
                # and cleans only this attempt's temporary profile.
                output=io.StringIO()
                with patch.object(p.Production,'gate',return_value={**valid_gate(),'active_runs':1}),contextlib.redirect_stdout(output):
                    self.assertEqual(p.main(),1)
                stopped=json.loads(next(Path(root).glob('*/report.json')).read_text(encoding='utf-8'))
                self.assertFalse(stopped['production_transaction_attempted'])
                self.assertEqual(stopped['temporary_profile_cleanup'],'passed')
                self.assertEqual(db.ledger()['ledger'],p.r.OLD_LEDGER)
                # Without this request's explicit exception the same saved state stops.
                output=io.StringIO()
                with contextlib.redirect_stdout(output): self.assertEqual(p.main(),1)
                self.assertIn('gate_3_not_quiet_pending_controls_no_migration',output.getvalue())
                candidate=db.gate()['preserved_pause']
                self.assertTrue(candidate['eligible'])
                digest=candidate['state_sha256']
                # A change during DDL must roll back, including the injected change.
                changed_sql=p.migration_sql(fixtures.r.INNER,paused_digest=digest).replace(
                    '\\i '+fixtures.r.INNER+'/004_continuity_execution.sql',
                    "UPDATE checkpoints SET checkpoint=checkpoint || '{\"fixture_changed\":true}'::jsonb;\n"
                    '\\i '+fixtures.r.INNER+'/004_continuity_execution.sql')
                changed=db.sql(changed_sql,check=False)
                self.assertIn('PRESERVED_PAUSE_CHANGED_ROLLED_BACK',changed.stdout.decode())
                self.assertEqual(db.ledger()['ledger'],p.r.OLD_LEDGER)
                self.assertEqual(db.gate()['preserved_pause']['state_sha256'],digest)
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(p.main(allow_saved_pause=True),0)
                reports=[json.loads(f.read_text(encoding='utf-8')) for f in Path(root).glob('*/report.json')]
                passed=next(item for item in reports if item['status']=='passed')
                self.assertTrue(passed['production_commit_confirmed'])
                self.assertEqual(passed['temporary_profile_cleanup'],'passed')
                self.assertEqual(len(passed['gate_3_observations']),6)
                self.assertTrue(passed['preserved_pause_exception_approved'])
                self.assertTrue(passed['preserved_pause_unchanged'])
                self.assertEqual(passed['preserved_pause_state_sha256'],digest)
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
