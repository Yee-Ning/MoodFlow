import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

class MockAudio {
  currentTime = 0
  duration = 180
  src = ''
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
function response(data, status = 200, message = 'success') {
  return new Response(JSON.stringify({ code: status, message, data }), { status })
}
function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}
try {
  const { usePlayerStore: player } = await server.ssrLoadModule('/src/store/playerStore.ts')
  const { executeClientAction } = await server.ssrLoadModule('/src/services/aiClientActions.ts')
  const { registerSongLikeSyncAdapter } = await server.ssrLoadModule('/src/services/songLikes.ts')
  const { useAuthStore: auth } = await server.ssrLoadModule('/src/store/authStore.ts')
  player.getState().resetLikedSongs()
  const writes = []
  globalThis.fetch = async (url, init) => {
    const target = new URL(url)
    assert.equal(target.pathname, '/api/songs/101/like')
    assert.equal(init.method, 'POST')
    assert.equal(init.credentials, 'include')
    const liked = target.searchParams.get('liked') === 'true'
    writes.push(liked)
    return response({ songId: 101, liked, status: 'synced', message: 'synced' })
  }
  await player.getState().setLiked(101, true)
  assert.equal(player.getState().likedSongIds.has(101), true)
  await player.getState().setLiked(101, false)
  assert.equal(player.getState().likedSongIds.has(101), false)
  assert.deepEqual(writes, [true, false])

  globalThis.fetch = async () => response(null, 401, '请先登录网易云音乐')
  await player.getState().setLiked(101, true)
  assert.equal(player.getState().likedSongIds.has(101), false, 'failed writes must not paint a false red heart')
  assert.match(player.getState().notice, /请先登录/)
  player.getState().applyLikedState(101, true)
  await player.getState().setLiked(101, false)
  assert.equal(player.getState().likedSongIds.has(101), true, 'failed unlike preserves confirmed state')

  globalThis.fetch = async () => response({ songId: 999, liked: true, status: 'synced' })
  await player.getState().setLiked(202, true)
  assert.equal(player.getState().likedSongIds.has(202), false)
  assert.match(player.getState().notice, /不匹配/)

  let calls = 0
  globalThis.fetch = async () => { calls += 1; throw new Error('AI actions must not write remotely') }
  const action = { action_id: 'cloud-like-result', task_id: 'cloud-task', name: 'set_liked',
    arguments: { song_id: 202, liked: true } }
  await executeClientAction(action)
  await executeClientAction(action)
  assert.equal(player.getState().likedSongIds.has(202), true)
  assert.equal(calls, 0, 'synced client actions only update local state')

  globalThis.fetch = async (url) => {
    assert.equal(new URL(url).pathname, '/api/user/liked-songs')
    return response([303, 404])
  }
  await player.getState().refreshLikedSongs()
  assert.deepEqual([...player.getState().likedSongIds], [303, 404])

  const started = deferred()
  const firstWrite = deferred()
  const order = []
  registerSongLikeSyncAdapter(async (_id, liked) => {
    order.push(liked)
    if (liked) { started.resolve(); await firstWrite.promise }
  })
  const like = player.getState().setLiked(505, true)
  const unlike = player.getState().setLiked(505, false)
  await started.promise
  assert.deepEqual(order, [true])
  firstWrite.resolve()
  await Promise.all([like, unlike])
  assert.deepEqual(order, [true, false], 'same song mutations run in order')
  assert.equal(player.getState().likedSongIds.has(505), false)
  registerSongLikeSyncAdapter(null)

  const oldRead = deferred()
  globalThis.fetch = async () => oldRead.promise
  const read = player.getState().refreshLikedSongs()
  player.getState().applyLikedState(606, true)
  oldRead.resolve(response([303]))
  await read
  assert.equal(player.getState().likedSongIds.has(606), true, 'late hydration must not overwrite an applied action')

  const staleRead = deferred()
  globalThis.fetch = async () => staleRead.promise
  const beforeLogout = player.getState().refreshLikedSongs()
  player.getState().resetLikedSongs()
  staleRead.resolve(response([777]))
  await beforeLogout
  assert.equal(player.getState().likedSongIds.size, 0, 'late responses from an old session are ignored')

  globalThis.fetch = async (url) => new URL(url).pathname === '/api/user/liked-songs'
    ? response([808]) : response({ loggedIn: true })
  assert.equal(await auth.getState().refreshAuthStatus(), true)
  assert.equal(player.getState().likedSongIds.has(808), true, 'login hydrates cloud hearts')
  globalThis.fetch = async () => response({ loggedIn: false })
  await auth.getState().logout()
  assert.equal(player.getState().likedSongIds.size, 0)

  const song = { id: 101, name: '晴天', artists: ['周杰伦'], album: '', coverUrl: '', duration: 180000 }
  player.setState({ currentSong: song, queue: [song], playlist: [song], currentIndex: 0, isPlaying: true })
  await executeClientAction({ action_id: 'clear-cloud-test', task_id: 'queue-task', name: 'clear_queue', arguments: {} })
  assert.equal(player.getState().currentSong, null)
  assert.equal(player.getState().queue.length, 0)
  assert.equal(player.getState().isPlaying, false)
  console.log('Cloud music asset and direct player checks passed.')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
