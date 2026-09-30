import { apiRequest, BackendApiError } from '../api/client'

export type SongLikeSyncAdapter = (songId: number, liked: boolean) => Promise<void>

let syncAdapter: SongLikeSyncAdapter | null = null

/** Optional adapter for embedding/testing; the default uses the authenticated backend. */
export function registerSongLikeSyncAdapter(adapter: SongLikeSyncAdapter | null) {
  syncAdapter = adapter
}

export async function syncSongLikePreference(songId: number, liked: boolean) {
  if (syncAdapter) {
    await syncAdapter(songId, liked)
    return true
  }
  const result = await apiRequest<{
    songId: number; liked: boolean; status: string
  }>(`/api/songs/${songId}/like?liked=${liked}`, undefined, { method: 'POST' })
  if (result?.songId !== songId || result.liked !== liked || result.status !== 'synced') {
    throw new BackendApiError('音乐服务返回了不匹配的红心状态')
  }
  return true
}

export async function getCloudLikedSongIds() {
  const ids = await apiRequest<number[]>('/api/user/liked-songs')
  if (!Array.isArray(ids) || ids.some((id) => !Number.isSafeInteger(id) || id <= 0)) {
    throw new BackendApiError('音乐服务返回了无效的红心列表')
  }
  return ids
}
