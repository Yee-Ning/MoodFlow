import { Play } from 'lucide-react'
import type { Album } from '../../data/homeData'

interface AlbumCardProps {
  album: Album
}

function AlbumCard({ album }: AlbumCardProps) {
  return (
    <article className="album-card">
      <div className="album-card__cover">
        <img src={album.cover} alt={`${album.title} cover`} />
        <button type="button" aria-label={`Play ${album.title} placeholder`}>
          <Play size={17} fill="currentColor" aria-hidden="true" />
        </button>
      </div>
      <p>{album.type} · {album.year}</p>
      <h3>{album.title}</h3>
      <span>{album.artist}</span>
    </article>
  )
}

export default AlbumCard
