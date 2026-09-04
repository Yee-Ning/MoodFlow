import type { AiChatNode, AiSongRecommendation } from '../types/aiChat'

const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')

export interface ChatRequest {
  message: string
  thread_id: string
  current_song_id: number | null
}

export type AiResponseType = 'chat' | 'music_search' | 'music_recommendation'

export interface ChatResponse {
  type: AiResponseType
  message: string
  search_query: string | null
  conversation_id?: string
  recommendations?: AiSongRecommendation[]
}

export type AiChatStreamEvent =
  | { type: 'message'; delta: string }
  | { type: 'update'; node: AiChatNode; status: 'completed' }
  | { type: 'final'; response: ChatResponse }
  | { type: 'error'; message: string }
  | { type: 'done' }

export class AiChatApiError extends Error {
  readonly status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = 'AiChatApiError'
    this.status = status
  }
}

function isRecommendation(value: unknown): value is AiSongRecommendation {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<AiSongRecommendation>
  return (
    typeof item.id === 'number'
    && typeof item.name === 'string'
    && Array.isArray(item.artists)
    && item.artists.every((artist) => typeof artist === 'string')
    && typeof item.album === 'string'
    && typeof item.coverUrl === 'string'
    && typeof item.duration === 'number'
    && typeof item.reason === 'string'
  )
}

function parseResponse(payload: unknown): ChatResponse {
  if (!payload || typeof payload !== 'object') {
    throw new AiChatApiError('AI 返回了无法识别的内容')
  }

  const candidate = payload as Partial<ChatResponse> & { songs?: unknown }
  if (
    typeof candidate.message !== 'string'
    || !['chat', 'music_search', 'music_recommendation'].includes(candidate.type ?? '')
  ) {
    throw new AiChatApiError('AI 返回了无法识别的内容')
  }

  const rawRecommendations = candidate.recommendations ?? candidate.songs
  const recommendations = Array.isArray(rawRecommendations)
    ? rawRecommendations.filter(isRecommendation)
    : undefined

  return {
    type: candidate.type as AiResponseType,
    message: candidate.message,
    search_query: typeof candidate.search_query === 'string' ? candidate.search_query : null,
    conversation_id: typeof candidate.conversation_id === 'string'
      ? candidate.conversation_id
      : undefined,
    recommendations: recommendations?.length ? recommendations : undefined,
  }
}

function parseSseEvent(block: string): AiChatStreamEvent | null {
  let eventName = ''
  const dataLines: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!eventName) return null
  let payload: unknown
  try {
    payload = JSON.parse(dataLines.join('\n') || '{}')
  } catch {
    throw new AiChatApiError('AI 返回了无法识别的流式数据')
  }
  const candidate = payload && typeof payload === 'object'
    ? payload as Record<string, unknown>
    : {}

  if (eventName === 'message' && typeof candidate.delta === 'string') {
    return { type: 'message', delta: candidate.delta }
  }
  if (
    eventName === 'update'
    && ['load_song_context', 'agent', 'tools'].includes(String(candidate.node))
    && candidate.status === 'completed'
  ) {
    return {
      type: 'update',
      node: candidate.node as AiChatNode,
      status: 'completed',
    }
  }
  if (eventName === 'final') {
    return { type: 'final', response: parseResponse(payload) }
  }
  if (eventName === 'error' && typeof candidate.message === 'string') {
    return { type: 'error', message: candidate.message }
  }
  if (eventName === 'done') return { type: 'done' }
  return null
}

export async function* streamAiChatMessage(
  request: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<AiChatStreamEvent> {
  let response: Response

  try {
    response = await fetch(`${AI_API_BASE_URL}/api/ai/chat`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new AiChatApiError('暂时无法连接 MoodFlow AI，请稍后再试')
  }

  if (!response.ok) {
    const payload = await response.json().catch(() => null) as unknown
    const rawDetail = payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : null
    const detail = response.status >= 500
      ? 'MoodFlow AI 暂时离线，稍后再试'
      : typeof rawDetail === 'string'
        ? rawDetail
        : '这条消息暂时没有发送成功，请换个说法再试'
    throw new AiChatApiError(detail, response.status)
  }

  if (!response.body) {
    throw new AiChatApiError('AI 没有返回可读取的响应流')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      buffer = buffer.replace(/\r\n/g, '\n')

      let separator = buffer.indexOf('\n\n')
      while (separator >= 0) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        const event = parseSseEvent(block)
        if (event) yield event
        separator = buffer.indexOf('\n\n')
      }

      if (done) break
    }

    if (buffer.trim()) {
      const event = parseSseEvent(buffer)
      if (event) yield event
    }
  } finally {
    reader.releaseLock()
  }
}
