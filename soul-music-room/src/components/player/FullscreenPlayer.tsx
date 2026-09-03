import { Disc3, Heart, Minimize2 } from 'lucide-react'
import type { CSSProperties } from 'react'
import NeteaseImage from '../music/NeteaseImage'
import AmbientCoverVisual from '../visual/AmbientCoverVisual'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import { usePlayerStore } from '../../store/playerStore'
import GlassButton from './GlassButton'
import PlayerControls from './PlayerControls'
import PlayerProgress from './PlayerProgress'
import PlayModeButton from './PlayModeButton'
import VolumeControl from './VolumeControl'

interface FullscreenPlayerProps {
  onExit: () => Promise<void>
}

function FullscreenPlayer({ onExit }: FullscreenPlayerProps) {
  const isFullscreen = usePlayerStore((state) => state.isFullscreen)
  const currentSong = usePlayerStore((state) => state.currentSong)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const lyrics = usePlayerStore((state) => state.lyrics)
  const activeLyricIndex = usePlayerStore((state) => state.activeLyricIndex)
  const liked = usePlayerStore((state) => (
    currentSong ? state.likedSongIds.has(currentSong.id) : false
  ))
  const toggleLike = usePlayerStore((state) => state.toggleLike)
  const activeLyric = activeLyricIndex >= 0 ? lyrics[activeLyricIndex] : undefined
  const previousLyric = activeLyricIndex > 0 ? lyrics[activeLyricIndex - 1] : undefined
  const nextLyric = activeLyricIndex >= 0 ? lyrics[activeLyricIndex + 1] : lyrics[0]

  if (!isFullscreen) return null

  const backgroundStyle = currentSong?.coverUrl
    ? { '--fullscreen-art': `url("${currentSong.coverUrl}")` } as CSSProperties
    : undefined

  return (
    <div className="fullscreen-player" style={backgroundStyle} role="dialog" aria-modal="true" aria-label="沉浸式全屏播放器">
      <AmbientCoverVisual artworkUrl={currentSong?.coverUrl ?? ''} isPlaying={isPlaying} />
      <div className="fullscreen-player__backdrop" aria-hidden="true" />
      <header className="fullscreen-player__header">
        <div>
          <span className="fullscreen-player__brand-mark"><Disc3 size={18} aria-hidden="true" /></span>
          <span>MOODFLOW · IMMERSIVE PLAYER</span>
        </div>
        <GlassButton tooltip="退出全屏" aria-label="退出全屏播放器" onClick={() => void onExit()}>
          <Minimize2 size={19} aria-hidden="true" />
        </GlassButton>
      </header>

      <main className="fullscreen-player__content">
        <div className={isPlaying ? 'fullscreen-player__art is-playing' : 'fullscreen-player__art'}>
          <NeteaseImage
            source={currentSong?.coverUrl ?? ''}
            size={900}
            alt={currentSong ? `${currentSong.name} 封面` : ''}
            fallback={<Disc3 size={82} strokeWidth={0.9} aria-hidden="true" />}
          />
        </div>

        <section className="fullscreen-player__info" aria-label="当前歌曲与播放控制">
          <LiquidGlassSurface
            className="fullscreen-player__info-liquid"
            preset="overlay"
            radius={42}
            intensity={0.82}
          >
            <span />
          </LiquidGlassSurface>
          <p>NOW PLAYING</p>
          <div className="fullscreen-player__title-row">
            <div>
              <h1>{currentSong?.name ?? '暂无播放歌曲'}</h1>
              <span>{currentSong?.artists.join('、') || 'MoodFlow'}</span>
            </div>
            <GlassButton
              tooltip={liked ? '取消喜欢' : '喜欢这首歌'}
              variant={liked ? 'liked' : 'default'}
              aria-label={liked ? '取消喜欢当前歌曲' : '喜欢当前歌曲'}
              aria-pressed={liked}
              disabled={!currentSong}
              onClick={() => currentSong && void toggleLike(currentSong.id)}
            >
              <Heart size={19} fill={liked ? 'currentColor' : 'none'} aria-hidden="true" />
            </GlassButton>
          </div>

          <div className="fullscreen-player__lyric" aria-live="polite">
            {previousLyric && <span className="fullscreen-player__lyric-context">{previousLyric.text}</span>}
            <strong>{activeLyric?.text ?? '让声音慢慢抵达'}</strong>
            {activeLyric?.translation && <span>{activeLyric.translation}</span>}
            {nextLyric && <span className="fullscreen-player__lyric-context">{nextLyric.text}</span>}
          </div>

          <div className="fullscreen-player__progress"><PlayerProgress /></div>
          <div className="fullscreen-player__controls">
            <PlayModeButton />
            <PlayerControls />
            <VolumeControl />
          </div>
        </section>
      </main>
    </div>
  )
}

export default FullscreenPlayer
