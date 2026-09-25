import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const server = await createServer({
  root: projectRoot, appType: 'custom', logLevel: 'silent',
  server: { middlewareMode: true },
})

const knowledge = {
  id: 'knowledge:rel:1', source_type: 'knowledge', title: 'Nujabes profile',
  source: 'Reviewed music archive', url: 'https://example.com/source',
  excerpt: 'Nujabes is associated with Jazz Hip-Hop.',
  metadata: { exact_span_start: 12, registry_version: 'v2' },
}
const edge = {
  source: 'netease:artist:2', target: 'genre:jazz_hip_hop', relation: 'associated_with',
  description: '', evidence_ids: [knowledge.id],
}
const eraEvidence = {
  id: 'knowledge:era:1', source_type: 'knowledge', title: 'Era profile',
  source: 'Reviewed archive', url: 'https://example.com/era',
  excerpt: 'Nujabes was active in the 2000s.', metadata: { claim_id: 'claim:era:1' },
}
const eraEdge = {
  source: 'netease:artist:2', target: 'era:2000s', relation: 'belongs_to_era',
  description: '', evidence_ids: [eraEvidence.id],
}
const eraRelation = {
  source_entity: { node_id: 'netease:artist:2', node_type: 'artist', canonical_name: 'Nujabes', knowledge_entity_id: 'artist:nujabes' },
  target_entity: null, era: { node_id: 'era:2000s', canonical_name: '2000s' },
  relation_type: 'belongs_to_era', start_year: 2003, end_year: 2003,
  temporal_precision: 'approximate', confidence: 0.88,
  evidence: [{ evidence_id: eraEvidence.id, source_id: eraEvidence.id, claim_id: 'claim:era:1', exact_span: eraEvidence.excerpt, start_year: 2003, end_year: 2003, raw_time: 'about 2003', confidence: 0.93 }],
  metadata: { temporal_conflict: false },
}
const sceneEvidence = {
  id: 'knowledge:scene:1', source_type: 'knowledge', title: 'Scene profile',
  source: 'Reviewed archive', url: 'https://example.com/scene',
  excerpt: 'Nujabes was part of the Tokyo hip-hop scene in 2003.',
  metadata: { claim_id: 'claim:scene:1' },
}
const sceneEdge = {
  source: 'netease:artist:2', target: 'scene:tokyo_hip_hop_scene',
  relation: 'belongs_to_scene', description: '', evidence_ids: [sceneEvidence.id],
}
const sceneRelation = {
  source_entity: { node_id: 'netease:artist:2', node_type: 'artist', canonical_name: 'Nujabes', knowledge_entity_id: 'artist:nujabes' },
  target_entity: null,
  scene: { node_id: 'scene:tokyo_hip_hop_scene', canonical_name: 'Tokyo hip-hop scene' },
  relation_type: 'belongs_to_scene', scene_name: 'Tokyo hip-hop scene',
  location: 'Tokyo', start_year: 2003, end_year: 2003,
  temporal_precision: 'approximate', confidence: 0.89,
  evidence: [{ evidence_id: sceneEvidence.id, source_id: sceneEvidence.id, claim_id: 'claim:scene:1', exact_span: sceneEvidence.excerpt, start_year: 2003, end_year: 2003, raw_time: 'about 2003', confidence: 0.94 }],
  metadata: { temporal_conflict: false, location_conflict: false },
}
const result = {
  root_entity: {
    entity_id: 'netease:artist:2', entity_type: 'artist', name: 'Nujabes', artist: null,
    album: null, source: 'netease', metadata: {},
  },
  graph: {
    root: 'netease:artist:2',
    nodes: [
      { id: 'netease:artist:2', type: 'artist', name: 'Nujabes', description: '', metadata: {} },
      { id: 'genre:jazz_hip_hop', type: 'genre', name: 'Jazz Hip-Hop', description: '', metadata: {} },
      { id: 'era:2000s', type: 'era', name: '2000s', description: '', metadata: { executable: false } },
      { id: 'scene:tokyo_hip_hop_scene', type: 'scene', name: 'Tokyo hip-hop scene', description: '', metadata: { executable: false } },
    ],
    edges: [edge, eraEdge, sceneEdge],
  },
  narrative: 'Nujabes 与 Jazz Hip-Hop 有关联。[knowledge:rel:1]',
  branches: [{
    id: 'wlb:1:genre', title: 'Jazz Hip-Hop', description: '查看流派', branch_type: 'genre',
    target_entity: { id: 'genre:jazz_hip_hop', type: 'genre', name: 'Jazz Hip-Hop' },
  }],
  sources: [knowledge, eraEvidence, sceneEvidence], citations: [knowledge],
  era_relations: [eraRelation], scene_relations: [sceneRelation], session_id: 'session',
  current_node: 'netease:artist:2', visited_nodes: ['netease:artist:2'],
  available_actions: [{
    id: 'wlb:1:genre', target_node_id: 'genre:jazz_hip_hop', action_type: 'explore_node',
    enabled: false, disabled_reason: 'genre_not_executable',
  }],
  exploration_depth: 0, revision: 1, outcome: null,
}

try {
  const [{ default: WorldLineGraph }, { default: EvidencePanel }, viewModel] = await Promise.all([
    server.ssrLoadModule('/src/components/worldline/WorldLineGraph.tsx'),
    server.ssrLoadModule('/src/components/worldline/WorldLineEvidencePanel.tsx'),
    server.ssrLoadModule('/src/components/worldline/worldlineViewModel.ts'),
  ])

  const graphMarkup = renderToStaticMarkup(React.createElement(WorldLineGraph, {
    result, selectedEdge: edge, pendingBranchId: null,
    onExplore: () => {}, onSelectEdge: () => {},
  }))
  assert.match(graphMarkup, /data-testid="worldline-graph"/)
  assert.match(graphMarkup, /data-node-type="artist"/)
  assert.match(graphMarkup, /data-node-type="genre"/)
  assert.match(graphMarkup, /data-node-type="era"/)
  assert.match(graphMarkup, /data-node-type="scene"/)
  assert.match(graphMarkup, /is-current/)
  assert.match(graphMarkup, /is-visited/)
  assert.match(graphMarkup, /aria-disabled="true"/)
  assert.match(graphMarkup, /associated with/)

  const mapped = viewModel.resolveEdgeEvidence(edge, result.sources, result.citations)
  assert.equal(mapped.length, 1)
  assert.equal(mapped[0].source.excerpt, knowledge.excerpt)
  assert.equal(mapped[0].cited, true)

  const evidenceMarkup = renderToStaticMarkup(React.createElement(EvidencePanel, {
    edge, nodes: result.graph.nodes, sources: result.sources, citations: result.citations,
    onClose: () => {},
  }))
  assert.match(evidenceMarkup, /Nujabes → Jazz Hip-Hop/)
  assert.match(evidenceMarkup, /Nujabes is associated with Jazz Hip-Hop\./)
  assert.match(evidenceMarkup, /narrative citation/)
  assert.match(evidenceMarkup, /exact_span_start/)

  const eraMarkup = renderToStaticMarkup(React.createElement(EvidencePanel, {
    edge: eraEdge, nodes: result.graph.nodes, sources: result.sources,
    citations: result.citations, eraRelations: result.era_relations,
    onClose: () => {},
  }))
  assert.match(eraMarkup, /Nujabes → 2000s/)
  assert.match(eraMarkup, /2003 · approximate/)
  assert.match(eraMarkup, /0\.88/)
  assert.match(eraMarkup, /Nujabes was active in the 2000s/)

  const sceneMarkup = renderToStaticMarkup(React.createElement(EvidencePanel, {
    edge: sceneEdge, nodes: result.graph.nodes, sources: result.sources,
    citations: result.citations, sceneRelations: result.scene_relations,
    onClose: () => {},
  }))
  assert.match(sceneMarkup, /Nujabes → Tokyo hip-hop scene/)
  assert.match(sceneMarkup, /<dt>Scene<\/dt><dd>Tokyo hip-hop scene<\/dd>/)
  assert.match(sceneMarkup, /<dt>Location<\/dt><dd>Tokyo<\/dd>/)
  assert.match(sceneMarkup, /2003 · approximate/)
  assert.match(sceneMarkup, /Nujabes was part of the Tokyo hip-hop scene/)

  const unavailableMarkup = renderToStaticMarkup(React.createElement(EvidencePanel, {
    edge: { ...edge, evidence_ids: ['missing'] }, nodes: result.graph.nodes,
    sources: result.sources, citations: result.citations, onClose: () => {},
  }))
  assert.match(unavailableMarkup, /证据暂不可用/)

  console.log('worldline frontend view passed')
} finally {
  await server.close()
}
