import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const memory = new Map()
globalThis.sessionStorage = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => memory.set(key, value),
}

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const server = await createServer({
  root: projectRoot, appType: 'custom', logLevel: 'silent',
  server: { middlewareMode: true },
})
const originalFetch = globalThis.fetch

const initial = {
  root_entity: { entity_id: 'netease:song:1', entity_type: 'song', name: 'Feather' },
  graph: { root: 'netease:song:1', nodes: [
    { id: 'netease:song:1', type: 'song', name: 'Feather', description: '' },
    { id: 'netease:artist:2', type: 'artist', name: 'Nujabes', description: '' },
  ], edges: [] },
  narrative: '目录事实', branches: [{ id: 'wlb:1:server', title: 'Nujabes', description: '探索',
    branch_type: 'artist', target_entity: { id: 'netease:artist:2', type: 'artist', name: 'Nujabes' } }],
  sources: [], citations: [], session_id: 'server-session', current_node: 'netease:song:1',
  available_actions: [{ id: 'wlb:1:server', target_node_id: 'netease:artist:2',
    action_type: 'explore_node', enabled: true, disabled_reason: null }],
  exploration_depth: 0, revision: 1, outcome: null,
}
const expanded = {
  ...initial,
  graph: { ...initial.graph, nodes: [
    ...initial.graph.nodes,
    { id: 'netease:album:3', type: 'album', name: 'Modal Soul', description: '' },
  ] },
  current_node: 'netease:artist:2', exploration_depth: 1, revision: 2,
  branches: [], available_actions: [],
}
const requests = []
globalThis.fetch = async (url, options) => {
  requests.push({ url, body: JSON.parse(options.body) })
  return { ok: true, json: async () => requests.length === 1 ? initial : expanded }
}

try {
  const { useWorldLineStore } = await server.ssrLoadModule('/src/store/worldlineStore.ts')
  await useWorldLineStore.getState().createSession('Feather')
  assert.equal(useWorldLineStore.getState().result.session_id, 'server-session')
  assert.deepEqual(requests[0].body, { query: 'Feather' })
  await useWorldLineStore.getState().explore('unknown')
  assert.equal(requests.length, 1, 'unissued branch cannot call API')
  await useWorldLineStore.getState().explore('wlb:1:server')
  assert.deepEqual(requests[1].body, { session_id: 'server-session', branch_id: 'wlb:1:server' })
  assert.equal(useWorldLineStore.getState().result.graph.nodes.length, 3)
  assert.equal(JSON.parse(memory.get('moodflow-worldline-v21')).revision, 2)
  console.log('worldline frontend flow passed')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
