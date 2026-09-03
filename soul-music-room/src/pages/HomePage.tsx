import AlbumCard from '../components/music/AlbumCard'
import ArtistCard from '../components/music/ArtistCard'
import MusicSection from '../components/music/MusicSection'
import PlaylistCard from '../components/music/PlaylistCard'
import TrackCard from '../components/music/TrackCard'
import {
  continueListening,
  featuredPlaylists,
  madeForYou,
  newReleases,
  popularArtists,
} from '../data/homeData'
import HeroSection from './home/HeroSection'
import './HomePage.css'

function HomePage() {
  return (
    <div className="mood-home">
      <HeroSection />

      <div className="mood-home__catalog">
        <MusicSection
          eyebrow="PICK UP THE THREAD"
          title="Continue Listening"
          description="The tide remembers where you left off."
          viewAllPath="/history"
        >
          <div className="track-row">
            {continueListening.map((track) => <TrackCard key={track.id} track={track} />)}
          </div>
        </MusicSection>

        <MusicSection
          eyebrow="CURATED AROUND YOU"
          title="Made For You"
          description="Small rooms for every pace of the day."
        >
          <div className="playlist-grid playlist-grid--four">
            {madeForYou.map((playlist) => <PlaylistCard key={playlist.id} playlist={playlist} />)}
          </div>
        </MusicSection>

        <MusicSection
          eyebrow="FROM THE EDITORS"
          title="Featured Playlists"
          description="Listen deeper, one world at a time."
        >
          <div className="playlist-grid playlist-grid--featured">
            {featuredPlaylists.map((playlist) => <PlaylistCard key={playlist.id} playlist={playlist} />)}
          </div>
        </MusicSection>

        <MusicSection
          eyebrow="VOICES IN ORBIT"
          title="Popular Artists"
          description="Artists shaping how the room feels right now."
        >
          <div className="artist-grid">
            {popularArtists.map((artist, index) => (
              <ArtistCard artist={artist} index={index} key={artist.id} />
            ))}
          </div>
        </MusicSection>

        <MusicSection
          eyebrow="FRESH PRESSINGS"
          title="New Releases"
          description="New to this room, ready when you are."
        >
          <div className="album-grid">
            {newReleases.map((album) => <AlbumCard album={album} key={album.id} />)}
          </div>
        </MusicSection>

        <footer className="mood-home__footer">
          <p>ONE OCEAN. ENDLESS SOUND.</p>
          <span>MoodFlow · Music moves differently here.</span>
        </footer>
      </div>
    </div>
  )
}

export default HomePage
