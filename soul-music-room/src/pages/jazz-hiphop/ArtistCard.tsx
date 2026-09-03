import { useState } from 'react'
import type { JazzArtist } from '../../data/jazzHipHopData'

interface ArtistCardProps {
  artist: JazzArtist
}

function ArtistCard({ artist }: ArtistCardProps) {
  const [imageFailed, setImageFailed] = useState(false)
  const emphasis = artist.emphasis ?? 'standard'

  return (
    <article className={`jazz-artist-card jazz-artist-card--${emphasis}`}>
      <div
        className={`jazz-artist-card__image-wrap${imageFailed ? ' is-missing' : ''}`}
      >
        {!imageFailed && (
          <img
            src={artist.image}
            alt={`${artist.name} portrait`}
            className="jazz-artist-card__image"
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        )}
      </div>

      <div className="jazz-artist-card__shade" aria-hidden="true" />
      <div className="jazz-artist-card__number" aria-hidden="true">
        {String(artist.id).padStart(2, '0')}
      </div>

      <div className="jazz-artist-card__content">
        <p className="jazz-artist-card__location">{artist.location}</p>
        <h3>{artist.name}</h3>
        <p className="jazz-artist-card__role">{artist.role}</p>
        <p className="jazz-artist-card__description">{artist.description}</p>
      </div>
    </article>
  )
}

export default ArtistCard
