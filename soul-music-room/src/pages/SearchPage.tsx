import { ArrowUpRight, AudioLines, Disc3, ListMusic, LoaderCircle, Music2, Play, Plus, Search, UserRound, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ENTITY_TYPES, getAlbumDetail, parseSearchCategory, searchUrl, songFromEntity, type SearchCategory } from '../api/musicSearch'
import NeteaseImage from '../components/music/NeteaseImage'
import { playSong } from '../services/player'
import { useAuthStore } from '../store/authStore'
import { usePlayerStore } from '../store/playerStore'
import { useSearchStore } from '../store/searchStore'
import type { AlbumDetail, MusicEntity, MusicEntityType, SongSummary } from '../types/music'
import './SearchPage.css'

const categories: { type: SearchCategory; label: string }[] = [
  { type: 'all', label: '全部' }, { type: 'song', label: '歌曲' },
  { type: 'artist', label: '歌手' }, { type: 'album', label: '专辑' }, { type: 'playlist', label: '歌单' },
]
const labels: Record<MusicEntityType, string> = { song: '歌曲', artist: '歌手', album: '专辑', playlist: '歌单' }

function formatDuration(duration: number) {
  const seconds = Math.max(0, Math.floor(duration / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

interface SongResultProps {
  song: SongSummary
  onPlay: () => void
  onQueue: () => void
}

function SongResult({ song, onPlay, onQueue }: SongResultProps) {
  const active = usePlayerStore((state) => state.currentSong?.id === song.id)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  return (
    <article className={`search-song${active ? ' is-active' : ''}`}>
      <button className="search-song__play" type="button" onClick={onPlay} aria-label={`播放 ${song.name}`}>
        <NeteaseImage source={song.coverUrl} size={128} alt="" loading="lazy" fallback={<Music2 size={24} />} />
        <span>{active && isPlaying ? <AudioLines size={19} /> : <Play size={19} fill="currentColor" />}</span>
      </button>
      <div className="search-song__identity"><h3>{song.name}</h3><p>{song.artists.join('、') || '未知歌手'}</p></div>
      <p className="search-song__album">{song.album || '未知专辑'}</p>
      <span className="search-song__duration">{song.duration ? formatDuration(song.duration) : '—'}</span>
      <button className="search-icon-button" type="button" onClick={onQueue} aria-label={`加入队列 ${song.name}`} title="加入播放队列">
        <Plus size={18} />
      </button>
    </article>
  )
}

function AlbumPreview({ entity, onClose, onPlay, onQueue }: {
  entity: MusicEntity
  onClose: () => void
  onPlay: (song: SongSummary, songs: SongSummary[]) => void
  onQueue: (song: SongSummary) => void
}) {
  const [detail, setDetail] = useState<AlbumDetail | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    headingRef.current?.focus()
    const controller = new AbortController()
    void getAlbumDetail(entity.id, controller.signal).then((value) => {
      if (!controller.signal.aborted) setDetail(value)
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '专辑加载失败')
    })
    return () => controller.abort()
  }, [entity.id, attempt])
  return (
    <section className="search-album-preview" aria-label={`${entity.name} 专辑歌曲`}>
      <header><div><p>专辑 · {entity.artist}</p><h2 ref={headingRef} tabIndex={-1}>{entity.name}</h2></div>
        <button className="search-icon-button" type="button" onClick={onClose} aria-label="关闭专辑歌曲"><X size={20} /></button>
      </header>
      {!detail && !error && <p role="status"><LoaderCircle className="search-spinner" size={18} /> 正在加载专辑歌曲…</p>}
      {error && <div role="alert"><p>{error}</p><button className="search-text-button" type="button" onClick={() => { setError(''); setAttempt((value) => value + 1) }}>重新加载专辑</button></div>}
      {detail && <>
        <div className="search-album-preview__actions"><span>{detail.songs.length} 首歌曲</span>
          <button className="search-text-button" type="button" disabled={!detail.songs.length} onClick={() => onPlay(detail.songs[0], detail.songs)}><Play size={15} /> 播放专辑</button>
        </div>
        {detail.songs.length ? detail.songs.map((song) => <SongResult key={song.id} song={song}
          onPlay={() => onPlay(song, detail.songs)} onQueue={() => onQueue(song)} />) : <p>这张专辑暂时没有歌曲。</p>}
      </>}
    </section>
  )
}

function SearchPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const query = (params.get('q') ?? '').trim()
  const category = parseSearchCategory(params.get('type'))
  const search = useSearchStore()
  const loadSearch = search.loadSearch
  const loggedIn = useAuthStore((state) => state.loggedIn)
  const [notice, setNotice] = useState<{ key: string; message: string; login?: boolean } | null>(null)
  const [selectedAlbum, setSelectedAlbum] = useState<{ key: string; entity: MusicEntity } | null>(null)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const searchKey = `${query}:${category}`
  const isCurrent = search.query === query && search.category === category
  const loading = Boolean(query) && (!isCurrent || search.status === 'loading')
  const types = category === 'all' ? ENTITY_TYPES : [category]
  const total = isCurrent ? types.reduce((count, type) => count + search.results[type].length, 0) : 0
  const hasErrors = isCurrent && Object.keys(search.errors).length > 0

  useEffect(() => loadSearch(query, category), [query, category, loadSearch, searchAttempt])

  const selectCategory = (next: SearchCategory) => navigate(searchUrl(query, next))
  const requestPlayback = (song: SongSummary, songs: SongSummary[]) => {
    if (!loggedIn) {
      setNotice({ key: searchKey, message: '登录网易云音乐后即可播放搜索结果。', login: true })
      return
    }
    setNotice(null)
    void playSong(song, songs)
  }
  const queueSong = (song: SongSummary) => {
    usePlayerStore.getState().appendToQueue(song)
    setNotice({ key: searchKey, message: `已将“${song.name}”加入播放队列。` })
  }

  return (
    <main className="search-page" aria-busy={loading}>
      <header className="search-page__heading">
        <p><Search size={17} aria-hidden="true" /> SEARCH THE OCEAN</p>
        <h1>{query ? '搜索结果' : 'Search'}</h1>
        <div>{query ? <>寻找“<strong>{query}</strong>”</> : '从一首歌、一位歌手或一张专辑开始。'}</div>
      </header>
      <nav className="search-categories" aria-label="搜索分类">
        {categories.map((item) => <button key={item.type} type="button" aria-pressed={category === item.type}
          onClick={() => selectCategory(item.type)}>{item.label}</button>)}
      </nav>
      {notice?.key === searchKey && <div className="search-notice" role="status"><span>{notice.message}</span>
        {notice.login && <button className="search-text-button" type="button" onClick={() => navigate('/login')}>前往登录</button>}
      </div>}
      {!query && <section className="search-empty">
        <Search size={36} strokeWidth={1.2} aria-hidden="true" /><h2>你想听什么？</h2>
        <p>在顶部输入关键词，按回车或点击搜索。歌曲、歌手、专辑和歌单都可以找到。</p>
        <div>{['周杰伦', 'Kanye West', '学习'].map((keyword) => <button className="search-text-button" key={keyword}
          type="button" onClick={() => navigate(searchUrl(keyword, category))}>{keyword}<ArrowUpRight size={14} /></button>)}</div>
      </section>}
      {loading && <section className="search-empty" role="status"><LoaderCircle className="search-spinner" size={28} /><h2>正在寻找音乐…</h2><p>搜索“{query}”</p></section>}
      {query && !loading && isCurrent && <>
        <div className="search-summary" aria-live="polite"><span>{total} 条结果{category !== 'all' ? ' · 最多展示 50 条' : ''}</span>
          {hasErrors && <button className="search-text-button" type="button" onClick={() => setSearchAttempt((value) => value + 1)}>重试搜索</button>}
        </div>
        {selectedAlbum?.key === searchKey && <AlbumPreview key={`${searchKey}:${selectedAlbum.entity.id}`} entity={selectedAlbum.entity}
          onClose={() => setSelectedAlbum(null)} onPlay={requestPlayback} onQueue={queueSong} />}
        {total === 0 && !hasErrors && <section className="search-empty"><Music2 size={32} aria-hidden="true" /><h2>没有找到相关音乐</h2><p>试试更短的关键词，或换一个分类。</p></section>}
        {types.map((type) => {
          const entities = search.results[type]
          const error = search.errors[type]
          if (!entities.length && !error) return null
          const songs = type === 'song' ? entities.map(songFromEntity) : []
          const Icon = type === 'artist' ? UserRound : type === 'playlist' ? ListMusic : Disc3
          return <section className="search-results" key={type} aria-label={`${labels[type]}搜索结果`}>
            <header><h2>{labels[type]} <span>{entities.length}</span></h2>
              {category === 'all' && entities.length > 0 && <button className="search-text-button" type="button" onClick={() => selectCategory(type)}>更多{labels[type]}<ArrowUpRight size={15} /></button>}
            </header>
            {error && <p className="search-error" role="alert">{labels[type]}暂时无法搜索：{error}</p>}
            {type === 'song' ? <div className="search-song-list">{songs.map((song) => <SongResult key={song.id} song={song}
              onPlay={() => requestPlayback(song, songs)} onQueue={() => queueSong(song)} />)}</div> :
              <div className="search-entity-grid">{entities.map((entity) => <article className={`search-entity search-entity--${type}`} key={entity.id}>
                <button className="search-entity__cover" type="button"
                  aria-label={type === 'artist' ? `搜索 ${entity.name} 的歌曲` : type === 'album' ? `查看专辑 ${entity.name} 的歌曲` : `查看歌单 ${entity.name}`}
                  onClick={() => {
                    if (type === 'artist') navigate(searchUrl(entity.name, 'song'))
                    else if (type === 'album') setSelectedAlbum({ key: searchKey, entity })
                    else navigate(`/playlist/${entity.id}`)
                  }}>
                  <NeteaseImage source={entity.coverUrl} size={400} alt="" loading="lazy" fallback={<Icon size={44} strokeWidth={1.2} />} />
                  <span><ArrowUpRight size={21} /></span>
                </button>
                <h3>{entity.name}</h3><p>{type === 'artist' ? '搜索歌手的歌曲' : entity.artist || '未知创作者'}</p>
                {type === 'playlist' && typeof entity.metadata.track_count === 'number' && <small>{entity.metadata.track_count} 首歌曲</small>}
                {type === 'album' && <small>查看专辑歌曲</small>}
              </article>)}</div>}
          </section>
        })}
      </>}
    </main>
  )
}

export default SearchPage
