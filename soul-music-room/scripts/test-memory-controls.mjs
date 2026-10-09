import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'

const original = globalThis.fetch
class MockAudio {
  currentTime = 0
  duration = 180
  volume = 1
  addEventListener() {}
  pause() {}
  load() {}
  removeAttribute() {}
  async play() {}
}
globalThis.Audio = MockAudio
const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)),
  appType: 'custom', logLevel: 'silent', server: { middlewareMode: true } })
try {
  const api = await server.ssrLoadModule('/src/services/memory.ts')
  const requests = []
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options })
    assert.equal(options.credentials, 'include')
    return new Response(JSON.stringify({ enabled: false, generation: 1, items: [], next_cursor: null,
      preferences: { enabled: false, generation: 1 } }), { status: 200 })
  }
  await api.listMemories('memory:cursor')
  assert.match(requests[0].url, /after_id=memory%3Acursor/)
  await api.setMemoryEnabled(false)
  assert.deepEqual(JSON.parse(requests[1].options.body), { enabled: false })
  await api.correctMemory('memory:id', '我不喜欢现场版', 'request1')
  assert.equal(requests[2].options.method, 'PATCH')
  assert.deepEqual(JSON.parse(requests[2].options.body), { text: '我不喜欢现场版', request_id: 'request1' })
  await api.forgetMemory('memory:id')
  assert.equal(requests[3].options.method, 'DELETE')
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: '登录后才能管理记忆' }), { status: 401 })
  await assert.rejects(api.listMemories(), /登录后才能管理记忆/)
  globalThis.fetch = async () => new Response('invalid', { status: 503 })
  await assert.rejects(api.listMemories(), /记忆服务暂不可用/)
  globalThis.fetch = async () => new Response('null', { status: 200 })
  await assert.rejects(api.listMemories(), /无效响应/)
  const { default: Page } = await server.ssrLoadModule('/src/pages/MemoryPage.tsx')
  const { useAuthStore } = await server.ssrLoadModule('/src/store/authStore.ts')
  Object.assign(useAuthStore.getInitialState(), { loggedIn: false, initialized: true })
  const html = renderToStaticMarkup(createElement(MemoryRouter, null, createElement(Page)))
  assert.match(html, /登录后可以管理你的记忆/)
  assert.doesNotMatch(html, /确认忘记/)
  console.log('memory controls: request validation, mutations, errors and anonymous UI passed')
} finally {
  globalThis.fetch = original
  await server.close()
}
