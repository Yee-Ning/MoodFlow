export interface Track {
  id: string
  title: string
  artist: string
  album: string
  cover: string
  progress: number
}

export interface Playlist {
  id: string
  title: string
  subtitle: string
  cover: string
  tag: string
  path?: string
}

export interface Artist {
  id: string
  name: string
  note: string
  origin: string
}

export interface Album {
  id: string
  title: string
  artist: string
  year: string
  cover: string
  type: string
}

const jazzRoot = '/images/jazz-hiphop'

export const continueListening: Track[] = [
  {
    id: 'feather',
    title: 'Feather',
    artist: 'Nujabes',
    album: 'Modal Soul',
    cover: `${jazzRoot}/album-modal-soul.webp`,
    progress: 58,
  },
  {
    id: 'flowers',
    title: 'Flowers',
    artist: 'J Dilla',
    album: 'Donuts',
    cover: `${jazzRoot}/album-donuts.webp`,
    progress: 31,
  },
  {
    id: 'electric-relaxation',
    title: 'Electric Relaxation',
    artist: 'A Tribe Called Quest',
    album: 'The Low End Theory',
    cover: `${jazzRoot}/album-low-end-theory.webp`,
    progress: 73,
  },
]

export const madeForYou: Playlist[] = [
  {
    id: 'midnight-coding',
    title: 'Midnight Coding',
    subtitle: 'Low light, steady focus, no sharp edges.',
    cover: `${jazzRoot}/album-metaphorical-music.webp`,
    tag: '24 TRACKS',
  },
  {
    id: 'soft-morning',
    title: 'Soft Morning',
    subtitle: 'Warm keys for the first quiet hour.',
    cover: `${jazzRoot}/album-shoulian-shui.webp`,
    tag: '18 TRACKS',
  },
  {
    id: 'rainy-window',
    title: 'Rainy Window',
    subtitle: 'Soft percussion under a silver sky.',
    cover: `${jazzRoot}/album-gdna.webp`,
    tag: '21 TRACKS',
  },
  {
    id: 'late-night-drive',
    title: 'Late Night Drive',
    subtitle: 'City lights drifting past the glass.',
    cover: `${jazzRoot}/album-low-end-theory.webp`,
    tag: '32 TRACKS',
  },
]

export const featuredPlaylists: Playlist[] = [
  {
    id: 'jazz-hiphop-essentials',
    title: 'Jazz HipHop Essentials',
    subtitle: 'Dusty drums, mellow keys and after-midnight soul.',
    cover: `${jazzRoot}/album-modal-soul.webp`,
    tag: 'EDITORIAL ARCHIVE',
    path: '/genre/jazz-hiphop',
  },
  {
    id: 'future-soul',
    title: 'Future Soul',
    subtitle: 'A softer edge of rhythm and electronics.',
    cover: `${jazzRoot}/album-donuts.webp`,
    tag: 'MOODFLOW SELECTS',
  },
  {
    id: 'blue-hour',
    title: 'Blue Hour',
    subtitle: 'Songs for the moment daylight gives in.',
    cover: `${jazzRoot}/album-metaphorical-music.webp`,
    tag: 'MOODFLOW SELECTS',
  },
]

export const popularArtists: Artist[] = [
  { id: 'nujabes', name: 'Nujabes', note: 'Jazz HipHop · Producer', origin: 'TOKYO' },
  { id: 'soft-lipa', name: '蛋堡 Soft Lipa', note: 'HipHop · Songwriter', origin: 'TAIPEI' },
  { id: 'kendrick', name: 'Kendrick Lamar', note: 'HipHop · Storyteller', origin: 'COMPTON' },
  { id: 'frank-ocean', name: 'Frank Ocean', note: 'Alternative R&B', origin: 'NEW ORLEANS' },
  { id: 'sza', name: 'SZA', note: 'R&B · Songwriter', origin: 'ST. LOUIS' },
  { id: 'tyler', name: 'Tyler, The Creator', note: 'HipHop · Producer', origin: 'LOS ANGELES' },
]

export const newReleases: Album[] = [
  {
    id: 'blue-current',
    title: 'Blue Current',
    artist: 'Aya Mori',
    year: '2026',
    cover: '/images/ocean-bg-generated-hd.webp',
    type: 'NEW SINGLE',
  },
  {
    id: 'pearl-dawn',
    title: 'Pearl Dawn',
    artist: 'Lumen',
    year: '2026',
    cover: '/images/ocean-bg-generated.png',
    type: 'NEW ALBUM',
  },
  {
    id: 'slow-signal',
    title: 'Slow Signal',
    artist: 'North of Midnight',
    year: '2026',
    cover: '/images/ocean-bg.jpg',
    type: 'NEW SINGLE',
  },
  {
    id: 'glass-tides',
    title: 'Glass Tides',
    artist: 'Kairo Blue',
    year: '2026',
    cover: '/images/ocean-bg-generated-hd.webp',
    type: 'NEW EP',
  },
]
