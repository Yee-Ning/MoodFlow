import { AudioLines, Clock3, Disc3, LoaderCircle, LogIn, Music2, Play, RefreshCw, Shuffle, Waves } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import LiquidGlassSurface from '../components/liquid-glass/LiquidGlassSurface'
import NeteaseImage from '../components/music/NeteaseImage'
import LyricsPanel from '../components/player/LyricsPanel'
import AmbientCoverVisual from '../components/visual/AmbientCoverVisual'
import { getPlaylistDetail, MusicLibraryApiError } from '../api/musicLibrary'
import { playSong } from '../services/player'
import { usePlayerStore } from '../store/playerStore'
import type { PlaylistDetail, SongSummary } from '../types/music'
import './MusicLibraryPages.css'
import './PlaylistDetailLiquidGlass.css'

type PageStatus = 'loading' | 'ready' | 'unauthenticated' | 'error'

function formatDuration(duration: number) {
  const totalSeconds = Math.max(0, Math.floor(duration / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function shuffleSongs(songs: SongSummary[]) {
  const shuffled = [...songs]
  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1))
    const currentSong = shuffled[index]
    shuffled[index] = shuffled[swapIndex]
    shuffled[swapIndex] = currentSong
  }
  return shuffled
}

interface SongRowProps {
  song: SongSummary
  songs: SongSummary[]
  index: number
  active: boolean
  playing: boolean
  loading: boolean
}

function SongRow({ song, songs, index, active, playing, loading }: SongRowProps) {
  const row = (
    <button
      className={`playlist-song${active ? ' is-active' : ''}`}
      type="button"
      onClick={() => void playSong(song, songs)}
      aria-label={`${active && playing ? '暂停' : '播放'} ${song.name}，${song.artists.join('、')}`}
    >
      <span className="playlist-song__index">
        <span>{String(index + 1).padStart(2, '0')}</span>
        {loading ? (
          <LoaderCircle className="playlist-song__loader" size={14} aria-hidden="true" />
        ) : active && playing ? (
          <AudioLines className="playlist-song__active-icon" size={15} aria-hidden="true" />
        ) : (
          <Play size={14} fill="currentColor" aria-hidden="true" />
        )}
      </span>
      <span className="playlist-song__identity">
        <span className="playlist-song__cover">
          <NeteaseImage
            source={song.coverUrl}
            size={96}
            alt=""
            loading="lazy"
            fallback={<Music2 size={18} strokeWidth={1.3} aria-hidden="true" />}
          />
        </span>
        <span>
          <strong>{song.name}</strong>
          <small>{song.artists.join('、') || '未知歌手'}</small>
        </span>
      </span>
      <span className="playlist-song__album">{song.album || '未知专辑'}</span>
      <span className="playlist-song__duration">{formatDuration(song.duration)}</span>
    </button>
  )

  if (!active) return row

  return (
    <LiquidGlassSurface
      className="playlist-song-glass"
      preset="control"
      radius={22}
      intensity={0.84}
    >
      {row}
    </LiquidGlassSurface>
  )
}

function PageState({ children }: { children: ReactNode }) {
  return (
    <LiquidGlassSurface className="library-state" preset="overlay" radius={34} intensity={0.78}>
      {children}
    </LiquidGlassSurface>
  )
}

function PlaylistDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [status, setStatus] = useState<PageStatus>('loading')
  const [detail, setDetail] = useState<PlaylistDetail | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const currentSongId = usePlayerStore((state) => state.currentSong?.id)
  const currentSongCover = usePlayerStore((state) => state.currentSong?.coverUrl ?? '')
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const isPlayerLoading = usePlayerStore((state) => state.isLoading)
  const isLyricsOpen = usePlayerStore((state) => state.isLyricsOpen)
  const playlistId = Number(id)
  const invalidPlaylistId = !id || !Number.isSafeInteger(playlistId) || playlistId <= 0

  useEffect(() => {
    if (invalidPlaylistId) return
    const controller = new AbortController()

    getPlaylistDetail(playlistId, controller.signal)
      .then((nextDetail) => {
        setDetail(nextDetail)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (error instanceof MusicLibraryApiError && error.status === 401) {
          setStatus('unauthenticated')
          return
        }
        setErrorMessage(error instanceof Error ? error.message : '歌单加载失败')
        setStatus('error')
      })

    return () => controller.abort()
  }, [invalidPlaylistId, playlistId, reloadKey])

  const retry = () => {
    setStatus('loading')
    setErrorMessage('')
    setReloadKey((key) => key + 1)
  }

  if (status === 'loading') {
    return (
      <main className="music-library-page music-library-page--centered" aria-busy="true">
        <PageState>
          <span className="library-loader"><Waves size={26} strokeWidth={1.3} /></span>
          <p>正在拾取歌单里的声音…</p>
        </PageState>
      </main>
    )
  }

  if (status === 'unauthenticated') {
    return (
      <main className="music-library-page music-library-page--centered">
        <PageState>
          <span><LogIn size={30} strokeWidth={1.25} /></span>
          <p className="library-state__eyebrow">LOGIN REQUIRED</p>
          <h1>请先登录网易云音乐</h1>
          <p>登录后才能从当前会话中读取这张歌单。</p>
          <button type="button" onClick={() => navigate('/login')}>前往扫码登录</button>
        </PageState>
      </main>
    )
  }

  if (invalidPlaylistId || status === 'error' || !detail) {
    return (
      <main className="music-library-page music-library-page--centered">
        <PageState>
          <span><RefreshCw size={29} strokeWidth={1.25} /></span>
          <p className="library-state__eyebrow">PLAYLIST UNAVAILABLE</p>
          <h1>这张歌单暂时无法抵达</h1>
          <p>{invalidPlaylistId ? '歌单地址无效' : errorMessage}</p>
          <div className="library-state__actions">
            <button type="button" onClick={() => navigate('/library')}>返回音乐库</button>
            {!invalidPlaylistId && <button type="button" onClick={retry}>重新加载</button>}
          </div>
        </PageState>
      </main>
    )
  }

  const { playlist, description, songs } = detail

  const playAll = () => {
    if (songs.length > 0) void playSong(songs[0], songs)
  }

  const playShuffled = () => {
    const shuffled = shuffleSongs(songs)
    if (shuffled.length > 0) void playSong(shuffled[0], shuffled)
  }

  return (
    <main className="music-library-page playlist-detail-page">
      {!currentSongCover && (
        <AmbientCoverVisual artworkUrl={playlist.coverImgUrl} isPlaying={false} />
      )}
      <div className="music-library-page__ambient" aria-hidden="true" />

      <div className={`playlist-detail__layout${isLyricsOpen ? ' has-lyrics' : ''}`}>
        <div className="playlist-detail__main">
          <header className="playlist-detail__hero">
            <div className="playlist-detail__cover">
              <NeteaseImage
                source={playlist.coverImgUrl}
                size={640}
                alt={`${playlist.name} 封面`}
                fallback={<Disc3 size={58} strokeWidth={1.05} aria-hidden="true" />}
              />
            </div>
            <div className="playlist-detail__meta">
              <p>MOODFLOW PLAYLIST</p>
              <h1>{playlist.name}</h1>
              <div className="playlist-detail__facts">
                <strong>{playlist.creator}</strong>
                <span aria-hidden="true" />
                <span>{playlist.trackCount} 首歌曲</span>
              </div>
              <p className="playlist-detail__description">
                {description || '这张歌单暂时没有简介。'}
              </p>
              <div className="playlist-detail__actions">
                <button type="button" disabled={songs.length === 0} onClick={playAll}>
                  <Play size={17} fill="currentColor" aria-hidden="true" />
                  播放全部
                </button>
                <button type="button" disabled={songs.length === 0} onClick={playShuffled}>
                  <Shuffle size={17} aria-hidden="true" />
                  随机播放
                </button>
              </div>
            </div>
          </header>

          <section className="playlist-tracklist" aria-labelledby="playlist-tracks-title">
            <div className="playlist-tracklist__heading">
              <div>
                <p>TRACKLIST</p>
                <h2 id="playlist-tracks-title">曲目</h2>
              </div>
              <span>{songs.length} 首歌曲</span>
            </div>

            {songs.length > 0 ? (
              <div className="playlist-tracklist__table">
                <div className="playlist-tracklist__labels" aria-hidden="true">
                  <span>#</span>
                  <span>歌曲</span>
                  <span>专辑</span>
                  <span><Clock3 size={14} strokeWidth={1.5} /></span>
                </div>
                <div>
                  {songs.map((song, index) => (
                    <SongRow
                      key={`${song.id}-${index}`}
                      song={song}
                      songs={songs}
                      index={index}
                      active={currentSongId === song.id}
                      playing={currentSongId === song.id && isPlaying}
                      loading={currentSongId === song.id && isPlayerLoading}
                    />
                  ))}
                </div>
              </div>
            ) : (
              <div className="library-empty library-empty--tracks">
                <Music2 size={26} strokeWidth={1.2} aria-hidden="true" />
                <p>这张歌单里暂时没有歌曲</p>
              </div>
            )}
          </section>
        </div>

        {isLyricsOpen && <LyricsPanel presentation="stage" />}
      </div>
    </main>
  )
}

export default PlaylistDetailPage
