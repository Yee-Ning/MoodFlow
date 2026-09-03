import { ArrowDown, ArrowUp, Music2, Play, Trash2 } from 'lucide-react'
import { forwardRef, memo, useState } from 'react'
import type { SongSummary } from '../../types/music'
import NeteaseImage from '../music/NeteaseImage'

type MoveDirection = 'up' | 'down'

interface QueueSongItemProps {
  song: SongSummary
  isCurrent: boolean
  isPlaying: boolean
  isLoading: boolean
  canMoveUp: boolean
  canMoveDown: boolean
  onPlay: (songId: number) => void
  onRemove: (songId: number) => void
  onMove: (songId: number, direction: MoveDirection) => void
}

function formatDuration(duration: number) {
  const totalSeconds = Math.max(0, Math.floor(duration / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, '0')}`
}

const QueueSongItem = memo(forwardRef<HTMLLIElement, QueueSongItemProps>(
  function QueueSongItem({
    song,
    isCurrent,
    isPlaying,
    isLoading,
    canMoveUp,
    canMoveDown,
    onPlay,
    onRemove,
    onMove,
  }, ref) {
    const [isRemoving, setIsRemoving] = useState(false)

    const requestRemove = () => {
      if (isLoading || isRemoving) return
      setIsRemoving(true)
      window.setTimeout(() => onRemove(song.id), 190)
    }

    return (
      <li
        ref={ref}
        className={`play-queue-item${isCurrent ? ' is-current' : ''}${isRemoving ? ' is-removing' : ''}`}
        data-queue-current={isCurrent ? 'true' : undefined}
      >
        <button
          className="play-queue-item__song"
          type="button"
          aria-current={isCurrent ? 'true' : undefined}
          aria-label={`${isCurrent ? '当前播放' : '播放'}：${song.name}，${song.artists.join('、') || '未知歌手'}`}
          disabled={isLoading && isCurrent}
          onClick={() => onPlay(song.id)}
        >
          <span className="play-queue-item__cover" aria-hidden="true">
            <NeteaseImage
              source={song.coverUrl}
              size={112}
              alt=""
              loading="lazy"
              fallback={<Music2 size={18} strokeWidth={1.35} />}
            />
            {isCurrent && (
              <span className="play-queue-item__indicator">
                {isPlaying ? (
                  <span className="play-queue-item__bars" aria-label="正在播放">
                    <i /><i /><i />
                  </span>
                ) : (
                  <Play size={13} fill="currentColor" aria-label="当前歌曲" />
                )}
              </span>
            )}
          </span>

          <span className="play-queue-item__copy">
            <strong>{song.name}</strong>
            <small>{song.artists.join('、') || '未知歌手'}</small>
          </span>

          <time className="play-queue-item__duration" dateTime={`PT${Math.round(song.duration / 1000)}S`}>
            {formatDuration(song.duration)}
          </time>
        </button>

        <div className="play-queue-item__actions" aria-label={`${song.name} 队列操作`}>
          <button
            type="button"
            aria-label={`上移 ${song.name}`}
            title="上移"
            disabled={!canMoveUp || isRemoving}
            onClick={() => onMove(song.id, 'up')}
          >
            <ArrowUp size={14} strokeWidth={1.7} aria-hidden="true" />
          </button>
          <button
            type="button"
            aria-label={`下移 ${song.name}`}
            title="下移"
            disabled={!canMoveDown || isRemoving}
            onClick={() => onMove(song.id, 'down')}
          >
            <ArrowDown size={14} strokeWidth={1.7} aria-hidden="true" />
          </button>
          <button
            className="play-queue-item__remove"
            type="button"
            aria-label={`从队列删除 ${song.name}`}
            title="从队列删除"
            disabled={isLoading || isRemoving}
            onClick={requestRemove}
          >
            <Trash2 size={14} strokeWidth={1.65} aria-hidden="true" />
          </button>
        </div>
      </li>
    )
  },
))

export default QueueSongItem
export type { MoveDirection }
