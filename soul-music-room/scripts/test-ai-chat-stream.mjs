import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

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
  await useAiChatStore.getState().sendMessage('分析这首歌', 185809)
  const completed = useAiChatStore.getState()
  const assistantMessages = completed.messages.filter((message) => message.role === 'assistant')

  assert.equal(requestBody.thread_id, threadId)
  assert.equal(requestBody.current_song_id, 185809)
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

  globalThis.fetch = async () => streamResponse([
    sse('error', { message: '流式请求失败' }),
    sse('done', {}),
  ])
  useAiChatStore.getState().clearConversation()
  await useAiChatStore.getState().sendMessage('再试一次', null)
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
  const pendingRequest = useAiChatStore.getState().sendMessage('旧会话问题', 185809)
  await sawOldDelta
  useAiChatStore.getState().clearConversation()
  stopWaiting()
  await pendingRequest
  const cleared = useAiChatStore.getState()

  assert.notEqual(cleared.conversationId, oldThreadId)
  assert.equal(cleared.messages.length, 0)
  assert.equal(cleared.isSending, false)
  assert.equal(cleared.status, 'idle')

  process.stdout.write('AI chat streaming store checks passed.\n')
} finally {
  globalThis.fetch = originalFetch
  await server.close()
}
