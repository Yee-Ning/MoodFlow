import type { ClientAction } from './aiChat'
import { usePlayerStore } from '../store/playerStore'
import type { SongSummary } from '../types/music'

const MAX_REMEMBERED_ACTIONS = 500
const executedActionIds = new Set<string>()
const executedActionOrder: string[] = []

export class AiClientActionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AiClientActionError'
  }
}

function rememberAction(actionId: string) {
  if (executedActionIds.has(actionId)) return false
  executedActionIds.add(actionId)
  executedActionOrder.push(actionId)
  if (executedActionOrder.length > MAX_REMEMBERED_ACTIONS) {
    const oldest = executedActionOrder.shift()
    if (oldest) executedActionIds.delete(oldest)
  }
  return true
}

function requireCurrentSong() {
  if (!usePlayerStore.getState().currentSong) {
    throw new AiClientActionError('AI 播放器操作未执行：请先选择一首歌曲')
  }
}

function requireQueue() {
  if (usePlayerStore.getState().queue.length === 0) {
    throw new AiClientActionError('AI 播放器操作未执行：当前播放队列为空')
  }
}

function toSongSummary(action: Extract<ClientAction, { name: 'play_song' }>): SongSummary {
  const song = action.arguments.song
  return {
    id: song.song_id,
    name: song.name,
    artists: song.artists,
    album: song.album,
    coverUrl: song.cover_url,
    duration: song.duration,
  }
}

export async function executeClientAction(action: ClientAction): Promise<void> {
  if (!rememberAction(action.action_id)) return

  const player = usePlayerStore.getState()
  switch (action.name) {
    case 'pause_player':
      requireCurrentSong()
      player.pausePlayback()
      return
    case 'resume_player':
      requireCurrentSong()
      await player.resumePlayback()
      return
    case 'play_next':
      requireQueue()
      await player.playNext()
      return
    case 'play_previous':
      requireQueue()
      await player.playPrevious()
      return
    case 'set_volume':
      player.setVolume(action.arguments.volume)
      return
    case 'set_muted':
      player.setMuted(action.arguments.muted)
      return
    case 'seek_to':
      requireCurrentSong()
      player.seek(action.arguments.seconds)
      return
    case 'seek_by':
      requireCurrentSong()
      player.seek(usePlayerStore.getState().currentTime + action.arguments.offset_seconds)
      return
    case 'set_play_mode':
      player.setPlayMode(action.arguments.mode)
      return
    case 'play_song': {
      const song = toSongSummary(action)
      const latest = usePlayerStore.getState()
      if (latest.currentSong?.id === song.id) {
        await latest.resumePlayback()
      } else {
        await latest.playSong(song)
      }
      return
    }
    case 'set_liked': {
      const latest = usePlayerStore.getState()
      const knownSong = latest.currentSong?.id === action.arguments.song_id
        || latest.queue.some((song) => song.id === action.arguments.song_id)
      if (!knownSong) {
        throw new AiClientActionError('AI 喜欢操作未执行：目标歌曲不在当前播放器中')
      }
      await latest.setLiked(action.arguments.song_id, action.arguments.liked)
      return
    }
    case 'open_lyrics':
      requireCurrentSong()
      player.openLyrics()
  }
}
