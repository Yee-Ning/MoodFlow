import type { AiChatNode, AiSongRecommendation } from '../types/aiChat'

const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')
const ACCEPTANCE_TELEMETRY_KEY = 'moodflow-acceptance-telemetry'

function acceptanceTelemetryHeaders(): Record<string, string> {
  if (typeof window === 'undefined') return {}

  try {
    const marker = new URLSearchParams(window.location.search).get('__moodflow_acceptance')
    if (marker === '1') window.sessionStorage.setItem(ACCEPTANCE_TELEMETRY_KEY, '1')
    if (marker === '0') window.sessionStorage.removeItem(ACCEPTANCE_TELEMETRY_KEY)
    return window.sessionStorage.getItem(ACCEPTANCE_TELEMETRY_KEY) === '1'
      ? { 'X-MoodFlow-Telemetry-Class': 'acceptance' }
      : {}
  } catch {
    return {}
  }
}

export interface ChatRequest {
  message: string
  thread_id: string
  current_song_id: number | null
  player_context: PlayerContext
}

export type PlayerState = 'playing' | 'paused' | 'stopped' | 'unknown'
export type PlayerPlayMode = 'sequence' | 'list-loop' | 'single-loop' | 'shuffle'

export interface PlayerContext {
  has_current_song: boolean
  current_song_id: number | null
  player_state: PlayerState
  volume: number
  is_muted: boolean
  play_mode: PlayerPlayMode
  current_time: number
  duration: number
  queue_size: number
  current_index: number
  current_song_liked: boolean | null
}

interface ClientTrack {
  song_id: number
  name: string
  artists: string[]
  album: string
  cover_url: string
  duration: number
}

interface ClientActionBase {
  action_id: string
  task_id: string
}

type EmptyClientActionName =
  | 'pause_player'
  | 'resume_player'
  | 'play_next'
  | 'play_previous'
  | 'open_lyrics'

export type ClientAction =
  | (ClientActionBase & {
      name: EmptyClientActionName
      arguments: Record<string, never>
    })
  | (ClientActionBase & { name: 'set_volume'; arguments: { volume: number } })
  | (ClientActionBase & { name: 'set_muted'; arguments: { muted: boolean } })
  | (ClientActionBase & { name: 'seek_to'; arguments: { seconds: number } })
  | (ClientActionBase & { name: 'seek_by'; arguments: { offset_seconds: number } })
  | (ClientActionBase & {
      name: 'set_play_mode'
      arguments: { mode: PlayerPlayMode }
    })
  | (ClientActionBase & { name: 'play_song'; arguments: { song: ClientTrack } })
  | (ClientActionBase & {
      name: 'insert_next_track' | 'append_to_queue'
      arguments: { song: ClientTrack }
    })
  | (ClientActionBase & {
      name: 'play_album'
      arguments: { album_id: number; name: string; tracks: ClientTrack[] }
    })
  | (ClientActionBase & {
      name: 'play_playlist'
      arguments: { playlist_id: number; name: string; tracks: ClientTrack[] }
    })
  | (ClientActionBase & {
      name: 'jump_to_queue_item'
      arguments: { song_id: number } | { index: number }
    })
  | (ClientActionBase & {
      name: 'set_liked'
      arguments: { song_id: number; liked: boolean }
    })

export type AiResponseType = 'chat' | 'music_search' | 'music_recommendation'

export interface FoundationTaskResult {
  task_id: string
  status: 'success' | 'failed' | 'needs_input'
  subgraph: 'current_song' | 'tool_calling' | 'general_chat' | 'worldline'
  answer: string | null
  citations: Record<string, unknown>[]
  sources: Record<string, unknown>[]
  tool_calls: Record<string, unknown>[]
  metadata: Record<string, unknown>
}

export interface ChatResponse {
  type: AiResponseType
  message: string
  search_query: string | null
  conversation_id?: string
  recommendations?: AiSongRecommendation[]
  status?: FoundationTaskResult['status']
  results?: FoundationTaskResult[]
}

export type AiChatStreamEvent =
  | { type: 'message'; delta: string }
  | { type: 'update'; node: AiChatNode; status: 'completed' }
  | { type: 'action'; action: ClientAction }
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, keys: string[]) {
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every((key) => key in value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0
}

function parseClientTrack(value: unknown): ClientTrack {
  if (!isRecord(value) || !hasExactKeys(
    value,
    ['song_id', 'name', 'artists', 'album', 'cover_url', 'duration'],
  )) {
    throw new AiChatApiError('AI 返回了无法识别的播放器动作')
  }
  if (
    !isPositiveInteger(value.song_id)
    || typeof value.name !== 'string'
    || !value.name.trim()
    || !Array.isArray(value.artists)
    || value.artists.length === 0
    || !value.artists.every((artist) => typeof artist === 'string' && artist.trim())
    || typeof value.album !== 'string'
    || typeof value.cover_url !== 'string'
    || !Number.isInteger(value.duration)
    || Number(value.duration) < 0
  ) {
    throw new AiChatApiError('AI 返回了无法识别的播放器动作')
  }
  return {
    song_id: value.song_id,
    name: value.name,
    artists: value.artists,
    album: value.album,
    cover_url: value.cover_url,
    duration: value.duration as number,
  }
}

function parseClientTracks(value: unknown): ClientTrack[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > 1000) {
    throw new AiChatApiError('AI 返回了无法识别的播放器动作')
  }
  return value.map(parseClientTrack)
}

function parseClientAction(payload: unknown): ClientAction {
  if (!isRecord(payload) || !hasExactKeys(
    payload,
    ['action_id', 'task_id', 'name', 'arguments'],
  )) {
    throw new AiChatApiError('AI 返回了无法识别的播放器动作')
  }
  if (
    typeof payload.action_id !== 'string'
    || !payload.action_id
    || payload.action_id.length > 128
    || typeof payload.task_id !== 'string'
    || !payload.task_id
    || payload.task_id.length > 128
    || typeof payload.name !== 'string'
    || !isRecord(payload.arguments)
  ) {
    throw new AiChatApiError('AI 返回了无法识别的播放器动作')
  }

  const base = { action_id: payload.action_id, task_id: payload.task_id }
  const args = payload.arguments
  switch (payload.name) {
    case 'pause_player':
    case 'resume_player':
    case 'play_next':
    case 'play_previous':
    case 'open_lyrics':
      if (!hasExactKeys(args, [])) break
      return { ...base, name: payload.name, arguments: {} }
    case 'set_volume':
      if (!hasExactKeys(args, ['volume']) || !isFiniteNumber(args.volume)) break
      if (args.volume < 0 || args.volume > 1) break
      return { ...base, name: payload.name, arguments: { volume: args.volume } }
    case 'set_muted':
      if (!hasExactKeys(args, ['muted']) || typeof args.muted !== 'boolean') break
      return { ...base, name: payload.name, arguments: { muted: args.muted } }
    case 'seek_to':
      if (!hasExactKeys(args, ['seconds']) || !isFiniteNumber(args.seconds)) break
      if (args.seconds < 0) break
      return { ...base, name: payload.name, arguments: { seconds: args.seconds } }
    case 'seek_by':
      if (!hasExactKeys(args, ['offset_seconds']) || !isFiniteNumber(args.offset_seconds)) break
      if (Math.abs(args.offset_seconds) > 86400) break
      return {
        ...base,
        name: payload.name,
        arguments: { offset_seconds: args.offset_seconds },
      }
    case 'set_play_mode': {
      const modes: PlayerPlayMode[] = ['sequence', 'list-loop', 'single-loop', 'shuffle']
      if (
        !hasExactKeys(args, ['mode'])
        || !modes.includes(args.mode as PlayerPlayMode)
      ) break
      return {
        ...base,
        name: payload.name,
        arguments: { mode: args.mode as PlayerPlayMode },
      }
    }
    case 'play_song':
    case 'insert_next_track':
    case 'append_to_queue':
      if (!hasExactKeys(args, ['song'])) break
      return {
        ...base,
        name: payload.name,
        arguments: { song: parseClientTrack(args.song) },
      }
    case 'play_album':
      if (
        !hasExactKeys(args, ['album_id', 'name', 'tracks'])
        || !isPositiveInteger(args.album_id)
        || typeof args.name !== 'string'
        || !args.name.trim()
      ) break
      return {
        ...base,
        name: payload.name,
        arguments: {
          album_id: args.album_id,
          name: args.name,
          tracks: parseClientTracks(args.tracks),
        },
      }
    case 'play_playlist':
      if (
        !hasExactKeys(args, ['playlist_id', 'name', 'tracks'])
        || !isPositiveInteger(args.playlist_id)
        || typeof args.name !== 'string'
        || !args.name.trim()
      ) break
      return {
        ...base,
        name: payload.name,
        arguments: {
          playlist_id: args.playlist_id,
          name: args.name,
          tracks: parseClientTracks(args.tracks),
        },
      }
    case 'jump_to_queue_item':
      if (hasExactKeys(args, ['song_id']) && isPositiveInteger(args.song_id)) {
        return { ...base, name: payload.name, arguments: { song_id: args.song_id } }
      }
      if (hasExactKeys(args, ['index']) && isPositiveInteger(args.index)) {
        return { ...base, name: payload.name, arguments: { index: args.index } }
      }
      break
    case 'set_liked':
      if (
        !hasExactKeys(args, ['song_id', 'liked'])
        || !isPositiveInteger(args.song_id)
        || typeof args.liked !== 'boolean'
      ) break
      return {
        ...base,
        name: payload.name,
        arguments: { song_id: args.song_id, liked: args.liked },
      }
  }
  throw new AiChatApiError('AI 返回了无法识别的播放器动作')
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
  let results: FoundationTaskResult[] | undefined
  if ('results' in candidate) {
    if (!Array.isArray(candidate.results)) {
      throw new AiChatApiError('AI 返回了无法识别的任务结果')
    }
    results = candidate.results.map((value) => {
      if (
        !isRecord(value)
        || typeof value.task_id !== 'string'
        || !['success', 'failed', 'needs_input'].includes(String(value.status))
        || !['current_song', 'tool_calling', 'general_chat', 'worldline'].includes(String(value.subgraph))
        || (value.answer !== null && typeof value.answer !== 'string')
        || !Array.isArray(value.citations)
        || !value.citations.every(isRecord)
        || !Array.isArray(value.sources)
        || !value.sources.every(isRecord)
        || !Array.isArray(value.tool_calls)
        || !value.tool_calls.every(isRecord)
        || !isRecord(value.metadata)
      ) {
        throw new AiChatApiError('AI 返回了无法识别的任务结果')
      }
      return value as unknown as FoundationTaskResult
    })
  }

  return {
    type: candidate.type as AiResponseType,
    message: candidate.message,
    search_query: typeof candidate.search_query === 'string' ? candidate.search_query : null,
    conversation_id: typeof candidate.conversation_id === 'string'
      ? candidate.conversation_id
      : undefined,
    recommendations: recommendations?.length ? recommendations : undefined,
    status: ['success', 'failed', 'needs_input'].includes(String(candidate.status))
      ? candidate.status as FoundationTaskResult['status']
      : undefined,
    results,
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
    && [
      'load_song_context',
      'agent',
      'tools',
      'prepare_context',
      'intent_router',
      'development_route',
      'current_song_subgraph',
      'tool_calling_subgraph',
      'worldline_subgraph',
      'general_chat_subgraph',
      'aggregate_results',
      'final_response',
      'clarify',
    ].includes(String(candidate.node))
    && candidate.status === 'completed'
  ) {
    return {
      type: 'update',
      node: candidate.node as AiChatNode,
      status: 'completed',
    }
  }
  if (eventName === 'action') {
    return { type: 'action', action: parseClientAction(payload) }
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
        ...acceptanceTelemetryHeaders(),
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
