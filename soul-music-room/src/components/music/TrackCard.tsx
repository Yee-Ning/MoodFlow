import { Play } from 'lucide-react'
import type { Track } from '../../data/homeData'

interface TrackCardProps {
  track: Track
}

function TrackCard({ track }: TrackCardProps) {
  return (
    <article className="track-card">
      <div className="track-card__cover">
        <img src={track.cover} alt={`${track.album} album cover`} />
        <button type="button" aria-label={`Play ${track.title} placeholder`}>
          <Play size={16} fill="currentColor" aria-hidden="true" />
        </button>
      </div>
      <div className="track-card__copy">
        <span>CONTINUE FROM {track.album.toUpperCase()}</span>
        <h3>{track.title}</h3>
        <p>{track.artist}</p>
        <div className="track-card__progress" aria-label={`${track.progress}% listened`}>
          <i style={{ width: `${track.progress}%` }} />
        </div>
      </div>
    </article>
  )
}

export default TrackCard
