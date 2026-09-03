import { LoaderCircle, Pause, Play, SkipBack, SkipForward } from 'lucide-react'
import { memo } from 'react'
import { usePlayerStore } from '../../store/playerStore'
import GlassIconButton from './GlassIconButton'

function PlaybackControls() {
  const hasCurrentSong = usePlayerStore((state) => Boolean(state.currentSong))
  const queueLength = usePlayerStore((state) => state.queue.length)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const isLoading = usePlayerStore((state) => state.isLoading)
  const togglePlayback = usePlayerStore((state) => state.togglePlayback)
  const playPrevious = usePlayerStore((state) => state.playPrevious)
  const playNext = usePlayerStore((state) => state.playNext)
  const navigationDisabled = queueLength === 0 || isLoading

  return (
    <div className="playback-controls" aria-label="播放控制">
      <GlassIconButton
        className="playback-controls__previous"
        icon={<SkipBack size={18} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />}
        label="上一首"
        aria-label="上一首"
        disabled={navigationDisabled}
        onClick={() => void playPrevious()}
      />

      <GlassIconButton
        className="playback-controls__play"
        icon={isLoading ? (
          <LoaderCircle className="playback-controls__loader" size={20} aria-hidden="true" />
        ) : isPlaying ? (
          <Pause size={19} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />
        ) : (
          <Play className="playback-controls__play-icon" size={19} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />
        )}
        label={isPlaying ? '暂停' : '播放'}
        primary
        active={isPlaying}
        aria-label={isPlaying ? '暂停' : '播放'}
        aria-busy={isLoading}
        disabled={!hasCurrentSong || isLoading}
        onClick={() => void togglePlayback()}
      />

      <GlassIconButton
        className="playback-controls__next"
        icon={<SkipForward size={18} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />}
        label="下一首"
        aria-label="下一首"
        disabled={navigationDisabled}
        onClick={() => void playNext()}
      />
    </div>
  )
}

export default memo(PlaybackControls)
