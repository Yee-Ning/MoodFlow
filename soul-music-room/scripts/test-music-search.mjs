import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'

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
const originalFetch = globalThis.fetch
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)), appType: 'custom', logLevel: 'silent',
  server: { middlewareMode: true },
})
const entity = (type, id = 101, name = '晴天') => ({
  id, entityType: type, name, artist: '周杰伦', album: '叶惠美', coverUrl: '',
  metadata: { duration: 269000, track_count: 12 },
  relatedEntities: [{ id: 6452, entityType: 'artist', name: '周杰伦', artist: '周杰伦', album: '' }],
})
const response = (data, status = 200, message = 'success') => new Response(
  JSON.stringify({ code: status, data, message }), { status },
)
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
async function ready(store) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (store.getState().status !== 'loading') return
    await new Promise((resolve) => setImmediate(resolve))
  }
  assert.fail('search request did not finish')
}

try {
  const api = await server.ssrLoadModule('/src/api/musicSearch.ts')
  const { useSearchStore: store } = await server.ssrLoadModule('/src/store/searchStore.ts')
  // Hydrate Zustand's SSR snapshot with the state already loaded by the real
  // search action so server-rendered UI assertions reflect client rendering.
  const renderSearch = (path) => {
    Object.assign(store.getInitialState(), store.getState())
    return renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [path] }, createElement(SearchPage)))
  }
  const { default: SearchPage } = await server.ssrLoadModule('/src/pages/SearchPage.tsx')
  const { default: TopBar } = await server.ssrLoadModule('/src/components/layout/TopBar.tsx')
  const { usePlayerStore: player } = await server.ssrLoadModule('/src/store/playerStore.ts')
  const { BackendApiError } = await server.ssrLoadModule('/src/api/client.ts')
  const seen = []
  globalThis.fetch = async (url, options) => {
    const target = new URL(url)
    seen.push(target)
    assert.equal(options.credentials, 'include')
    assert.equal(target.pathname, '/api/music/entities/search')
    return response([entity(target.searchParams.get('type'))])
  }
  const controller = new AbortController()
  const query = '  周杰伦 & Kanye/West  '
  await api.searchEntities(query, 'song', 8, controller.signal)
  assert.equal(seen[0].searchParams.get('keyword'), query.trim())
  assert.equal(seen[0].searchParams.get('type'), 'song')
  assert.equal(seen[0].searchParams.get('limit'), '8')
  const link = new URL(api.searchUrl(query, 'album'), 'https://example.test')
  assert.equal(link.searchParams.get('q'), query.trim())
  assert.equal(link.searchParams.get('type'), 'album')
  assert.equal(api.searchUrl('  '), '/search')
  assert.equal(api.parseSearchCategory('unknown'), 'all')
  for (const type of api.ENTITY_TYPES) assert.equal(api.parseSearchCategory(type), type)

  const count = seen.length
  for (const [keyword, type, limit] of [['', 'song', 1], [' '.repeat(5), 'song', 1],
    ['x'.repeat(201), 'song', 1], ['x', 'invalid', 1], ['x', 'song', 0], ['x', 'song', 51], ['x', 'song', 1.5]]) {
    await assert.rejects(api.searchEntities(keyword, type, limit), BackendApiError)
  }
  assert.equal(seen.length, count, 'invalid arguments must not reach the backend')
  store.getState().loadSearch('  ', 'all')
  assert.equal(store.getState().status, 'idle')
  assert.equal(seen.length, count, 'empty search must not make a network request')

  store.getState().loadSearch('周杰伦', 'all')
  await ready(store)
  assert.equal(seen.length, count + 4)
  for (const type of api.ENTITY_TYPES) assert.equal(store.getState().results[type][0].entityType, type)
  store.getState().loadSearch('周杰伦', 'song')
  await ready(store)
  assert.equal(seen.at(-1).searchParams.get('limit'), '50')
  assert.equal(store.getState().results.artist.length, 0)

  const song = api.songFromEntity(entity('song'))
  assert.deepEqual(song, { id: 101, name: '晴天', artists: ['周杰伦'], album: '叶惠美', coverUrl: '', duration: 269000 })
  assert.equal(api.songFromEntity({ ...entity('song'), metadata: { duration: 'bad' } }).duration, 0)
  assert.throws(() => api.songFromEntity(entity('album')), BackendApiError)
  player.getState().appendToQueue(song)
  assert.equal(player.getState().queue[0].id, 101)
  assert.equal(player.getState().isPlaying, false, 'adding a search result must not interrupt playback')

  // Provider regression: a real song with no album must survive the 50-row category request.
  const unreleased = { ...entity('song', 2148728095, 'EVERYBODY'), album: '', metadata: { duration: 180000 } }
  globalThis.fetch = async () => response(Array.from({ length: 50 }, (_, index) =>
    index === 14 ? unreleased : entity('song', index + 1000, `Track ${index}`)))
  for (const query of ['kanye', 'Kanye West']) {
    store.getState().loadSearch(query, 'song')
    await ready(store)
    assert.equal(store.getState().results.song.length, 50)
    assert.equal(Object.keys(store.getState().errors).length, 0)
    markupForMissingAlbum(query)
  }
  function markupForMissingAlbum(query) {
    const markup = renderSearch(api.searchUrl(query, 'song'))
    assert.match(markup, /50 条结果/)
    assert.match(markup, /EVERYBODY/)
    assert.match(markup, /未知专辑/)
    assert.doesNotMatch(markup, /暂时无法搜索/)
  }

  globalThis.fetch = async (url) => {
    const type = new URL(url).searchParams.get('type')
    return type === 'album' ? response(null, 503, '上游服务暂时不可用') : response([entity(type)])
  }
  store.getState().loadSearch('周杰伦', 'all')
  await ready(store)
  assert.equal(store.getState().results.song.length, 1)
  assert.match(store.getState().errors.album, /上游服务/)
  assert.equal(store.getState().results.playlist.length, 1, 'one category failure must not hide other categories')

  globalThis.fetch = async () => response(null, 503, '暂时不可用')
  store.getState().loadSearch('周杰伦', 'all')
  await ready(store)
  assert.equal(Object.keys(store.getState().errors).length, 4)
  let markup = renderSearch('/search?q=周杰伦')
  assert.match(markup, /重试搜索/)
  assert.match(markup, /暂时无法搜索/)
  globalThis.fetch = async () => response([])
  store.getState().loadSearch('不存在的关键词', 'all')
  await ready(store)
  markup = renderSearch('/search?q=不存在的关键词')
  assert.match(markup, /没有找到相关音乐/)
  assert.equal(Object.keys(store.getState().errors).length, 0)

  const old = deferred()
  let oldSignal
  globalThis.fetch = async (url, options) => {
    if (new URL(url).searchParams.get('keyword') === 'old') { oldSignal = options.signal; return old.promise }
    return response([entity('song', 202, 'new')])
  }
  store.getState().loadSearch('old', 'song')
  store.getState().loadSearch('new', 'song')
  await ready(store)
  assert.equal(oldSignal.aborted, true)
  old.resolve(response([entity('song', 303, 'old')]))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(store.getState().results.song[0].name, 'new', 'late previous results must never overwrite the latest search')

  const cancelled = deferred()
  globalThis.fetch = async () => cancelled.promise
  const cancel = store.getState().loadSearch('cancelled', 'song')
  cancel()
  store.getState().loadSearch('', 'all')
  cancelled.resolve(response([entity('song')]))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(store.getState().status, 'idle')
  assert.equal(store.getState().results.song.length, 0)

  // The page effect owns retries as well as initial requests and cancels the active request on exit.
  const retry = deferred()
  let retrySignal
  globalThis.fetch = async (_url, options) => { retrySignal = options.signal; return retry.promise }
  const cancelRetry = store.getState().loadSearch('kanye', 'song')
  cancelRetry()
  retry.resolve(response([unreleased]))
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(retrySignal.aborted, true)
  assert.equal(store.getState().results.song.length, 0, 'cancelled retry must not apply late results')

  for (const bad of [null, {}, [{ ...entity('song'), id: -1 }], [entity('album')],
    [{ ...entity('song'), metadata: null }], [{ ...entity('song'), relatedEntities: [null] }],
    ...[{ id: 0 }, { name: '' }, { entityType: 'fake' }, { artist: null }].map((fields) =>
      [{ ...entity('song'), relatedEntities: [{ ...entity('song').relatedEntities[0], ...fields }] }])]) {
    globalThis.fetch = async () => response(bad)
    await assert.rejects(api.searchEntities('x', 'song', 1), /无效的实体/)
  }
  globalThis.fetch = async () => { throw new Error('offline') }
  await assert.rejects(api.searchEntities('x', 'song', 1), /无法连接/)
  globalThis.fetch = async () => new Response('invalid JSON')
  await assert.rejects(api.searchEntities('x', 'song', 1), /无效响应/)
  globalThis.fetch = async () => response(null, 401, '请先登录')
  await assert.rejects(api.searchEntities('x', 'song', 1), (error) => error.status === 401)

  globalThis.fetch = async (url) => {
    assert.equal(new URL(url).pathname, '/api/albums/101')
    return response({ album: entity('album'), songs: [song] })
  }
  assert.equal((await api.getAlbumDetail(101)).songs[0].id, 101)
  await assert.rejects(api.getAlbumDetail(0), /专辑地址无效/)
  for (const bad of [{ album: entity('album', 999), songs: [song] }, { album: entity('album'), songs: null },
    { album: entity('album'), songs: [{ ...song, id: -1 }] },
    { album: { ...entity('album'), name: '' }, songs: [song] },
    { album: { ...entity('album'), relatedEntities: [null] }, songs: [song] }]) {
    globalThis.fetch = async () => response(bad)
    await assert.rejects(api.getAlbumDetail(101), /不匹配/)
  }

  const escapedName = '<script>晴天</script>'
  globalThis.fetch = async () => response([entity('song', 101, escapedName)])
  store.getState().loadSearch('周杰伦', 'song')
  await ready(store)
  markup = renderSearch('/search?q=周杰伦&type=song')
  assert.match(markup, /播放 &lt;script&gt;晴天&lt;\/script&gt;/)
  assert.match(markup, /加入队列/)
  assert.doesNotMatch(markup, /Static preview|静态搜索入口|<script>晴天/)
  markup = renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: ['/search?q=Kanye%20West&type=artist'] }, createElement(TopBar)))
  assert.match(markup, /value="Kanye West"/)
  assert.match(markup, /提交音乐搜索/)
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8')
  assert.match(app, /path="\/search" element=\{<SearchPage \/>\}/)
  assert.doesNotMatch(app, /StaticDestinationPage page="search"/)
  const page = await readFile(new URL('../src/pages/SearchPage.tsx', import.meta.url), 'utf8')
  assert.match(page, /useEffect\(\(\) => loadSearch\(query, category\), \[query, category, loadSearch, searchAttempt\]\)/)
  assert.match(page, /onClick=\{\(\) => setSearchAttempt\(/)
  assert.doesNotMatch(page, /search\.loadSearch\(query, category\)/, 'retry must retain the effect cleanup')
  console.log('Music search API, categories, rendering, queue, validation, partial failures, retry and cancellation checks passed.')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
