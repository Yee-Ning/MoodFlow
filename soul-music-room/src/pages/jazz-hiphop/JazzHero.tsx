import { ArrowDown } from 'lucide-react'

interface JazzHeroProps {
  onExplore: () => void
}

function JazzHero({ onExplore }: JazzHeroProps) {
  return (
    <section className="jazz-hero" aria-labelledby="jazz-hero-title">
      <div className="jazz-hero__edition" aria-hidden="true">
        <span>SOUL MUSIC ARCHIVE</span>
        <span>ISSUE 01 · AFTER MIDNIGHT</span>
      </div>

      <div className="jazz-hero__copy">
        <p className="jazz-kicker">A STUDY IN RHYTHM &amp; DUST</p>
        <h1 id="jazz-hero-title">
          <span>JAZZ</span>
          <span>HIPHOP</span>
        </h1>
        <p className="jazz-hero__subtitle">Dusty Beats, Mellow Keys</p>
        <p className="jazz-hero__intro">
          爵士的即兴、HipHop 的鼓点，以及城市深夜里缓慢流动的情绪。
        </p>
      </div>

      <button className="jazz-explore" type="button" onClick={onExplore}>
        <span>EXPLORE THE CULTURE</span>
        <ArrowDown size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>

      <p className="jazz-hero__side-note" aria-hidden="true">
        PLAY IT LOW
        <br />
        LET IT BREATHE
      </p>
    </section>
  )
}

export default JazzHero
