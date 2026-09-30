import { ChevronLeft, ChevronRight, Search, UserRound } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { getCurrentUserProfile } from '../../api/musicLibrary'
import { parseSearchCategory, searchUrl } from '../../api/musicSearch'
import { useAuthStore } from '../../store/authStore'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import NeteaseImage from '../music/NeteaseImage'

function SearchForm({ initialQuery }: { initialQuery: string }) {
  const [query, setQuery] = useState(initialQuery)
  const navigate = useNavigate()
  const location = useLocation()
  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const category = location.pathname === '/search'
      ? parseSearchCategory(new URLSearchParams(location.search).get('type')) : 'all'
    navigate(searchUrl(query, category))
  }
  return (
    <form className="topbar__search" role="search" onSubmit={submitSearch}>
      <Search size={17} strokeWidth={1.6} aria-hidden="true" />
      <input aria-label="搜索 MoodFlow" placeholder="搜索歌曲、歌手、专辑或歌单"
        type="search" maxLength={200} value={query}
        onChange={(event) => setQuery(event.target.value)} />
      <button className="topbar__search-submit" type="submit" aria-label="提交音乐搜索">搜索</button>
    </form>
  )
}

function TopBar() {
  const [avatarUrl, setAvatarUrl] = useState('')
  const navigate = useNavigate()
  const location = useLocation()
  const searchQuery = location.pathname === '/search' ? new URLSearchParams(location.search).get('q') ?? '' : ''
  const loggedIn = useAuthStore((state) => state.loggedIn)

  useEffect(() => {
    if (!loggedIn) return
    const controller = new AbortController()
    getCurrentUserProfile(controller.signal)
      .then((profile) => setAvatarUrl(profile.avatarUrl))
      .catch(() => {
        // The account shortcut remains usable with its icon fallback.
      })
    return () => controller.abort()
  }, [loggedIn])

  return (
    <header className="topbar">
      <LiquidGlassSurface className="topbar__surface" preset="panel" radius={24} intensity={0.56}>
        <div className="topbar__history" aria-label="页面历史">
          <button type="button" onClick={() => navigate(-1)} aria-label="返回上一页">
            <ChevronLeft size={19} strokeWidth={1.7} />
          </button>
          <button type="button" onClick={() => navigate(1)} aria-label="前往下一页">
            <ChevronRight size={19} strokeWidth={1.7} />
          </button>
        </div>

        <SearchForm key={searchQuery} initialQuery={searchQuery} />

        <button
          className={`topbar__profile${loggedIn ? ' is-authenticated' : ''}`}
          type="button"
          aria-label={loggedIn ? '已登录，查看账户' : '打开登录页面'}
          onClick={() => navigate('/login')}
        >
          {loggedIn && avatarUrl ? (
            <NeteaseImage
              source={avatarUrl}
              size={96}
              alt="当前用户头像"
              fallback={<UserRound size={18} strokeWidth={1.6} aria-hidden="true" />}
            />
          ) : (
            <UserRound size={18} strokeWidth={1.6} aria-hidden="true" />
          )}
          {loggedIn && <span className="topbar__profile-status" aria-hidden="true" />}
        </button>
      </LiquidGlassSurface>
    </header>
  )
}

export default TopBar
