import type { SongSummary } from './music'

export type AiChatRole = 'user' | 'assistant'
export type AiChatStatus = 'idle' | 'sending' | 'loading' | 'error'

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

export interface AiChatContext {
  current_song_id?: number | null
  current_playlist_id?: number | null
  page?: string | null
}

