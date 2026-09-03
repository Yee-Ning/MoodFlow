import { LoaderCircle, Pause, Play, SkipBack, SkipForward } from 'lucide-react'
import { usePlayerStore } from '../../store/playerStore'
import GlassButton from './GlassButton'

function PlayerControls() {
  const currentSong = usePlayerStore((state) => state.currentSong)
  const queueLength = usePlayerStore((state) => state.queue.length)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const isLoading = usePlayerStore((state) => state.isLoading)
  const togglePlayback = usePlayerStore((state) => state.togglePlayback)
  const playPrevious = usePlayerStore((state) => state.playPrevious)
  const playNext = usePlayerStore((state) => state.playNext)
  const controlsDisabled = queueLength === 0 || isLoading

  return (
    <div className="player-controls" aria-label="核心播放控制">
      <GlassButton
        tooltip="上一首"
        aria-label="上一首"
        disabled={controlsDisabled}
        onClick={() => void playPrevious()}
      >
        <SkipBack size={18} fill="currentColor" aria-hidden="true" />
      </GlassButton>

      <GlassButton
        className="player-controls__play"
        tooltip={isPlaying ? '暂停' : '播放'}
        variant="primary"
        aria-label={isPlaying ? '暂停' : '播放'}
        disabled={!currentSong || isLoading}
        onClick={() => void togglePlayback()}
      >
        {isLoading ? (
          <LoaderCircle className="player-controls__loader" size={20} aria-hidden="true" />
        ) : isPlaying ? (
          <Pause size={20} fill="currentColor" aria-hidden="true" />
        ) : (
          <Play className="player-controls__play-icon" size={20} fill="currentColor" aria-hidden="true" />
        )}
      </GlassButton>

      <GlassButton
        tooltip="下一首"
        aria-label="下一首"
        disabled={controlsDisabled}
        onClick={() => void playNext()}
      >
        <SkipForward size={18} fill="currentColor" aria-hidden="true" />
      </GlassButton>
    </div>
  )
}

export default PlayerControls
