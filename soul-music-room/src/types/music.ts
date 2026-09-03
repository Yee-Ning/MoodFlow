export interface UserProfile {
  userId: number
  nickname: string
  avatarUrl: string
}

export interface PlaylistSummary {
  id: number
  name: string
  coverImgUrl: string
  trackCount: number
  creator: string
  subscribed: boolean
}

export interface SongSummary {
  id: number
  name: string
  artists: string[]
  album: string
  coverUrl: string
  duration: number
}

export interface PlaylistDetail {
  playlist: PlaylistSummary
  description: string
  songs: SongSummary[]
}

export interface SongPlaybackSource {
  songId: number
  url: string
  available: boolean
  message: string
}

export interface LyricLine {
  time: number
  text: string
  translation?: string
}

export type LyricsStatus = 'idle' | 'loading' | 'ready' | 'empty' | 'instrumental' | 'error'
