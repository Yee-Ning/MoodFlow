import type { ClientAction } from './aiChat'
import { usePlayerStore } from '../store/playerStore'
import type { SongSummary } from '../types/music'

const MAX_REMEMBERED_ACTIONS = 500
const executedActions = new Map<string, { signature: string; result: Promise<void> }>()
const executedActionOrder: string[] = []

export class AiClientActionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AiClientActionError'
  }
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

type ClientTrack = Extract<ClientAction, { name: 'play_song' }>['arguments']['song']

function toSongSummary(song: ClientTrack): SongSummary {
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
  const signature = JSON.stringify(action)
  const previous = executedActions.get(action.action_id)
  if (previous) {
    if (previous.signature !== signature) throw new AiClientActionError('动作标识与原操作不一致')
    return previous.result
  }
  const result = applyClientAction(action)
  executedActions.set(action.action_id, { signature, result })
  executedActionOrder.push(action.action_id)
  if (executedActionOrder.length > MAX_REMEMBERED_ACTIONS) {
    const oldest = executedActionOrder.shift()
    if (oldest) executedActions.delete(oldest)
  }
  return result
}

async function applyClientAction(action: ClientAction): Promise<void> {

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
      const song = toSongSummary(action.arguments.song)
      const latest = usePlayerStore.getState()
      if (latest.currentSong?.id === song.id) {
        await latest.resumePlayback()
      } else {
        await latest.playSong(song)
      }
      return
    }
    case 'insert_next_track':
      player.insertNextTrack(toSongSummary(action.arguments.song))
      return
    case 'append_to_queue':
      player.appendToQueue(toSongSummary(action.arguments.song))
      return
    case 'play_album':
    case 'play_playlist':
      await player.replaceQueueAndPlay(action.arguments.tracks.map(toSongSummary))
      return
    case 'jump_to_queue_item': {
      requireQueue()
      const latest = usePlayerStore.getState()
      const target = action.arguments
      const index = 'song_id' in target
        ? latest.queue.findIndex((song) => song.id === target.song_id)
        : target.index - 1
      if (index < 0 || index >= latest.queue.length) {
        throw new AiClientActionError('AI 切歌操作未执行：目标歌曲不在当前播放队列中')
      }
      await latest.playQueueItem(index)
      return
    }
    case 'set_liked': {
      // The server has already persisted this explicit state; do not write twice.
      player.applyLikedState(action.arguments.song_id, action.arguments.liked)
      return
    }
    case 'clear_queue':
      player.clearQueue()
      return
    case 'open_lyrics':
      requireCurrentSong()
      player.openLyrics()
  }
}
