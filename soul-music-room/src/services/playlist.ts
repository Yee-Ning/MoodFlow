import { apiRequest } from '../api/client'
import { getCurrentUserPlaylists } from '../api/musicLibrary'
import type { PlaylistSummary } from '../types/music'

export interface SelectablePlaylist extends PlaylistSummary {
  editable: boolean
}

export type AddToPlaylistStatus = 'added' | 'duplicate' | 'forbidden'

export interface AddToPlaylistResult {
  status: AddToPlaylistStatus
  message: string
}

export async function getSelectablePlaylists(signal?: AbortSignal) {
  const playlists = await getCurrentUserPlaylists(signal)
  return playlists.map<SelectablePlaylist>((playlist) => ({
    ...playlist,
    editable: !playlist.subscribed,
  }))
}

export function addSongToPlaylist(playlistId: number, songId: number, signal?: AbortSignal) {
  return apiRequest<AddToPlaylistResult>(
    `/api/playlists/${playlistId}/songs/${songId}`,
    signal,
    { method: 'POST' },
  )
}
