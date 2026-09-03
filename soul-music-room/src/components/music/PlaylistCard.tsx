import { ArrowUpRight, Play } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { Playlist } from '../../data/homeData'

interface PlaylistCardProps {
  playlist: Playlist
}

function PlaylistCard({ playlist }: PlaylistCardProps) {
  const navigate = useNavigate()
  const isLinked = Boolean(playlist.path)

  const openPlaylist = () => {
    if (playlist.path) navigate(playlist.path)
  }

  return (
    <article className="playlist-card" data-linked={isLinked}>
      <button
        className="playlist-card__cover"
        type="button"
        onClick={openPlaylist}
        disabled={!isLinked}
        aria-label={isLinked ? `Open ${playlist.title}` : `${playlist.title} playlist preview`}
      >
        <img src={playlist.cover} alt="" />
        <span className="playlist-card__play" aria-hidden="true">
          {isLinked ? <ArrowUpRight size={19} /> : <Play size={18} fill="currentColor" />}
        </span>
      </button>
      <p>{playlist.tag}</p>
      <h3>{playlist.title}</h3>
      <span>{playlist.subtitle}</span>
    </article>
  )
}

export default PlaylistCard
