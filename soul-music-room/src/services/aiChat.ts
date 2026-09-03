import type { AiChatContext, AiSongRecommendation } from '../types/aiChat'

const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')

export interface ChatRequest {
  message: string
  context?: AiChatContext
}

export type AiResponseType = 'chat' | 'music_search' | 'music_recommendation'

export interface ChatResponse {
  type: AiResponseType
  message: string
  search_query: string | null
  conversation_id?: string
  recommendations?: AiSongRecommendation[]
}

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

export async function sendAiChatMessage(
  request: ChatRequest,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  let response: Response

  try {
    response = await fetch(`${AI_API_BASE_URL}/api/ai/chat`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new AiChatApiError('暂时无法连接 MoodFlow AI，请稍后再试')
  }

  const payload = await response.json().catch(() => null) as unknown
  if (!response.ok) {
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

  return parseResponse(payload)
}
