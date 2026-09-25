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
    ],
    edges: [edge],
  },
  narrative: 'Nujabes 与 Jazz Hip-Hop 有关联。[knowledge:rel:1]',
  branches: [{
    id: 'wlb:1:genre', title: 'Jazz Hip-Hop', description: '查看流派', branch_type: 'genre',
    target_entity: { id: 'genre:jazz_hip_hop', type: 'genre', name: 'Jazz Hip-Hop' },
  }],
  sources: [knowledge], citations: [knowledge], session_id: 'session',
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

  const unavailableMarkup = renderToStaticMarkup(React.createElement(EvidencePanel, {
    edge: { ...edge, evidence_ids: ['missing'] }, nodes: result.graph.nodes,
    sources: result.sources, citations: result.citations, onClose: () => {},
  }))
  assert.match(unavailableMarkup, /证据暂不可用/)

  console.log('worldline frontend view passed')
} finally {
  await server.close()
}
