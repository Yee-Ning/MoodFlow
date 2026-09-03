import { ArrowUpRight, Music2 } from 'lucide-react'
import type { Artist } from '../../data/homeData'

interface ArtistCardProps {
  artist: Artist
  index: number
}

function ArtistCard({ artist, index }: ArtistCardProps) {
  return (
    <article className="artist-card">
      <div className="artist-card__top">
        <span>{String(index + 1).padStart(2, '0')}</span>
        <Music2 size={17} strokeWidth={1.4} aria-hidden="true" />
      </div>
      <div>
        <p>{artist.origin}</p>
        <h3>{artist.name}</h3>
        <span>{artist.note}</span>
      </div>
      <ArrowUpRight className="artist-card__arrow" size={18} strokeWidth={1.4} aria-hidden="true" />
    </article>
  )
}

export default ArtistCard
