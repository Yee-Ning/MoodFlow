import type { CSSProperties } from 'react'
import { useLocation } from 'react-router-dom'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import { usePlayerStore } from '../../store/playerStore'
import { getNeteaseImageUrl } from '../../utils/neteaseImage'
import { useFullscreen } from '../../composables/useFullscreen'
import { useLyrics } from '../../composables/useLyrics'
import AddToPlaylistModal from './AddToPlaylistModal'
import FullscreenPlayer from './FullscreenPlayer'
import LyricsPanel from './LyricsPanel'
import PlayQueueDrawer from './PlayQueueDrawer'
import PlaybackControls from './PlaybackControls'
import {
  PlayerFunctionGroup,
  QueueActionButton,
  SecondaryActionGroup,
} from './PlayerActionButtons'
import PlayerProgress from './PlayerProgress'
import SongInfo from './SongInfo'
import VolumeControl from './VolumeControl'
import './player.css'
import './liquid-glass-player.css'

function LiquidGlassPlayer() {
  useLyrics()

  const location = useLocation()
  const currentSong = usePlayerStore((state) => state.currentSong)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const notice = usePlayerStore((state) => state.notice)
  const { isFullscreen, enterFullscreen, exitFullscreen } = useFullscreen()
  const artworkUrl = getNeteaseImageUrl(currentSong?.coverUrl ?? '', 640)
  const playerStyle = artworkUrl
    ? { '--liquid-player-artwork': `url("${artworkUrl}")` } as CSSProperties
    : undefined

  const toggleFullscreen = () => {
    if (isFullscreen) void exitFullscreen()
    else void enterFullscreen()
  }

  return (
    <>
      <footer className="liquid-player-shell" aria-label="全局音乐播放器">
        {notice && <p className="liquid-player__notice" role="status">{notice}</p>}

        <LiquidGlassSurface
          className={`liquid-player${isPlaying ? ' is-playing' : ''}`}
          preset="dock"
          radius={50}
          intensity={1}
          interactive
          refract
          style={playerStyle}
        >
          <span className="liquid-player__artwork-wash" aria-hidden="true" />

          <div className="liquid-player__progress-zone">
            <PlayerProgress />
          </div>

          <div className="liquid-player__left-rail">
            <div className="liquid-player__song-zone">
              <SongInfo onMobileExpand={() => void enterFullscreen()} />
            </div>
            <SecondaryActionGroup />
          </div>

          <div className="liquid-player__center-rail">
            <div className="liquid-player__playback-zone">
              <PlaybackControls />
            </div>
            <QueueActionButton />
          </div>

          <div className="liquid-player__right-rail">
            <PlayerFunctionGroup
              isFullscreen={isFullscreen}
              onToggleFullscreen={toggleFullscreen}
            />
            <div className="liquid-player__volume-zone">
              <VolumeControl />
            </div>
          </div>
        </LiquidGlassSurface>
      </footer>

      <PlayQueueDrawer />
      {!location.pathname.startsWith('/playlist/') && <LyricsPanel presentation="overlay" />}
      <AddToPlaylistModal />
      {isFullscreen && <FullscreenPlayer onExit={exitFullscreen} />}
    </>
  )
}

export default LiquidGlassPlayer
