import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

class MockAudio {
  constructor() {
    this.currentTime = 0
    this.duration = 180
    this.loop = false
    this.muted = false
    this.paused = true
    this.preload = ''
    this.src = 'mock://current-song'
    this.volume = 0.72
    this.listeners = new Map()
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) ?? []
    listeners.push(listener)
    this.listeners.set(name, listeners)
  }

  dispatch(name) {
    for (const listener of this.listeners.get(name) ?? []) listener()
  }

  load() {}

  pause() {
    this.paused = true
    this.dispatch('pause')
  }

  async play() {
    this.paused = false
    this.dispatch('play')
  }

  removeAttribute(name) {
    if (name === 'src') this.src = ''
  }
}

globalThis.Audio = MockAudio

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const server = await createServer({
  root: projectRoot,
  appType: 'custom',
  logLevel: 'silent',
  server: { middlewareMode: true },
})
const originalFetch = globalThis.fetch

function sse(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

function streamResponse(chunks) {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
  })
}

function playerContext(overrides = {}) {
  return {
    has_current_song: true,
    current_song_id: 185809,
    player_state: 'playing',
    volume: 0.72,
    is_muted: false,
    play_mode: 'list-loop',
    current_time: 10,
    duration: 180,
    queue_size: 1,
    current_index: 0,
    current_song_liked: false,
    ...overrides,
  }
}

try {
  const { useAiChatStore } = await server.ssrLoadModule('/src/store/aiChatStore.ts')
  let requestBody
  const observedAssistantStates = []
  const unsubscribe = useAiChatStore.subscribe((state) => {
    observedAssistantStates.push(
      state.messages
        .filter((message) => message.role === 'assistant')
        .map((message) => ({ id: message.id, content: message.content })),
    )
  })
  globalThis.fetch = async (_url, init) => {
    requestBody = JSON.parse(String(init?.body))
    assert.equal(init?.headers.Accept, 'text/event-stream')
    assert.equal(init?.credentials, 'include')
    return streamResponse([
      sse('update', { node: 'load_song_context', status: 'completed' }),
      sse('update', { node: 'agent', status: 'completed' }),
      sse('update', { node: 'tools', status: 'completed' }),
      sse('message', { delta: '逐步' }),
      sse('message', { delta: '生成回答' }),
      sse('update', { node: 'agent', status: 'completed' }),
      sse('final', { type: 'chat', message: '逐步生成回答', search_query: null }),
      sse('done', {}),
    ])
  }

  useAiChatStore.getState().clearConversation()
  const threadId = useAiChatStore.getState().conversationId
  const firstPlayerContext = playerContext()
  await useAiChatStore.getState().sendMessage('分析这首歌', firstPlayerContext)
  const completed = useAiChatStore.getState()
  const assistantMessages = completed.messages.filter((message) => message.role === 'assistant')

  assert.equal(requestBody.thread_id, threadId)
  assert.equal(requestBody.current_song_id, 185809)
  assert.deepEqual(requestBody.player_context, firstPlayerContext)
  assert.equal(assistantMessages.length, 1)
  assert.equal(assistantMessages[0].content, '逐步生成回答')
  assert.ok(observedAssistantStates.some((messages) => messages[0]?.content === '逐步'))
  assert.ok(observedAssistantStates.some((messages) => messages[0]?.content === '逐步生成回答'))
  assert.ok(observedAssistantStates.every((messages) => messages.length <= 1))
  assert.equal(
    new Set(
      observedAssistantStates.flatMap((messages) => messages.map((message) => message.id)),
    ).size,
    1,
  )
  assert.equal(completed.isSending, false)
  assert.equal(completed.status, 'idle')
  unsubscribe()

  const { usePlayerStore } = await server.ssrLoadModule('/src/store/playerStore.ts')
  const currentSong = {
    id: 185809,
    name: '晴天',
    artists: ['周杰伦'],
    album: '叶惠美',
    coverUrl: 'https://example.test/cover.jpg',
    duration: 180000,
  }
  usePlayerStore.setState({
    currentSong,
    queue: [currentSong],
    playlist: [currentSong],
    currentIndex: 0,
    isPlaying: true,
    currentTime: 10,
    duration: 180,
    volume: 0.72,
    isMuted: false,
    likedSongIds: new Set(),
    isLyricsOpen: false,
  })
  const { createPlayerContextSnapshot } = await server.ssrLoadModule(
    '/src/services/playerContext.ts',
  )
  const livePlayerContext = createPlayerContextSnapshot()
  assert.deepEqual(livePlayerContext, playerContext())
  globalThis.fetch = async () => streamResponse([
    sse('action', {
      action_id: 'action-pause',
      task_id: 'task-player',
      name: 'pause_player',
      arguments: {},
    }),
    sse('action', {
      action_id: 'action-seek',
      task_id: 'task-player',
      name: 'seek_by',
      arguments: { offset_seconds: 5 },
    }),
    sse('action', {
      action_id: 'action-seek',
      task_id: 'task-player',
      name: 'seek_by',
      arguments: { offset_seconds: 5 },
    }),
    sse('action', {
      action_id: 'action-volume',
      task_id: 'task-player',
      name: 'set_volume',
      arguments: { volume: 0.35 },
    }),
    sse('action', {
      action_id: 'action-like',
      task_id: 'task-player',
      name: 'set_liked',
      arguments: { song_id: 185809, liked: true },
    }),
    sse('action', {
      action_id: 'action-lyrics',
      task_id: 'task-player',
      name: 'open_lyrics',
      arguments: {},
    }),
    sse('final', { type: 'chat', message: '播放器操作已发送', search_query: null }),
    sse('done', {}),
  ])
  useAiChatStore.getState().clearConversation()
  await useAiChatStore.getState().sendMessage('暂停并前进五秒', livePlayerContext)
  const playerAfterActions = usePlayerStore.getState()

  assert.equal(playerAfterActions.isPlaying, false)
  assert.equal(playerAfterActions.currentTime, 15, 'duplicate action IDs must run once')
  assert.equal(playerAfterActions.volume, 0.35)
  assert.equal(playerAfterActions.likedSongIds.has(185809), true)
  assert.equal(playerAfterActions.isLyricsOpen, true)
  assert.equal(useAiChatStore.getState().error, '')

  const { executeClientAction } = await server.ssrLoadModule(
    '/src/services/aiClientActions.ts',
  )
  const secondSong = {
    id: 202,
    name: '第二首',
    artists: ['歌手二'],
    album: '专辑二',
    coverUrl: '',
    duration: 200000,
  }
  usePlayerStore.setState({
    currentSong,
    queue: [currentSong, secondSong],
    playlist: [currentSong, secondSong],
    currentIndex: 0,
    isPlaying: false,
    playMode: 'list-loop',
  })
  globalThis.fetch = async () => new Response(JSON.stringify({
    code: 200,
    message: 'ok',
    data: {
      songId: 202,
      url: 'mock://playback-source',
      available: true,
      message: 'ok',
    },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  await executeClientAction({
    action_id: 'action-next',
    task_id: 'task-player',
    name: 'play_next',
    arguments: {},
  })
  assert.equal(usePlayerStore.getState().currentSong?.id, 202)
  await executeClientAction({
    action_id: 'action-previous',
    task_id: 'task-player',
    name: 'play_previous',
    arguments: {},
  })
  assert.equal(usePlayerStore.getState().currentSong?.id, 185809)
  await executeClientAction({
    action_id: 'action-mode',
    task_id: 'task-player',
    name: 'set_play_mode',
    arguments: { mode: 'shuffle' },
  })
  await executeClientAction({
    action_id: 'action-muted',
    task_id: 'task-player',
    name: 'set_muted',
    arguments: { muted: true },
  })
  await executeClientAction({
    action_id: 'action-unmuted',
    task_id: 'task-player',
    name: 'set_muted',
    arguments: { muted: false },
  })
  await executeClientAction({
    action_id: 'action-resume',
    task_id: 'task-player',
    name: 'resume_player',
    arguments: {},
  })
  assert.equal(usePlayerStore.getState().playMode, 'shuffle')
  assert.equal(usePlayerStore.getState().isMuted, false)
  assert.equal(usePlayerStore.getState().isPlaying, true)

  const searchedSong = {
    song_id: 303,
    name: '搜索歌曲',
    artists: ['搜索歌手'],
    album: '搜索专辑',
    cover_url: 'https://example.test/search.jpg',
    duration: 210000,
  }
  await executeClientAction({
    action_id: 'action-play-song',
    task_id: 'task-player',
    name: 'play_song',
    arguments: { song: searchedSong },
  })
  assert.equal(usePlayerStore.getState().currentSong?.id, 303)
  assert.equal(usePlayerStore.getState().currentSong?.coverUrl, searchedSong.cover_url)
  await assert.rejects(
    executeClientAction({
      action_id: 'action-unknown-like',
      task_id: 'task-player',
      name: 'set_liked',
      arguments: { song_id: 999, liked: true },
    }),
    /目标歌曲不在当前播放器中/,
  )

  globalThis.fetch = async () => streamResponse([
    sse('action', {
      action_id: 'invalid-volume',
      task_id: 'task-player',
      name: 'set_volume',
      arguments: { volume: 2 },
    }),
    sse('final', { type: 'chat', message: '不应接受无效动作', search_query: null }),
    sse('done', {}),
  ])
  useAiChatStore.getState().clearConversation()
  await useAiChatStore.getState().sendMessage('无效动作', createPlayerContextSnapshot())
  const rejectedActionState = useAiChatStore.getState()

  assert.equal(rejectedActionState.status, 'error')
  assert.match(rejectedActionState.error, /无法识别的播放器动作/)
  assert.equal(usePlayerStore.getState().volume, 0.35)

  globalThis.fetch = async () => streamResponse([
    sse('error', { message: '流式请求失败' }),
    sse('done', {}),
  ])
  useAiChatStore.getState().clearConversation()
  await useAiChatStore.getState().sendMessage(
    '再试一次',
    playerContext({
      has_current_song: false,
      current_song_id: null,
      player_state: 'stopped',
      queue_size: 0,
      current_index: -1,
      current_song_liked: null,
    }),
  )
  const failed = useAiChatStore.getState()

  assert.equal(failed.isSending, false)
  assert.equal(failed.status, 'error')
  assert.equal(failed.error, '流式请求失败')

  globalThis.fetch = async (_url, init) => {
    const encoder = new TextEncoder()
    return new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sse('message', { delta: '旧会话内容' })))
        init?.signal?.addEventListener('abort', () => {
          controller.error(new DOMException('Aborted', 'AbortError'))
        }, { once: true })
      },
    }), {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream; charset=utf-8' },
    })
  }
  useAiChatStore.getState().clearConversation()
  const oldThreadId = useAiChatStore.getState().conversationId
  let stopWaiting
  const sawOldDelta = new Promise((resolve) => {
    stopWaiting = useAiChatStore.subscribe((state) => {
      if (state.messages.some((message) => message.content === '旧会话内容')) resolve()
    })
  })
  const pendingRequest = useAiChatStore.getState().sendMessage(
    '旧会话问题',
    playerContext(),
  )
  await sawOldDelta
  useAiChatStore.getState().clearConversation()
  stopWaiting()
  await pendingRequest
  const cleared = useAiChatStore.getState()

  assert.notEqual(cleared.conversationId, oldThreadId)
  assert.equal(cleared.messages.length, 0)
  assert.equal(cleared.isSending, false)
  assert.equal(cleared.status, 'idle')

  const { streamAiChatMessage } = await server.ssrLoadModule('/src/services/aiChat.ts')
  const telemetryStorage = new Map()
  globalThis.window = {
    location: { search: '' },
    sessionStorage: {
      getItem: (key) => telemetryStorage.get(key) ?? null,
      setItem: (key, value) => telemetryStorage.set(key, value),
      removeItem: (key) => telemetryStorage.delete(key),
    },
  }
  let requestHeaders
  globalThis.fetch = async (_url, init) => {
    requestHeaders = init.headers
    return streamResponse([sse('done', {})])
  }
  const telemetryRequest = {
    message: '验收遥测', thread_id: 'acceptance-test', current_song_id: null,
    player_context: playerContext({ has_current_song: false, current_song_id: null }),
  }
  async function sendTelemetryRequest() {
    for await (const event of streamAiChatMessage(telemetryRequest)) { void event }
  }
  await sendTelemetryRequest()
  assert.equal(requestHeaders['X-MoodFlow-Telemetry-Class'], undefined)
  globalThis.window.location.search = '?__moodflow_acceptance=1'
  await sendTelemetryRequest()
  assert.equal(requestHeaders['X-MoodFlow-Telemetry-Class'], 'acceptance')
  globalThis.window.location.search = ''
  await sendTelemetryRequest()
  assert.equal(requestHeaders['X-MoodFlow-Telemetry-Class'], 'acceptance')
  globalThis.window.location.search = '?__moodflow_acceptance=0'
  await sendTelemetryRequest()
  assert.equal(requestHeaders['X-MoodFlow-Telemetry-Class'], undefined)
  globalThis.window.location.search = '?__moodflow_acceptance=1'
  globalThis.window.sessionStorage.getItem = () => { throw new Error('storage unavailable') }
  await sendTelemetryRequest()
  assert.equal(requestHeaders['X-MoodFlow-Telemetry-Class'], undefined)
  delete globalThis.window

  globalThis.fetch = async () => streamResponse([
    sse('final', {
      type: 'chat', status: 'success', message: '世界线已生成', search_query: null,
      results: [{
        task_id: 'worldline-1', status: 'success', subgraph: 'worldline',
        answer: '世界线已生成', citations: [{ id: 'source-1' }],
        sources: [{ id: 'source-1' }], tool_calls: [], metadata: { worldline_result: {} },
      }],
    }),
    sse('done', {}),
  ])
  const foundationEvents = []
  for await (const event of streamAiChatMessage({
    message: '生成世界线', thread_id: 'foundation-test', current_song_id: null,
    player_context: playerContext({ has_current_song: false, current_song_id: null }),
  })) foundationEvents.push(event)
  assert.equal(foundationEvents[0].response.results[0].subgraph, 'worldline')
  assert.equal(foundationEvents[0].response.results[0].citations[0].id, 'source-1')

  globalThis.fetch = async () => streamResponse([
    sse('final', {
      type: 'chat', message: '无效结果', search_query: null, results: 'bad',
    }),
  ])
  await assert.rejects(async () => {
    for await (const event of streamAiChatMessage({
      message: '测试无效结果', thread_id: 'invalid-foundation', current_song_id: null,
      player_context: playerContext({ has_current_song: false, current_song_id: null }),
    })) { void event }
  }, /无法识别的任务结果/)

  process.stdout.write('AI chat streaming store checks passed.\n')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
