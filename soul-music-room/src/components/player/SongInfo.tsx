import { Disc3 } from 'lucide-react'
import { memo } from 'react'
import NeteaseImage from '../music/NeteaseImage'
import { usePlayerStore } from '../../store/playerStore'

interface SongInfoProps {
  onMobileExpand: () => void
}

function SongInfo({ onMobileExpand }: SongInfoProps) {
  const currentSong = usePlayerStore((state) => state.currentSong)
  const isPlaying = usePlayerStore((state) => state.isPlaying)

  const openSong = () => {
    if (!currentSong) return

    if (window.matchMedia('(max-width: 760px)').matches) {
      onMobileExpand()
      return
    }

    window.dispatchEvent(new CustomEvent('moodflow:open-song-details', {
      detail: { songId: currentSong.id },
    }))
  }

  return (
    <div className="song-info">
      <button
        className="song-info__details"
        type="button"
        disabled={!currentSong}
        onClick={openSong}
        aria-label={currentSong
          ? `打开 ${currentSong.name} 的歌曲详情`
          : '当前没有播放歌曲'}
      >
        <span
          className={`song-info__cover${isPlaying ? ' is-playing' : ''}`}
          key={`cover-${currentSong?.id ?? 'empty'}`}
        >
          <NeteaseImage
            source={currentSong?.coverUrl ?? ''}
            size={176}
            alt={currentSong ? `${currentSong.name} 封面` : ''}
            fallback={<Disc3 size={24} strokeWidth={1.2} aria-hidden="true" />}
          />
        </span>

        <span className="song-info__copy" key={`copy-${currentSong?.id ?? 'empty'}`}>
          <strong>{currentSong?.name ?? '选择一首歌曲'}</strong>
          <small>{currentSong?.artists.join('、') || '让音乐停靠在这里'}</small>
        </span>
      </button>
    </div>
  )
}

export default memo(SongInfo)
