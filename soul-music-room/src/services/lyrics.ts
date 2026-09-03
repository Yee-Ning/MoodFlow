import { apiRequest } from '../api/client'

export type RawLyricsStatus = 'available' | 'instrumental' | 'unavailable'

export interface RawLyricsData {
  lyric: string
  translation: string
  status: RawLyricsStatus
}

export function getSongLyrics(songId: number, signal?: AbortSignal) {
  return apiRequest<RawLyricsData>(`/api/songs/${songId}/lyrics`, signal)
}
