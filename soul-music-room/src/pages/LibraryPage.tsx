import { Library, ListMusic, LogIn, Music2, RefreshCw, Waves } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import LiquidGlassSurface from '../components/liquid-glass/LiquidGlassSurface'
import NeteaseImage from '../components/music/NeteaseImage'
import {
  getCurrentUserPlaylists,
  getCurrentUserProfile,
  MusicLibraryApiError,
} from '../api/musicLibrary'
import type { PlaylistSummary, UserProfile } from '../types/music'
import './MusicLibraryPages.css'
import './LibraryPage.css'

type PageStatus = 'loading' | 'ready' | 'unauthenticated' | 'error'

function PlaylistCard({ playlist }: { playlist: PlaylistSummary }) {
  const navigate = useNavigate()

  return (
    <button
      className="library-playlist-card"
      type="button"
      onClick={() => navigate(`/playlist/${playlist.id}`)}
      aria-label={`打开歌单 ${playlist.name}`}
    >
      <span className="library-playlist-card__cover">
        <NeteaseImage
          source={playlist.coverImgUrl}
          size={420}
          alt=""
          loading="lazy"
          fallback={<Music2 size={34} strokeWidth={1.2} aria-hidden="true" />}
        />
        <span className="library-playlist-card__count">
          <ListMusic size={13} strokeWidth={1.6} aria-hidden="true" />
          {playlist.trackCount}
        </span>
      </span>
      <span className="library-playlist-card__meta">
        <strong>{playlist.name}</strong>
        <small>{playlist.creator}</small>
      </span>
    </button>
  )
}

function EmptyCollection({ message }: { message: string }) {
  return (
    <div className="library-empty">
      <Music2 size={24} strokeWidth={1.2} aria-hidden="true" />
      <p>{message}</p>
    </div>
  )
}

function LibraryState({ children, loading = false }: { children: ReactNode; loading?: boolean }) {
  return (
    <LiquidGlassSurface
      className={`library-state${loading ? ' library-state--loading' : ''}`}
      preset="overlay"
      radius={34}
      intensity={0.78}
    >
      {children}
    </LiquidGlassSurface>
  )
}

function LibraryPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<PageStatus>('loading')
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [playlists, setPlaylists] = useState<PlaylistSummary[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    Promise.all([
      getCurrentUserProfile(controller.signal),
      getCurrentUserPlaylists(controller.signal),
    ])
      .then(([nextProfile, nextPlaylists]) => {
        setProfile(nextProfile)
        setPlaylists(nextPlaylists)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        if (error instanceof MusicLibraryApiError && error.status === 401) {
          setStatus('unauthenticated')
          return
        }
        setErrorMessage(error instanceof Error ? error.message : '音乐库加载失败')
        setStatus('error')
      })

    return () => controller.abort()
  }, [reloadKey])

  const retry = () => {
    setStatus('loading')
    setErrorMessage('')
    setReloadKey((key) => key + 1)
  }

  const createdPlaylists = useMemo(
    () => playlists.filter((playlist) => !playlist.subscribed),
    [playlists],
  )
  const subscribedPlaylists = useMemo(
    () => playlists.filter((playlist) => playlist.subscribed),
    [playlists],
  )

  if (status === 'loading') {
    return (
      <main className="music-library-page music-library-page--centered" aria-busy="true">
        <LibraryState loading>
          <span className="library-loader"><Waves size={26} strokeWidth={1.3} /></span>
          <p>正在同步你的音乐海域…</p>
        </LibraryState>
      </main>
    )
  }

  if (status === 'unauthenticated') {
    return (
      <main className="music-library-page music-library-page--centered">
        <LibraryState>
          <span><LogIn size={30} strokeWidth={1.25} /></span>
          <p className="library-state__eyebrow">YOUR PRIVATE TIDE</p>
          <h1>登录后同步音乐库</h1>
          <p>使用网易云音乐扫码登录，即可查看你创建和收藏的歌单。</p>
          <button type="button" onClick={() => navigate('/login')}>前往扫码登录</button>
        </LibraryState>
      </main>
    )
  }

  if (status === 'error') {
    return (
      <main className="music-library-page music-library-page--centered">
        <LibraryState>
          <span><RefreshCw size={29} strokeWidth={1.25} /></span>
          <p className="library-state__eyebrow">SIGNAL LOST</p>
          <h1>音乐库暂时没有回应</h1>
          <p>{errorMessage}</p>
          <button type="button" onClick={retry}>重新加载</button>
        </LibraryState>
      </main>
    )
  }

  return (
    <main className="music-library-page music-library-page--library">
      <div className="music-library-page__ambient" aria-hidden="true" />
      <header className="library-profile">
        <div className="library-profile__avatar">
          <NeteaseImage
            source={profile?.avatarUrl ?? ''}
            size={240}
            alt={`${profile?.nickname ?? '用户'}的头像`}
            fallback={<Library size={34} strokeWidth={1.25} aria-hidden="true" />}
          />
        </div>
        <div>
          <p>YOUR NETEASE LIBRARY</p>
          <h1>{profile?.nickname}</h1>
          <span>{playlists.length} 个歌单已同步</span>
        </div>
      </header>

      <section className="library-section" aria-labelledby="created-playlists-title">
        <div className="library-section__heading">
          <div>
            <p>MADE BY YOU</p>
            <h2 id="created-playlists-title">我创建的歌单</h2>
          </div>
          <span>{createdPlaylists.length} 个歌单</span>
        </div>
        {createdPlaylists.length > 0 ? (
          <div className="library-playlist-grid">
            {createdPlaylists.map((playlist, index) => (
              <PlaylistCard key={`${playlist.id}-${index}`} playlist={playlist} />
            ))}
          </div>
        ) : (
          <EmptyCollection message="这里还没有你创建的歌单" />
        )}
      </section>

      <section className="library-section" aria-labelledby="subscribed-playlists-title">
        <div className="library-section__heading">
          <div>
            <p>COLLECTED TIDES</p>
            <h2 id="subscribed-playlists-title">我收藏的歌单</h2>
          </div>
          <span>{subscribedPlaylists.length} 个歌单</span>
        </div>
        {subscribedPlaylists.length > 0 ? (
          <div className="library-playlist-grid">
            {subscribedPlaylists.map((playlist, index) => (
              <PlaylistCard key={`${playlist.id}-${index}`} playlist={playlist} />
            ))}
          </div>
        ) : (
          <EmptyCollection message="这里还没有收藏的歌单" />
        )}
      </section>
    </main>
  )
}

export default LibraryPage
