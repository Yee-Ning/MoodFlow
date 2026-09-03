import { jazzArtists } from '../../data/jazzHipHopData'
import ArtistCard from './ArtistCard'

function FeaturedArtists() {
  return (
    <section
      className="jazz-section jazz-artists"
      aria-labelledby="featured-artists-title"
    >
      <div className="jazz-section__heading jazz-section__heading--split">
        <div>
          <p>03 / PEOPLE WHO SHAPED THE ROOM</p>
          <h2 id="featured-artists-title">VOICES &amp; VISIONARIES</h2>
        </div>
        <p className="jazz-section__aside">
          Seven voices.
          <br />
          One unhurried pulse.
        </p>
      </div>

      <div className="jazz-artists__grid">
        {jazzArtists.map((artist) => (
          <ArtistCard artist={artist} key={artist.id} />
        ))}
      </div>
    </section>
  )
}

export default FeaturedArtists
