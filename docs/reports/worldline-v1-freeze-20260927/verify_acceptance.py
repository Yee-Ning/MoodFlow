"""Read-only verification of the captured final Worldline acceptance artifacts.

Run from ai-service using its virtualenv; never creates sessions or changes data.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'ai-service'))
from app.agent.foundation.subgraphs.worldline.models import WorldLineSessionResponse
from app.rag.source_context import RelationSourceContext

OUT = Path(__file__).resolve().parent
def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))
def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')

responses = [read(n) for n in ['create_response.json', 'explore_before_restart_response.json',
                              'recover_response.json', 'continuation_response.json']]
for response in responses:
    WorldLineSessionResponse.model_validate(response)
assert [r['revision'] for r in responses] == [1, 2, 2, 3]
assert len({r['session_id'] for r in responses}) == 1
created, before, recovered, continued = responses
assert before == recovered
for key in ['scene_relations', 'influence_relations']:
    assert len(created[key]) >= 1
    assert created[key] == before[key] == recovered[key] == continued[key]
old_nodes = {n['id']: n for n in before['graph']['nodes']}
new_nodes = {n['id']: n for n in continued['graph']['nodes']}
assert all(new_nodes[k] == v for k, v in old_nodes.items())
def edge_key(edge):
    return edge['source'], edge['target'], edge['relation']
new_edges = {edge_key(e): e for e in continued['graph']['edges']}
for edge in before['graph']['edges']:
    new = new_edges[edge_key(edge)]
    assert set(edge['evidence_ids']) <= set(new['evidence_ids'])
old_sources = {s['id']: s for s in before['sources']}
new_sources = {s['id']: s for s in continued['sources']}
assert all(new_sources[k] == v for k, v in old_sources.items())
assert set(before['visited_nodes']) <= set(continued['visited_nodes'])
influence = continued['influence_relations'][0]
assert influence['source_entity']['knowledge_entity_id'] == 'artist:pixies'
assert influence['target_entity']['knowledge_entity_id'] == 'artist:nirvana'
assert influence['direction'] == 'influencer_to_influenced'
assert influence['evidence'][0]['stance'] == 'support'
for node_id in ['artist:nirvana', 'scene:grunge_rock_scene']:
    metadata = new_nodes[node_id]['metadata']
    assert metadata['canonical_id'] == node_id
    assert metadata['knowledge_entity_id'] == node_id
    assert metadata['executable'] is False
    assert 'catalog_reference' not in metadata

pg = [read(n) for n in ['postgres_before_stop.json', 'postgres_process_stopped.json',
                       'postgres_after_recover.json', 'postgres_after_continuation.json']]
assert all(p['read_only'] and p['checkpoint_backend'] == 'postgres' for p in pg)
assert [p['checkpoint_count'] for p in pg] == [6, 6, 6, 9]
assert [p['session_state']['revision'] for p in pg] == [2, 2, 2, 3]
assert len({p['postgres_started_at'] for p in pg}) == 1
for key in ['postgres_session_storage_sha256', 'session_state_sha256']:
    assert len({p[key] for p in pg[:3]}) == 1
assert pg[2]['session_state'] == pg[0]['session_state']
for key in ['visited_nodes', 'consumed_branch_ids']:
    assert set(pg[0]['session_state'][key]) <= set(pg[3]['session_state'][key])
assert read('api_fully_stopped.json') == {'port_8001_listening': False, 'api_python_process_count': 0}

traces = [[json.loads(line) for line in (OUT / name).read_text(encoding='utf-8').splitlines()]
          for name in ['online_trace_before_restart.jsonl', 'online_trace_after_restart.jsonl']]
assert all(r['observer_error_count'] == 0 for rows in traces for r in rows)
assert traces[0][0]['pid'] != traces[1][0]['pid']
commits = [r for rows in traces for r in rows if r['stage'].endswith('WorldLineSessionService._commit')]
assert [r['payload']['committed_state']['revision'] for r in commits] == [1, 2, 3]
assert all(r['payload']['checkpointer_class'] == 'AsyncCompatiblePostgresSaver' for r in commits)
providers = [dict(pid=r['pid'], at=r['at'], **{k: r['payload'].get(k) for k in
             ['model', 'schema_name', 'latency_ms', 'status', 'response_id']})
             for rows in traces for r in rows if r['stage'].endswith(':_create_response')]
assert len(providers) >= 6
assert all(p['model'] == 'deepseek-flash' and p['status'] == 'completed' for p in providers)
write('provider_generation_evidence.json', providers)

retrievals = [r['payload'] for rows in traces for r in rows
              if r['stage'].endswith('HybridRetrievalService.retrieve')]
native_retrieval = next(p for p in retrievals if p['return']['candidates'])
assert native_retrieval['backend_class'] == 'QdrantHybridSearchBackend'
assert native_retrieval['encoder_class'] == 'QwenQueryEncoder'
assert native_retrieval['return']['status'] == 'success'
manifest_path = ROOT / 'ai-service/data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json'
assert Path(native_retrieval['bound_manifest']).resolve() == manifest_path.resolve()
assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == '445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3'
assert {e['channel'] for c in native_retrieval['return']['candidates'] for e in c['channel_evidence']} == {'dense', 'lexical'}
projection = [json.loads(line) for line in (manifest_path.parent / 'index_projection.jsonl').read_text(encoding='utf-8').splitlines()]
documents = next(r['payload']['return'] for r in traces[0]
                 if r['stage'].endswith(':_pack_documents') and r['payload']['return'])
bindings = []
for relation in created['scene_relations'] + created['influence_relations']:
    for evidence in relation['evidence']:
        source = old_sources[evidence['source_id']]
        metadata = source['metadata']
        document = next(d for d in documents if d['metadata']['chunk_id'] == metadata['source_chunk_id'])
        claim = next(c for c in document['metadata']['supported_claims'] if c['claim_id'] == evidence['claim_id'])
        projected = next(p for p in projection if p['chunk_id'] == claim['source_chunk_id'])
        assert claim in projected['payload']['supported_claims']
        assert source['excerpt'] == evidence['exact_span'] == claim['evidence_span']
        assert evidence['exact_span'] in projected['payload']['content']
        assert hashlib.sha256(projected['payload']['content'].encode()).hexdigest() == metadata['source_content_sha256']
        assert metadata['source_content_sha256'] == projected['payload']['source_content_sha256']
        assert metadata['source_id'] == projected['payload']['source_id']
        assert all(v == 'SUPPORTED' for v in projected['payload']['verification_statuses'])
        assert metadata['source_release_id'] == native_retrieval['index_manifest']['source_release_id']
        context = metadata['relation_source_context']
        RelationSourceContext.model_validate(context)
        assert context in document['metadata']['relation_source_contexts']
        assert context['claim_id'] == evidence['claim_id']
        assert context['response_sha256'] == projected['payload']['source_response_sha256']
        assert context['source_url'] == source['url']
        assert context['publisher'] == source['source'] and context['title'] == source['title']
        assert hashlib.sha256(context['content'].encode()).hexdigest() == context['content_sha256']
        anchor = context['anchor']
        assert context['content'][anchor['start']:anchor['end']] == anchor['exact_span'] == evidence['exact_span']
        for segment in context['segments']:
            assert context['content'][segment['start']:segment['end']] == segment['exact_span']
        bindings.append(dict(relation_type=relation['relation_type'], evidence=evidence,
                             source=source, grounding=claim.get('grounding'),
                             grounding_present='grounding' in claim, source_context_anchor=anchor))
assert '“Grunge-rock”' in bindings[0]['evidence']['exact_span']
assert any(r['stage'].endswith('SceneExpansionService.expand') and r['payload']['return']['relations'] for r in traces[0])
assert any(r['stage'].endswith('InfluenceExpansionService.expand') and r['payload']['return']['relations'] for r in traces[0])
for name in ['frontend_network_before.json', 'frontend_network_after.json']:
    network = read(name)
    assert all(e['status'] == 200 for e in network if e.get('status') is not None)
dom = (OUT / 'frontend_revision3_dom.txt').read_text(encoding='utf-8')
assert 'emerged_from_scene 证据' in dom and 'influenced 证据' in dom
assert 'checkpoint synced' in dom
write('retrieval_binding_evidence.json', dict(manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    release_id=native_retrieval['index_manifest']['source_release_id'],
    collection=native_retrieval['index_manifest']['collection_name'], candidates=len(native_retrieval['return']['candidates']),
    channels=['dense', 'lexical'], bindings=bindings))
flags = dict(runtime_entity_binding_pass=True, scene_relation_online_pass=True, influence_relation_online_pass=True,
             postgres_restart_acceptance=True, frontend_contract_pass=True, online_e2e_pass=True,
             session_recovery_pass=True, retrieval_runtime_pass=True, provider_generation_pass=True,
             worldline_v1_freeze_ready=True)
write('acceptance_evidence.json', dict(flags=flags, session_id=created['session_id'],
    revisions=[r['revision'] for r in responses], graph_sizes=[dict(nodes=len(r['graph']['nodes']),edges=len(r['graph']['edges'])) for r in responses],
    checkpoint_counts=[p['checkpoint_count'] for p in pg], before_recover_exact_match=True,
    preserved_nodes=len(old_nodes), preserved_edges=len(before['graph']['edges']), preserved_sources=len(old_sources),
    preserved_scene_relations=len(before['scene_relations']), preserved_influence_relations=len(before['influence_relations']),
    era_relation_count=len(created['era_relations']), api_process_ids=[rows[0]['pid'] for rows in traces],
    model_generation_count=len(providers), code_baseline_commit='7afc619'))
print(json.dumps(flags))
