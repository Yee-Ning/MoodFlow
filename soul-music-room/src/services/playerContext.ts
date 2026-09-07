import type { PlayerContext } from './aiChat'
import { usePlayerStore } from '../store/playerStore'

export function createPlayerContextSnapshot(): PlayerContext {
  const state = usePlayerStore.getState()
  const duration = Number.isFinite(state.duration) ? Math.max(0, state.duration) : 0
  const rawCurrentTime = Number.isFinite(state.currentTime)
    ? Math.max(0, state.currentTime)
    : 0
  const currentTime = duration > 0 ? Math.min(rawCurrentTime, duration) : rawCurrentTime
  const hasCurrentSong = state.currentSong !== null
  return {
    has_current_song: hasCurrentSong,
    current_song_id: state.currentSong?.id ?? null,
    player_state: hasCurrentSong
      ? state.isPlaying ? 'playing' : 'paused'
      : 'stopped',
    volume: Math.min(Math.max(state.volume, 0), 1),
    is_muted: state.isMuted,
    play_mode: state.playMode,
    current_time: currentTime,
    duration,
    queue_size: state.queue.length,
    current_index: state.currentIndex >= 0 && state.currentIndex < state.queue.length
      ? state.currentIndex
      : -1,
    current_song_liked: state.currentSong
      ? state.likedSongIds.has(state.currentSong.id)
      : null,
  }
}
