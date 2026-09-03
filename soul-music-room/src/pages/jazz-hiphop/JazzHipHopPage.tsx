import { ArrowLeft } from 'lucide-react'
import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import EssentialAlbums from './EssentialAlbums'
import FeaturedArtists from './FeaturedArtists'
import GenreIntroduction from './GenreIntroduction'
import JazzFooter from './JazzFooter'
import JazzHero from './JazzHero'
import './JazzHipHopPage.css'

function JazzHipHopPage() {
  const navigate = useNavigate()

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
  }, [])

  const returnHome = () => navigate('/home')

  const exploreCulture = () => {
    document.getElementById('about-the-sound')?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth',
      block: 'start',
    })
  }

  return (
    <main className="jazz-page">
      <div className="jazz-page__background" aria-hidden="true" />
      <div className="jazz-page__overlay" aria-hidden="true" />

      <nav className="jazz-nav" aria-label="Jazz HipHop page navigation">
        <button type="button" onClick={returnHome}>
          <ArrowLeft size={18} strokeWidth={1.5} aria-hidden="true" />
          <span>Back to Soul Music Room</span>
        </button>
        <p>01 / Jazz HipHop</p>
      </nav>

      <div className="jazz-page__content">
        <JazzHero onExplore={exploreCulture} />
        <GenreIntroduction />
        <FeaturedArtists />
        <EssentialAlbums />
        <JazzFooter onReturnHome={returnHome} />
      </div>
    </main>
  )
}

export default JazzHipHopPage
