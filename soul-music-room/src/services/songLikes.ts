export type SongLikeSyncAdapter = (songId: number, liked: boolean) => Promise<void>

let syncAdapter: SongLikeSyncAdapter | null = null

/**
 * Keeps the player independent from a specific backend endpoint. A future
 * NetEase like-song integration can register its existing authenticated call
 * here without changing the player store or UI.
 */
export function registerSongLikeSyncAdapter(adapter: SongLikeSyncAdapter | null) {
  syncAdapter = adapter
}

export async function syncSongLikePreference(songId: number, liked: boolean) {
  if (!syncAdapter) return false

  await syncAdapter(songId, liked)
  return true
}
