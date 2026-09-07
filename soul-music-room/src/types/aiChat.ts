import type { SongSummary } from './music'

export type AiChatRole = 'user' | 'assistant'
export type AiChatNode =
  | 'load_song_context'
  | 'agent'
  | 'tools'
  | 'prepare_context'
  | 'intent_router'
  | 'development_route'
  | 'current_song_subgraph'
  | 'tool_calling_subgraph'
  | 'worldline_subgraph'
  | 'general_chat_subgraph'
  | 'aggregate_results'
  | 'final_response'
  | 'clarify'
export type AiChatStatus =
  | 'idle'
  | 'sending'
  | 'loading-context'
  | 'generating'
  | 'using-tools'
  | 'finalizing'
  | 'error'

export interface AiSongRecommendation extends SongSummary {
  reason: string
}

export interface AiChatMessage {
  id: string
  role: AiChatRole
  content: string
  recommendations?: AiSongRecommendation[]
  createdAt: number
}
