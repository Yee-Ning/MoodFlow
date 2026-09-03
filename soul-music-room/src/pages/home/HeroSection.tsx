import { ArrowRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useReducedMotion } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import LiquidGlassSurface from '../../components/liquid-glass/LiquidGlassSurface'

const entrances = [
  {
    title: 'DISCOVER',
    subtitle: 'Music picked for you',
    description: '探索热门歌曲、精选歌单、新发行专辑和推荐音乐人。',
    action: 'EXPLORE MUSIC',
    path: '/discover',
  },
  {
    title: 'CREATE A VIBE',
    subtitle: 'Music for this moment',
    description: '根据场景、时长、情绪和音乐偏好创建专属歌单。',
    action: 'CREATE A SESSION',
    path: '/session/create',
  },
  {
    title: 'MUSIC WORLDLINE',
    subtitle: 'One song, infinite directions',
    description: '从一首歌曲出发，沿风格、情绪、乐器和年代探索不同音乐路线。',
    action: 'START EXPLORING',
    path: '/worldline',
  },
] as const

function HeroSection() {
  const navigationTimerRef = useRef<number | null>(null)
  const [pendingPath, setPendingPath] = useState<string | null>(null)
  const reduceMotion = Boolean(useReducedMotion())
  const navigate = useNavigate()

  useEffect(
    () => () => {
      if (navigationTimerRef.current !== null) window.clearTimeout(navigationTimerRef.current)
    },
    [],
  )

  const openEntrance = (path: string) => {
    if (pendingPath) return
    setPendingPath(path)
    navigationTimerRef.current = window.setTimeout(
      () => navigate(path),
      reduceMotion ? 60 : 520,
    )
  }

  return (
    <section
      className={`ocean-home${pendingPath ? ' is-navigating' : ''}`}
      aria-labelledby="moodflow-title"
      aria-busy={Boolean(pendingPath)}
    >
      <div className="ocean-home__backdrop" aria-hidden="true">
        <div className="ocean-home__image" />
        <div className="ocean-home__surface" />
        <div className="ocean-home__light" />
        <div className="ocean-home__veil" />
      </div>
      <div className="ocean-home__transition-shade" aria-hidden="true" />

      <div className="ocean-home__content">
        <header className="ocean-home__header">
          <LiquidGlassSurface className="ocean-home__brand" preset="control" radius={999} intensity={0.56}>
            <p id="moodflow-title">MoodFlow</p>
            <h1>ENTER YOUR MUSIC WORLD</h1>
          </LiquidGlassSurface>
        </header>

        <div className="channel-grid" aria-label="MoodFlow core experiences">
          {entrances.map((entrance, index) => {
            const isPending = pendingPath === entrance.path
            return (
              <div
                className="channel-card__float"
                data-transition-card={isPending ? 'selected' : 'other'}
                key={entrance.path}
              >
                <LiquidGlassSurface
                  className="channel-card__glass"
                  preset="panel"
                  radius={34}
                  intensity={0.72}
                >
                  <button
                    className="channel-card"
                    type="button"
                    onClick={() => openEntrance(entrance.path)}
                    disabled={Boolean(pendingPath)}
                    aria-label={`${entrance.title}: ${entrance.action}`}
                  >
                    <span className="channel-card__index" aria-hidden="true">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="channel-card__copy">
                      <span className="channel-card__title">{entrance.title}</span>
                      <span className="channel-card__subtitle">{entrance.subtitle}</span>
                      <span className="channel-card__atmosphere">{entrance.description}</span>
                    </span>
                    <span className="channel-card__action" aria-hidden="true">
                      {isPending ? 'ENTERING' : entrance.action}
                      <ArrowRight size={15} strokeWidth={1.5} />
                    </span>
                  </button>
                </LiquidGlassSurface>
              </div>
            )
          })}
        </div>

        <p className="ocean-home__selection">ONE OCEAN. ENDLESS SOUND.</p>
      </div>
    </section>
  )
}

export default HeroSection
