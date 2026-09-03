import { usePlayerStore } from '../store/playerStore'
import type { SongSummary } from '../types/music'

export function playSong(song: SongSummary, playlist: SongSummary[] = [song]) {
  return usePlayerStore.getState().playSong(song, playlist)
}
