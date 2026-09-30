import { create } from 'zustand'
import { BackendApiError } from '../api/client'
import { getSongPlaybackSource } from '../api/playback'
import { getGlobalAudioElement } from '../services/audio'
import { syncSongLikePreference } from '../services/songLikes'
import type { LyricLine, LyricsStatus, SongSummary } from '../types/music'

export type PlayMode = 'sequence' | 'list-loop' | 'single-loop' | 'shuffle'

interface PlayerState {
  currentSong: SongSummary | null
  queue: SongSummary[]
  /** Kept as a compatibility alias for existing playback callers. */
  playlist: SongSummary[]
  currentIndex: number
  isPlaying: boolean
  isLoading: boolean
  currentTime: number
  duration: number
  volume: number
  previousVolume: number
  isMuted: boolean
  playMode: PlayMode
  likedSongIds: Set<number>
  isQueueOpen: boolean
  isLyricsOpen: boolean
  isAddToPlaylistOpen: boolean
  isFullscreen: boolean
  lyrics: LyricLine[]
  activeLyricIndex: number
  lyricsLoading: boolean
  lyricsStatus: LyricsStatus
  lyricsError: string
  notice: string
  playSong: (song: SongSummary, playlist?: SongSummary[]) => Promise<void>
  insertNextTrack: (song: SongSummary) => void
  appendToQueue: (song: SongSummary) => void
  replaceQueueAndPlay: (tracks: SongSummary[]) => Promise<void>
  togglePlayback: () => Promise<void>
  pausePlayback: () => void
  resumePlayback: () => Promise<void>
  playPrevious: () => Promise<void>
  playNext: () => Promise<void>
  seek: (time: number) => void
  setVolume: (volume: number) => void
  toggleMute: () => void
  setMuted: (muted: boolean) => void
  setPlayMode: (mode: PlayMode) => void
  togglePlayMode: () => void
  toggleLike: (songId: number) => Promise<void>
  setLiked: (songId: number, liked: boolean) => Promise<void>
  playQueueItem: (index: number) => Promise<void>
  removeQueueItem: (index: number) => Promise<void>
  clearQueue: () => void
  clearQueueExceptCurrent: () => void
  reorderQueue: (fromIndex: number, toIndex: number) => void
  toggleQueue: () => void
  closeQueue: () => void
  toggleLyrics: () => void
  openLyrics: () => void
  closeLyrics: () => void
  openAddToPlaylist: () => void
  closeAddToPlaylist: () => void
  setFullscreen: (fullscreen: boolean) => void
  beginLyricsLoad: () => void
  setLyricsResult: (lyrics: LyricLine[], status: LyricsStatus) => void
  setLyricsError: (message: string) => void
  resetLyrics: () => void
  setActiveLyricIndex: (index: number) => void
  clearNotice: () => void
}

const DEFAULT_VOLUME = 0.72
const VOLUME_STORAGE_KEY = 'moodflow-player-volume'
const PREVIOUS_VOLUME_STORAGE_KEY = 'moodflow-player-previous-volume'
const PLAY_MODES: PlayMode[] = ['sequence', 'list-loop', 'single-loop', 'shuffle']

function clampVolume(volume: number) {
  return Math.min(Math.max(volume, 0), 1)
}

function readStoredVolume(key: string, fallback: number) {
  if (typeof window === 'undefined') return fallback

  try {
    const rawValue = window.localStorage.getItem(key)
    if (rawValue === null) return fallback
    const storedValue = Number(rawValue)
    return Number.isFinite(storedValue) ? clampVolume(storedValue) : fallback
  } catch {
    return fallback
  }
}

function persistVolume(volume: number, previousVolume: number) {
  if (typeof window === 'undefined') return

  try {
    window.localStorage.setItem(VOLUME_STORAGE_KEY, String(volume))
    window.localStorage.setItem(PREVIOUS_VOLUME_STORAGE_KEY, String(previousVolume))
  } catch {
    // Playback should keep working when storage is blocked by the browser.
  }
}

const initialVolume = readStoredVolume(VOLUME_STORAGE_KEY, DEFAULT_VOLUME)
const initialPreviousVolume = readStoredVolume(
  PREVIOUS_VOLUME_STORAGE_KEY,
  initialVolume > 0 ? initialVolume : DEFAULT_VOLUME,
)
const audio = getGlobalAudioElement()
audio.volume = initialVolume
audio.muted = initialVolume === 0
audio.loop = false

let loadVersion = 0
const runtimeFailureIds = new Set<number>()

class PlaybackUnavailableError extends Error {}

function normalizedIndex(index: number, length: number) {
  return ((index % length) + length) % length
}

function randomQueueIndex(length: number, currentIndex: number) {
  if (length <= 1) return Math.max(0, currentIndex)
  const offset = 1 + Math.floor(Math.random() * (length - 1))
  return normalizedIndex(currentIndex + offset, length)
}

function fallbackDuration(song: SongSummary | null) {
  return song ? Math.max(0, song.duration / 1000) : 0
}

function isAutoplayBlocked(error: unknown) {
  return error instanceof DOMException && error.name === 'NotAllowedError'
}

async function loadAndPlay(index: number, attemptedIds = new Set<number>()): Promise<void> {
  const queue = usePlayerStore.getState().queue
  if (queue.length === 0) return

  const nextIndex = normalizedIndex(index, queue.length)
  const song = queue[nextIndex]

  if (attemptedIds.has(song.id) || attemptedIds.size >= queue.length) {
    usePlayerStore.setState({
      isLoading: false,
      isPlaying: false,
      notice: '当前歌单中暂时没有可播放的歌曲',
    })
    return
  }

  attemptedIds.add(song.id)
  const version = ++loadVersion
  audio.pause()
  audio.removeAttribute('src')
  audio.load()

  usePlayerStore.setState({
    currentSong: song,
    currentIndex: nextIndex,
    isLoading: true,
    isPlaying: false,
    currentTime: 0,
    duration: fallbackDuration(song),
    lyrics: [],
    activeLyricIndex: -1,
    lyricsLoading: false,
    lyricsStatus: 'idle',
    lyricsError: '',
    notice: attemptedIds.size > 1 ? usePlayerStore.getState().notice : '',
  })

  try {
    const source = await getSongPlaybackSource(song.id)
    if (version !== loadVersion) return

    if (!source.available || !source.url) {
      throw new PlaybackUnavailableError(source.message || '该歌曲暂时没有可用的播放地址')
    }

    audio.src = source.url
    audio.currentTime = 0
    audio.loop = usePlayerStore.getState().playMode === 'single-loop'
    audio.load()
    await audio.play()

    if (version === loadVersion) {
      usePlayerStore.setState({ isLoading: false, isPlaying: true })
    }
  } catch (error) {
    if (version !== loadVersion) return

    if (isAutoplayBlocked(error)) {
      usePlayerStore.setState({
        isLoading: false,
        isPlaying: false,
        notice: '浏览器阻止了自动播放，请再次点击播放按钮',
      })
      return
    }

    if (error instanceof BackendApiError && error.status === 401) {
      usePlayerStore.setState({
        isLoading: false,
        isPlaying: false,
        notice: '网易云登录状态已失效，请重新扫码登录后再播放',
      })
      return
    }

    const reason = error instanceof Error ? error.message : '播放地址请求失败'
    const maxAttempts = error instanceof PlaybackUnavailableError
      ? queue.length
      : Math.min(queue.length, 2)
    const hasNextCandidate = attemptedIds.size < maxAttempts
    usePlayerStore.setState({
      isLoading: false,
      isPlaying: false,
      notice: hasNextCandidate
        ? `${song.name} 暂时无法播放：${reason}，正在尝试下一首`
        : `${song.name} 暂时无法播放：${reason}`,
    })

    if (hasNextCandidate) {
      await loadAndPlay(nextIndex + 1, attemptedIds)
    }
  }
}

export const usePlayerStore = create<PlayerState>((set, get) => ({
  currentSong: null,
  queue: [],
  playlist: [],
  currentIndex: -1,
  isPlaying: false,
  isLoading: false,
  currentTime: 0,
  duration: 0,
  volume: initialVolume,
  previousVolume: initialPreviousVolume,
  isMuted: initialVolume === 0,
  playMode: 'list-loop',
  likedSongIds: new Set<number>(),
  isQueueOpen: false,
  isLyricsOpen: false,
  isAddToPlaylistOpen: false,
  isFullscreen: false,
  lyrics: [],
  activeLyricIndex: -1,
  lyricsLoading: false,
  lyricsStatus: 'idle',
  lyricsError: '',
  notice: '',

  playSong: async (song, playlist = [song]) => {
    const queue = playlist.length > 0 ? playlist : [song]
    const nextIndex = Math.max(0, queue.findIndex((item) => item.id === song.id))
    const currentState = get()
    runtimeFailureIds.clear()

    set({ queue, playlist: queue, currentIndex: nextIndex })

    if (currentState.currentSong?.id === song.id && currentState.isLoading) {
      return
    }

    if (currentState.currentSong?.id === song.id && audio.src && !currentState.isLoading) {
      await get().togglePlayback()
      return
    }

    await loadAndPlay(nextIndex)
  },

  insertNextTrack: (song) => {
    const state = get()
    if (state.currentSong?.id === song.id) return
    const nextQueue = state.queue.filter((item) => item.id !== song.id)
    let currentIndex = state.currentSong
      ? nextQueue.findIndex((item) => item.id === state.currentSong?.id)
      : -1
    if (state.currentSong && currentIndex < 0) {
      nextQueue.unshift(state.currentSong)
      currentIndex = 0
    }
    nextQueue.splice(currentIndex >= 0 ? currentIndex + 1 : 0, 0, song)
    set({
      queue: nextQueue,
      playlist: nextQueue,
      currentIndex,
    })
  },

  appendToQueue: (song) => {
    const state = get()
    if (state.queue.some((item) => item.id === song.id)) return
    const nextQueue = [...state.queue]
    let currentIndex = state.currentIndex
    if (state.currentSong && currentIndex < 0) {
      nextQueue.unshift(state.currentSong)
      currentIndex = 0
    }
    nextQueue.push(song)
    set({ queue: nextQueue, playlist: nextQueue, currentIndex })
  },

  replaceQueueAndPlay: async (tracks) => {
    if (tracks.length === 0) return
    const seen = new Set<number>()
    const queue = tracks.filter((track) => {
      if (seen.has(track.id)) return false
      seen.add(track.id)
      return true
    })
    if (queue.length === 0) return
    runtimeFailureIds.clear()
    set({ queue, playlist: queue, currentIndex: 0 })
    await loadAndPlay(0)
  },

  togglePlayback: async () => {
    const state = get()
    if (state.isPlaying) {
      get().pausePlayback()
      return
    }

    await get().resumePlayback()
  },

  pausePlayback: () => {
    const state = get()
    if (!state.currentSong) return
    if (state.isLoading) loadVersion += 1
    audio.pause()
    set({ isPlaying: false, isLoading: false })
  },

  resumePlayback: async () => {
    const state = get()
    if (!state.currentSong || state.isLoading || state.isPlaying) return

    if (!audio.src) {
      await loadAndPlay(Math.max(0, state.currentIndex))
      return
    }

    try {
      await audio.play()
      set({ isPlaying: true, notice: '' })
    } catch {
      set({ isPlaying: false, notice: '播放失败，请稍后重试或选择其他歌曲' })
    }
  },

  playPrevious: async () => {
    const state = get()
    if (state.queue.length === 0) return
    runtimeFailureIds.clear()

    if (state.playMode === 'shuffle') {
      await loadAndPlay(randomQueueIndex(state.queue.length, state.currentIndex))
      return
    }

    if (state.playMode === 'sequence' && state.currentIndex <= 0) {
      get().seek(0)
      return
    }

    await loadAndPlay(state.currentIndex - 1)
  },

  playNext: async () => {
    const state = get()
    if (state.queue.length === 0) return
    runtimeFailureIds.clear()

    if (state.playMode === 'shuffle') {
      await loadAndPlay(randomQueueIndex(state.queue.length, state.currentIndex))
      return
    }

    if (state.playMode === 'sequence' && state.currentIndex >= state.queue.length - 1) {
      audio.pause()
      set({ isPlaying: false })
      return
    }

    await loadAndPlay(state.currentIndex + 1)
  },

  seek: (time) => {
    const duration = Number.isFinite(audio.duration) ? audio.duration : get().duration
    const nextTime = Math.min(Math.max(time, 0), Math.max(duration, 0))
    audio.currentTime = nextTime
    set({ currentTime: nextTime })
  },

  setVolume: (volume) => {
    const nextVolume = clampVolume(volume)
    const previousVolume = nextVolume > 0 ? nextVolume : get().previousVolume
    const isMuted = nextVolume === 0

    set({ volume: nextVolume, previousVolume, isMuted })
    persistVolume(nextVolume, previousVolume)
    audio.volume = nextVolume
    audio.muted = isMuted
  },

  toggleMute: () => {
    const state = get()
    get().setMuted(!state.isMuted)
  },

  setMuted: (muted) => {
    const state = get()
    if (muted === state.isMuted) return
    const nextVolume = muted
      ? 0
      : clampVolume(state.previousVolume || DEFAULT_VOLUME)
    const previousVolume = muted
      ? state.volume || state.previousVolume || DEFAULT_VOLUME
      : nextVolume

    set({
      volume: nextVolume,
      previousVolume,
      isMuted: muted,
    })
    persistVolume(nextVolume, previousVolume)
    audio.volume = nextVolume
    audio.muted = muted
  },

  setPlayMode: (mode) => {
    audio.loop = mode === 'single-loop'
    set({ playMode: mode })
  },

  togglePlayMode: () => {
    const currentModeIndex = PLAY_MODES.indexOf(get().playMode)
    const nextMode = PLAY_MODES[(currentModeIndex + 1) % PLAY_MODES.length]
    get().setPlayMode(nextMode)
  },

  toggleLike: async (songId) => {
    await get().setLiked(songId, !get().likedSongIds.has(songId))
  },

  setLiked: async (songId, liked) => {
    const previousIds = get().likedSongIds
    if (previousIds.has(songId) === liked) return
    const nextIds = new Set(previousIds)

    if (liked) nextIds.add(songId)
    else nextIds.delete(songId)

    set({ likedSongIds: nextIds })

    try {
      await syncSongLikePreference(songId, liked)
    } catch {
      set({
        notice: '喜欢状态已在播放器中更新，网易云同步暂时失败',
      })
    }
  },

  playQueueItem: async (index) => {
    const state = get()
    if (index < 0 || index >= state.queue.length || state.isLoading) return
    runtimeFailureIds.clear()

    if (index === state.currentIndex && state.currentSong) {
      if (!state.isPlaying) await state.togglePlayback()
      return
    }

    await loadAndPlay(index)
  },

  removeQueueItem: async (index) => {
    const state = get()
    if (index < 0 || index >= state.queue.length) return

    const nextQueue = state.queue.filter((_, queueIndex) => queueIndex !== index)
    if (nextQueue.length === 0) {
      get().clearQueue()
      return
    }

    if (index === state.currentIndex) {
      const nextIndex = Math.min(index, nextQueue.length - 1)
      set({ queue: nextQueue, playlist: nextQueue, currentIndex: nextIndex })
      runtimeFailureIds.clear()
      await loadAndPlay(nextIndex)
      return
    }

    const nextIndex = index < state.currentIndex
      ? state.currentIndex - 1
      : state.currentIndex
    set({ queue: nextQueue, playlist: nextQueue, currentIndex: nextIndex })
  },

  clearQueue: () => {
    loadVersion += 1
    runtimeFailureIds.clear()
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    set({
      currentSong: null,
      queue: [],
      playlist: [],
      currentIndex: -1,
      isPlaying: false,
      isLoading: false,
      currentTime: 0,
      duration: 0,
      lyrics: [],
      activeLyricIndex: -1,
      lyricsLoading: false,
      lyricsStatus: 'idle',
      lyricsError: '',
      notice: '',
    })
  },

  clearQueueExceptCurrent: () => {
    const state = get()
    if (!state.currentSong) {
      get().clearQueue()
      return
    }

    const currentQueueSong = state.queue[state.currentIndex]
    const currentSong = currentQueueSong?.id === state.currentSong.id
      ? currentQueueSong
      : state.currentSong

    set({
      queue: [currentSong],
      playlist: [currentSong],
      currentIndex: 0,
    })
  },

  reorderQueue: (fromIndex, toIndex) => {
    const state = get()
    if (
      fromIndex === toIndex
      || fromIndex < 0
      || toIndex < 0
      || fromIndex >= state.queue.length
      || toIndex >= state.queue.length
    ) return

    const currentQueueSong = state.queue[state.currentIndex]
    const nextQueue = [...state.queue]
    const [movedSong] = nextQueue.splice(fromIndex, 1)
    nextQueue.splice(toIndex, 0, movedSong)

    set({
      queue: nextQueue,
      playlist: nextQueue,
      currentIndex: currentQueueSong ? nextQueue.indexOf(currentQueueSong) : -1,
    })
  },

  toggleQueue: () => set((state) => ({
    isQueueOpen: !state.isQueueOpen,
    isAddToPlaylistOpen: false,
  })),
  closeQueue: () => set({ isQueueOpen: false }),
  toggleLyrics: () => set((state) => ({ isLyricsOpen: !state.isLyricsOpen })),
  openLyrics: () => set({ isLyricsOpen: true }),
  closeLyrics: () => set({ isLyricsOpen: false }),
  openAddToPlaylist: () => set({
    isAddToPlaylistOpen: true,
    isQueueOpen: false,
  }),
  closeAddToPlaylist: () => set({ isAddToPlaylistOpen: false }),
  setFullscreen: (fullscreen) => set({
    isFullscreen: fullscreen,
    isQueueOpen: fullscreen ? false : get().isQueueOpen,
    isAddToPlaylistOpen: fullscreen ? false : get().isAddToPlaylistOpen,
  }),
  beginLyricsLoad: () => set({
    lyrics: [],
    activeLyricIndex: -1,
    lyricsLoading: true,
    lyricsStatus: 'loading',
    lyricsError: '',
  }),
  setLyricsResult: (lyrics, status) => set({
    lyrics,
    activeLyricIndex: -1,
    lyricsLoading: false,
    lyricsStatus: status,
    lyricsError: '',
  }),
  setLyricsError: (message) => set({
    lyrics: [],
    activeLyricIndex: -1,
    lyricsLoading: false,
    lyricsStatus: 'error',
    lyricsError: message,
  }),
  resetLyrics: () => set({
    lyrics: [],
    activeLyricIndex: -1,
    lyricsLoading: false,
    lyricsStatus: 'idle',
    lyricsError: '',
  }),
  setActiveLyricIndex: (index) => set({ activeLyricIndex: index }),

  clearNotice: () => set({ notice: '' }),
}))

audio.addEventListener('timeupdate', () => {
  usePlayerStore.setState({ currentTime: audio.currentTime || 0 })
})

function updateDuration() {
  const currentSong = usePlayerStore.getState().currentSong
  usePlayerStore.setState({
    duration: Number.isFinite(audio.duration) ? audio.duration : fallbackDuration(currentSong),
  })
}

audio.addEventListener('loadedmetadata', updateDuration)
audio.addEventListener('durationchange', updateDuration)

audio.addEventListener('play', () => {
  usePlayerStore.setState({ isPlaying: true, isLoading: false })
})

audio.addEventListener('pause', () => {
  usePlayerStore.setState({ isPlaying: false })
})

audio.addEventListener('volumechange', () => {
  const state = usePlayerStore.getState()
  const nextVolume = audio.muted ? 0 : clampVolume(audio.volume)
  const previousVolume = nextVolume > 0 ? nextVolume : state.previousVolume

  persistVolume(nextVolume, previousVolume)
  usePlayerStore.setState({
    volume: nextVolume,
    previousVolume,
    isMuted: audio.muted || nextVolume === 0,
  })
})

audio.addEventListener('ended', () => {
  const state = usePlayerStore.getState()

  if (state.playMode === 'single-loop') {
    audio.currentTime = 0
    void audio.play()
    return
  }

  if (state.playMode === 'sequence' && state.currentIndex >= state.queue.length - 1) {
    usePlayerStore.setState({
      currentTime: state.duration,
      isPlaying: false,
    })
    return
  }

  void state.playNext()
})

audio.addEventListener('error', () => {
  const state = usePlayerStore.getState()
  if (!state.currentSong || state.isLoading || !audio.src) return

  runtimeFailureIds.add(state.currentSong.id)

  setTimeout(() => {
    const latest = usePlayerStore.getState()
    if (latest.currentSong?.id !== state.currentSong?.id) return

    if (runtimeFailureIds.size >= latest.queue.length) {
      usePlayerStore.setState({
        isPlaying: false,
        notice: '当前歌单中暂时没有可连续播放的歌曲',
      })
      return
    }

    usePlayerStore.setState({
      isPlaying: false,
      notice: `${state.currentSong?.name} 播放中断，正在尝试下一首`,
    })
    void loadAndPlay(latest.currentIndex + 1, new Set(runtimeFailureIds))
  }, 300)
})
