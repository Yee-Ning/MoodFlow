export type ArtistEmphasis = 'primary' | 'secondary'

export interface JazzArtist {
  id: number
  name: string
  location: string
  role: string
  description: string
  image: string
  emphasis?: ArtistEmphasis
}

export interface JazzAlbum {
  id: number
  title: string
  artist: string
  year: number
  description: string
  cover: string
}

const imageRoot = '/images/jazz-hiphop'

export const jazzArtists: JazzArtist[] = [
  {
    id: 1,
    name: 'Nujabes',
    location: 'Tokyo, Japan',
    role: 'Producer · Label Founder',
    description:
      '用温暖的爵士采样、克制的鼓点与留白，把嘻哈写成一封寄往深夜的信。',
    image: `${imageRoot}/artist-nujabes-custom.jpg`,
    emphasis: 'primary',
  },
  {
    id: 2,
    name: '蛋堡 Soft Lipa',
    location: 'Taipei, Taiwan',
    role: 'Rapper · Producer',
    description:
      '松弛而精确的叙事者，让爵士和生活里的小事在华语节拍中自然相遇。',
    image: `${imageRoot}/artist-soft-lipa-custom.jpg`,
    emphasis: 'secondary',
  },
  {
    id: 3,
    name: 'J Dilla',
    location: 'Detroit, USA',
    role: 'Producer · Beat Architect',
    description:
      '故意偏离网格的鼓点与切片方式，重新定义了采样音乐的呼吸。',
    image: `${imageRoot}/artist-j-dilla.webp`,
  },
  {
    id: 4,
    name: 'Uyama Hiroto',
    location: 'Yokohama, Japan',
    role: 'Producer · Saxophonist',
    description:
      '以萨克斯和宽阔的和声延续 Hydeout 世界里安静、流动的灵魂。',
    image: `${imageRoot}/artist-uyama-hiroto.webp`,
  },
  {
    id: 5,
    name: 'A Tribe Called Quest',
    location: 'Queens, USA',
    role: 'HipHop Collective',
    description:
      '把爵士低音、机智押韵与街区日常带进黄金年代的嘻哈语法。',
    image: `${imageRoot}/artist-atcq.webp`,
  },
  {
    id: 6,
    name: 'DJ Krush',
    location: 'Tokyo, Japan',
    role: 'Turntablist · Producer',
    description:
      '在烟雾般的低频、碎片化采样与电影感空间中，建立东方抽象嘻哈。',
    image: `${imageRoot}/artist-dj-krush.webp`,
  },
  {
    id: 7,
    name: '国蛋 GorDoN',
    location: 'Taipei, Taiwan',
    role: 'Rapper · Songwriter',
    description:
      '用从容声线与生活观察，让华语说唱保留爵士般的松弛和余韵。',
    image: `${imageRoot}/artist-gordon.webp`,
  },
]

export const essentialAlbums: JazzAlbum[] = [
  {
    id: 1,
    title: 'Modal Soul',
    artist: 'Nujabes',
    year: 2005,
    description: '旋律、诗意与鼓点交叠，一张让城市噪声慢慢退潮的唱片。',
    cover: `${imageRoot}/album-modal-soul.webp`,
  },
  {
    id: 2,
    title: 'Metaphorical Music',
    artist: 'Nujabes',
    year: 2003,
    description: '爵士采样如光线穿过旧窗，奠定 Nujabes 温暖而清澈的语言。',
    cover: `${imageRoot}/album-metaphorical-music.webp`,
  },
  {
    id: 3,
    title: 'Donuts',
    artist: 'J Dilla',
    year: 2006,
    description: '短促切片、偏移节拍与惊人的情绪密度，采样艺术的里程碑。',
    cover: `${imageRoot}/album-donuts.webp`,
  },
  {
    id: 4,
    title: 'The Low End Theory',
    artist: 'A Tribe Called Quest',
    year: 1991,
    description: '低音、爵士与群体化学反应，把街区智慧写进经典节奏。',
    cover: `${imageRoot}/album-low-end-theory.webp`,
  },
  {
    id: 5,
    title: '收敛水',
    artist: '蛋堡 Soft Lipa',
    year: 2009,
    description: '日常对白般的押韵贴着爵士律动，松弛却始终细腻。',
    cover: `${imageRoot}/album-shoulian-shui.webp`,
  },
  {
    id: 6,
    title: 'GDNA',
    artist: '国蛋 GorDoN',
    year: 2023,
    description: '成熟、从容的城市叙事，在低饱和节拍里留下长尾余温。',
    cover: `${imageRoot}/album-gdna.webp`,
  },
]

export const soundKeywords = [
  'SAMPLING',
  'IMPROVISATION',
  'DUSTY DRUMS',
  'WARM KEYS',
  'MIDNIGHT SOUL',
] as const
