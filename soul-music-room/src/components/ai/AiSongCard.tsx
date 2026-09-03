import { Music2, Play, Plus } from 'lucide-react'
import NeteaseImage from '../music/NeteaseImage'
import { playSong } from '../../services/player'
import type { AiSongRecommendation } from '../../types/aiChat'

interface AiSongCardProps {
  recommendation: AiSongRecommendation
  onAdd?: (song: AiSongRecommendation) => void
}

function AiSongCard({ recommendation, onAdd }: AiSongCardProps) {
  return (
    <article className="ai-song-card">
      <span className="ai-song-card__cover">
        <NeteaseImage
          source={recommendation.coverUrl}
          size={160}
          alt={`${recommendation.name} 封面`}
          fallback={<Music2 size={20} strokeWidth={1.3} aria-hidden="true" />}
        />
      </span>
      <span className="ai-song-card__body">
        <strong>{recommendation.name}</strong>
        <small>{recommendation.artists.join(' · ')}</small>
        <p>{recommendation.reason}</p>
      </span>
      <span className="ai-song-card__actions">
        <button type="button" aria-label={`播放 ${recommendation.name}`} onClick={() => void playSong(recommendation)}>
          <Play size={14} fill="currentColor" strokeWidth={1.5} aria-hidden="true" />
        </button>
        <button
          type="button"
          aria-label={`添加 ${recommendation.name} 到歌单`}
          disabled={!onAdd}
          title={onAdd ? '添加到歌单' : '添加功能将在歌曲数据接入后启用'}
          onClick={() => onAdd?.(recommendation)}
        >
          <Plus size={15} strokeWidth={1.6} aria-hidden="true" />
        </button>
      </span>
    </article>
  )
}

export default AiSongCard

