import { apiRequest } from './client'
import type { SongPlaybackSource } from '../types/music'

export function getSongPlaybackSource(songId: number, signal?: AbortSignal) {
  return apiRequest<SongPlaybackSource>(`/api/songs/${songId}/url`, signal)
}
