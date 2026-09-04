import type { SongSummary } from './music'

export type AiChatRole = 'user' | 'assistant'
export type AiChatNode = 'load_song_context' | 'agent' | 'tools'
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
