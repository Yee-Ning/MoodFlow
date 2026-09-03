import { Captions, Heart, ListMusic, Maximize2, Minimize2, Plus } from 'lucide-react'
import { memo } from 'react'
import { usePlayerStore } from '../../store/playerStore'
import GlassIconButton from './GlassIconButton'
import PlayModeButton from './PlayModeButton'

export const SecondaryActionGroup = memo(function SecondaryActionGroup() {
  const currentSong = usePlayerStore((state) => state.currentSong)
  const liked = usePlayerStore((state) => (
    currentSong ? state.likedSongIds.has(currentSong.id) : false
  ))
  const isAddToPlaylistOpen = usePlayerStore((state) => state.isAddToPlaylistOpen)
  const toggleLike = usePlayerStore((state) => state.toggleLike)
  const openAddToPlaylist = usePlayerStore((state) => state.openAddToPlaylist)
  const closeAddToPlaylist = usePlayerStore((state) => state.closeAddToPlaylist)

  return (
    <div className="player-secondary-actions" aria-label="歌曲快捷操作">
      <GlassIconButton
        className="player-secondary-actions__favorite"
        icon={<Heart size={17} fill={liked ? 'currentColor' : 'none'} strokeWidth={1.75} aria-hidden="true" />}
        label={liked ? '取消喜欢' : '收藏歌曲'}
        liked={liked}
        active={liked}
        aria-pressed={liked}
        disabled={!currentSong}
        onClick={() => currentSong && void toggleLike(currentSong.id)}
      />

      <GlassIconButton
        className="player-secondary-actions__add"
        icon={<Plus size={17} strokeWidth={1.7} aria-hidden="true" />}
        label={isAddToPlaylistOpen ? '关闭添加到歌单' : '添加到歌单'}
        active={isAddToPlaylistOpen}
        aria-pressed={isAddToPlaylistOpen}
        disabled={!currentSong}
        onClick={isAddToPlaylistOpen ? closeAddToPlaylist : openAddToPlaylist}
      />

      <PlayModeButton />
    </div>
  )
})

export const QueueActionButton = memo(function QueueActionButton() {
  const queueLength = usePlayerStore((state) => state.queue.length)
  const isQueueOpen = usePlayerStore((state) => state.isQueueOpen)
  const toggleQueue = usePlayerStore((state) => state.toggleQueue)

  return (
    <GlassIconButton
      className="player-queue-action"
      icon={<ListMusic size={17} strokeWidth={1.6} aria-hidden="true" />}
      label={`当前队列 · ${queueLength} 首`}
      active={isQueueOpen}
      aria-pressed={isQueueOpen}
      onClick={toggleQueue}
    />
  )
})

interface PlayerFunctionGroupProps {
  isFullscreen: boolean
  onToggleFullscreen: () => void
}

export const PlayerFunctionGroup = memo(function PlayerFunctionGroup({
  isFullscreen,
  onToggleFullscreen,
}: PlayerFunctionGroupProps) {
  const hasCurrentSong = usePlayerStore((state) => Boolean(state.currentSong))
  const isLyricsOpen = usePlayerStore((state) => state.isLyricsOpen)
  const toggleLyrics = usePlayerStore((state) => state.toggleLyrics)

  return (
    <div className="player-function-actions" aria-label="播放器功能">
      <GlassIconButton
        className="player-function-actions__lyrics"
        icon={<Captions size={18} strokeWidth={1.65} aria-hidden="true" />}
        label={isLyricsOpen ? '关闭歌词' : '打开歌词'}
        active={isLyricsOpen}
        aria-pressed={isLyricsOpen}
        disabled={!hasCurrentSong}
        onClick={toggleLyrics}
      />

      <GlassIconButton
        className="player-function-actions__fullscreen"
        icon={isFullscreen ? (
          <Minimize2 size={17} strokeWidth={1.7} aria-hidden="true" />
        ) : (
          <Maximize2 size={17} strokeWidth={1.7} aria-hidden="true" />
        )}
        label={isFullscreen ? '退出全屏' : '进入全屏'}
        active={isFullscreen}
        aria-pressed={isFullscreen}
        disabled={!hasCurrentSong}
        onClick={onToggleFullscreen}
      />
    </div>
  )
})
