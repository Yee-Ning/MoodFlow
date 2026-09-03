import { apiRequest, BackendApiError } from './client'
import type {
  PlaylistDetail,
  PlaylistSummary,
  UserProfile,
} from '../types/music'

export { BackendApiError as MusicLibraryApiError }

export function getCurrentUserProfile(signal?: AbortSignal) {
  return apiRequest<UserProfile>('/api/user/profile', signal)
}

export function getCurrentUserPlaylists(signal?: AbortSignal) {
  return apiRequest<PlaylistSummary[]>('/api/user/playlists', signal)
}

export function getPlaylistDetail(id: number, signal?: AbortSignal) {
  return apiRequest<PlaylistDetail>(`/api/playlists/${id}`, signal)
}
