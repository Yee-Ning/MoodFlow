import { Play } from 'lucide-react'
import { useState } from 'react'
import {
  essentialAlbums,
  type JazzAlbum,
} from '../../data/jazzHipHopData'

function AlbumCard({ album }: { album: JazzAlbum }) {
  const [imageFailed, setImageFailed] = useState(false)

  return (
    <article className="jazz-album-card">
      <div className={`jazz-album-card__cover${imageFailed ? ' is-missing' : ''}`}>
        {!imageFailed && (
          <img
            src={album.cover}
            alt={`${album.artist} — ${album.title} cover`}
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        )}
        <span className="jazz-album-card__play" aria-label="Static play icon">
          <Play size={19} strokeWidth={1.7} fill="currentColor" aria-hidden="true" />
        </span>
      </div>

      <div className="jazz-album-card__meta">
        <p>
          {album.artist} <span>/ {album.year}</span>
        </p>
        <h3>{album.title}</h3>
        <p className="jazz-album-card__description">{album.description}</p>
      </div>
    </article>
  )
}

function EssentialAlbums() {
  return (
    <section
      className="jazz-section jazz-albums"
      aria-labelledby="essential-listening-title"
    >
      <div className="jazz-section__heading jazz-section__heading--split">
        <div>
          <p>04 / RECORDS FOR THE LONG WAY HOME</p>
          <h2 id="essential-listening-title">ESSENTIAL LISTENING</h2>
        </div>
        <p className="jazz-section__aside">SELECTED · NOT RANKED</p>
      </div>

      <div className="jazz-albums__grid">
        {essentialAlbums.map((album) => (
          <AlbumCard album={album} key={album.id} />
        ))}
      </div>
    </section>
  )
}

export default EssentialAlbums
