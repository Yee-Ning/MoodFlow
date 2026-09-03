import {
  Compass,
  Disc3,
  Heart,
  History,
  Library,
  Music2,
  Search,
  Sparkles,
} from 'lucide-react'
import { useLocation } from 'react-router-dom'
import LiquidGlassSurface from '../components/liquid-glass/LiquidGlassSurface'
import './StaticDestinationPage.css'

const pages = {
  discover: {
    eyebrow: 'FIND YOUR NEXT FAVORITE',
    title: 'Discover',
    description: '热门歌曲、精选歌单、新发行与值得慢慢认识的音乐人。',
    icon: Compass,
    prompts: ['Trending Now', 'Editor Picks', 'Fresh Releases'],
  },
  session: {
    eyebrow: 'SET THE MOMENT',
    title: 'Create a Vibe',
    description: '选择场景、时长、情绪和偏好，构想一段属于此刻的播放体验。',
    icon: Sparkles,
    prompts: ['Scene', 'Duration', 'Mood'],
  },
  worldline: {
    eyebrow: 'ONE SONG, MANY DIRECTIONS',
    title: 'Music Worldline',
    description: '从一首歌曲出发，沿风格、情绪、乐器与年代浏览不同的音乐方向。',
    icon: Disc3,
    prompts: ['Style Route', 'Mood Route', 'Era Route'],
  },
  search: {
    eyebrow: 'SEARCH THE OCEAN',
    title: 'Search',
    description: '搜索歌曲、艺人、专辑或歌单。当前阶段展示静态搜索入口。',
    icon: Search,
    prompts: ['Songs', 'Artists', 'Albums'],
  },
  library: {
    eyebrow: 'YOUR COLLECTION',
    title: 'Library',
    description: '你保存的音乐将在这里汇集。',
    icon: Library,
    prompts: ['Playlists', 'Albums', 'Artists'],
  },
  liked: {
    eyebrow: 'SAVED FOR LATER',
    title: 'Liked Songs',
    description: '喜欢的歌曲会停靠在这里。',
    icon: Heart,
    prompts: ['Recently Liked', 'Most Played', 'Slow Favorites'],
  },
  history: {
    eyebrow: 'RECENT TIDES',
    title: 'Recently Played',
    description: '回到最近听过的声音，从熟悉的位置继续。',
    icon: History,
    prompts: ['Today', 'This Week', 'Earlier'],
  },
} as const

type PageKey = keyof typeof pages

interface StaticDestinationPageProps {
  page: PageKey
}

function StaticDestinationPage({ page }: StaticDestinationPageProps) {
  const location = useLocation()
  const current = pages[page]
  const Icon = current.icon
  const searchQuery = (location.state as { query?: string } | null)?.query

  return (
    <main className="destination-page">
      <div className="destination-page__glow" aria-hidden="true" />
      <section className="destination-page__hero">
        <LiquidGlassSurface className="destination-page__icon" preset="control" radius={20} intensity={0.72}>
          <Icon size={28} strokeWidth={1.35} />
        </LiquidGlassSurface>
        <p>{current.eyebrow}</p>
        <h1>{current.title}</h1>
        <div>{current.description}</div>
        {searchQuery && <strong>正在搜索“{searchQuery}”</strong>}
      </section>

      <section className="destination-page__cards" aria-label={`${current.title} categories`}>
        {current.prompts.map((prompt, index) => (
          <LiquidGlassSurface
            className="destination-page__card"
            preset="panel"
            radius={25}
            intensity={0.68}
            key={prompt}
          >
            <article>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <Music2 size={18} strokeWidth={1.4} aria-hidden="true" />
              <h2>{prompt}</h2>
              <p>Static preview · coming in a future phase</p>
            </article>
          </LiquidGlassSurface>
        ))}
      </section>
    </main>
  )
}

export default StaticDestinationPage
