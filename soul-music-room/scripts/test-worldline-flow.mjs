import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const memory = new Map()
globalThis.sessionStorage = {
  getItem: (key) => memory.get(key) ?? null,
  setItem: (key, value) => memory.set(key, value),
  removeItem: (key) => memory.delete(key),
}

const source = {
  id: 'music-api:song:1', source_type: 'music_api', title: 'Feather catalog',
  source: 'MoodFlow Music Service', url: null, excerpt: null, metadata: {},
}
const initial = {
  root_entity: {
    entity_id: 'netease:song:1', entity_type: 'song', name: 'Feather', artist: 'Nujabes',
    album: 'Modal Soul', source: 'netease', metadata: {},
  },
  graph: {
    root: 'netease:song:1',
    nodes: [
      { id: 'netease:song:1', type: 'song', name: 'Feather', description: '', metadata: {} },
      { id: 'netease:artist:2', type: 'artist', name: 'Nujabes', description: '', metadata: {} },
    ],
    edges: [{
      source: 'netease:song:1', target: 'netease:artist:2', relation: 'performed_by',
      description: '', evidence_ids: [source.id],
    }],
  },
  narrative: '目录事实',
  branches: [{
    id: 'wlb:1:artist', title: 'Nujabes', description: '探索', branch_type: 'artist',
    target_entity: { id: 'netease:artist:2', type: 'artist', name: 'Nujabes' },
  }],
  sources: [source], citations: [], session_id: 'server-session',
  current_node: 'netease:song:1', visited_nodes: ['netease:song:1'],
  available_actions: [{
    id: 'wlb:1:artist', target_node_id: 'netease:artist:2', action_type: 'explore_node',
    enabled: true, disabled_reason: null,
  }],
  exploration_depth: 0, revision: 1, outcome: null,
}
const expanded = {
  ...initial,
  graph: {
    ...initial.graph,
    nodes: [
      ...initial.graph.nodes,
      { id: 'netease:album:3', type: 'album', name: 'Modal Soul', description: '', metadata: {} },
    ],
  },
  current_node: 'netease:artist:2',
  visited_nodes: ['netease:song:1', 'netease:artist:2'],
  exploration_depth: 1,
  revision: 2,
  branches: [{
    id: 'wlb:2:album', title: 'Modal Soul', description: '探索', branch_type: 'album',
    target_entity: { id: 'netease:album:3', type: 'album', name: 'Modal Soul' },
  }],
  available_actions: [{
    id: 'wlb:2:album', target_node_id: 'netease:album:3', action_type: 'explore_node',
    enabled: true, disabled_reason: null,
  }],
}

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const server = await createServer({
  root: projectRoot, appType: 'custom', logLevel: 'silent',
  server: { middlewareMode: true },
})
const originalFetch = globalThis.fetch
const requests = []
let mode = 'normal'
let resolveExplore

globalThis.fetch = async (url, options = {}) => {
  requests.push({
    url: String(url),
    method: options.method ?? 'GET',
    body: options.body ? JSON.parse(options.body) : null,
  })
  if (mode === 'deferred-explore' && String(url).endsWith('/worldline/explore')) {
    return new Promise((resolve) => { resolveExplore = resolve })
  }
  if (mode === 'explore-failure' && String(url).endsWith('/worldline/explore')) {
    return {
      ok: false, status: 503,
      json: async () => ({ detail: { code: 'catalog_unavailable', message: '探索服务暂不可用。' } }),
    }
  }
  if (mode === 'expired' && options.method === undefined) {
    return {
      ok: false, status: 404,
      json: async () => ({ detail: { code: 'session_not_found', message: '会话不存在。' } }),
    }
  }
  const payload = options.method === undefined ? expanded : initial
  return { ok: true, status: 200, json: async () => payload }
}

try {
  const { useWorldLineStore } = await server.ssrLoadModule('/src/store/worldlineStore.ts')

  await useWorldLineStore.getState().createSession('Feather')
  assert.equal(useWorldLineStore.getState().result.session_id, 'server-session')
  assert.deepEqual(requests[0].body, { query: 'Feather' })
  assert.equal(memory.get('moodflow-worldline-session'), 'server-session')
  assert.equal(memory.get('moodflow-worldline-v21'), undefined, 'full result must not be persisted')

  await useWorldLineStore.getState().explore('unknown')
  assert.equal(requests.length, 1, 'unissued branch cannot call API')

  mode = 'deferred-explore'
  const firstClick = useWorldLineStore.getState().explore('wlb:1:artist')
  const duplicateClick = useWorldLineStore.getState().explore('wlb:1:artist')
  assert.equal(requests.length, 2, 'duplicate click must create only one explore request')
  assert.deepEqual(requests[1].body, {
    session_id: 'server-session', branch_id: 'wlb:1:artist',
  })
  resolveExplore({ ok: true, status: 200, json: async () => expanded })
  await Promise.all([firstClick, duplicateClick])
  assert.equal(useWorldLineStore.getState().result.graph.nodes.length, 3)

  mode = 'explore-failure'
  const beforeFailure = useWorldLineStore.getState().result
  await useWorldLineStore.getState().explore('wlb:2:album')
  assert.equal(useWorldLineStore.getState().result, beforeFailure, 'failed explore must preserve graph object')
  assert.equal(useWorldLineStore.getState().result.revision, 2)
  assert.equal(useWorldLineStore.getState().error, '探索服务暂不可用。')

  mode = 'normal'
  useWorldLineStore.setState({
    result: null, sessionId: 'server-session', status: 'idle', loading: false,
    operation: null, pendingBranchId: null, error: '',
  })
  await useWorldLineStore.getState().recoverSession()
  const recoveryRequest = requests.at(-1)
  assert.equal(recoveryRequest.method, 'GET')
  assert.match(recoveryRequest.url, /worldline\/sessions\/server-session$/)
  assert.deepEqual(useWorldLineStore.getState().result.visited_nodes, expanded.visited_nodes)
  assert.equal(useWorldLineStore.getState().result.current_node, expanded.current_node)

  mode = 'expired'
  memory.set('moodflow-worldline-session', 'expired-session')
  useWorldLineStore.setState({
    result: null, sessionId: 'expired-session', status: 'idle', loading: false,
    operation: null, pendingBranchId: null, error: '',
  })
  await useWorldLineStore.getState().recoverSession()
  assert.equal(useWorldLineStore.getState().status, 'expired')
  assert.equal(useWorldLineStore.getState().sessionId, null)
  assert.equal(memory.has('moodflow-worldline-session'), false)

  console.log('worldline frontend flow passed')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
