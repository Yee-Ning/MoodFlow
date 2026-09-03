import { useEffect, useRef } from 'react'
import type { CSSProperties } from 'react'
import { getNeteaseImageUrl } from '../../utils/neteaseImage'
import './ambient.css'

interface AmbientCoverVisualProps {
  artworkUrl: string
  isPlaying: boolean
}

interface RgbColor {
  r: number
  g: number
  b: number
}

interface SampledArtwork {
  colors: Uint8ClampedArray
  width: number
  height: number
  palette: RgbColor[]
}

interface AmbientParticle {
  anchorX: number
  anchorY: number
  color: RgbColor
  depth: number
  opacity: number
  phase: number
  radius: number
  speed: number
  sway: number
  streak: boolean
}

const FALLBACK_PALETTE: RgbColor[] = [
  { r: 118, g: 182, b: 191 },
  { r: 190, g: 150, b: 105 },
  { r: 65, g: 104, b: 137 },
  { r: 221, g: 231, b: 226 },
]

const clamp = (value: number, minimum: number, maximum: number) => (
  Math.min(maximum, Math.max(minimum, value))
)

const colorDistance = (left: RgbColor, right: RgbColor) => (
  Math.hypot(left.r - right.r, left.g - right.g, left.b - right.b)
)

function rgb(color: RgbColor, alpha?: number) {
  return alpha === undefined
    ? `rgb(${color.r} ${color.g} ${color.b})`
    : `rgb(${color.r} ${color.g} ${color.b} / ${alpha})`
}

function sampleArtwork(image: HTMLImageElement): SampledArtwork | null {
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) return null

  canvas.width = 44
  canvas.height = 44
  context.drawImage(image, 0, 0, canvas.width, canvas.height)

  try {
    const colors = context.getImageData(0, 0, canvas.width, canvas.height).data
    const candidates: Array<RgbColor & { score: number }> = []

    for (let index = 0; index < colors.length; index += 16) {
      if (colors[index + 3] < 180) continue
      const r = colors[index]
      const g = colors[index + 1]
      const b = colors[index + 2]
      const maximum = Math.max(r, g, b)
      const minimum = Math.min(r, g, b)
      const lightness = (maximum + minimum) / 510
      if (lightness < 0.045 || lightness > 0.96) continue
      const saturation = maximum === 0 ? 0 : (maximum - minimum) / maximum
      const midtone = 1 - Math.abs(lightness - 0.54)
      candidates.push({ r, g, b, score: saturation * 0.74 + midtone * 0.26 })
    }

    candidates.sort((left, right) => right.score - left.score)
    const palette: RgbColor[] = []
    for (const candidate of candidates) {
      if (palette.every((color) => colorDistance(color, candidate) > 72)) {
        palette.push({ r: candidate.r, g: candidate.g, b: candidate.b })
      }
      if (palette.length === 4) break
    }

    return {
      colors,
      width: canvas.width,
      height: canvas.height,
      palette: [...palette, ...FALLBACK_PALETTE].slice(0, 4),
    }
  } catch {
    return null
  }
}

function sampledColor(artwork: SampledArtwork | null, u: number, v: number, seed: number) {
  if (!artwork) return FALLBACK_PALETTE[seed % FALLBACK_PALETTE.length]

  const x = clamp(Math.floor(u * artwork.width), 0, artwork.width - 1)
  const y = clamp(Math.floor(v * artwork.height), 0, artwork.height - 1)
  const index = (y * artwork.width + x) * 4
  const sampled = {
    r: artwork.colors[index],
    g: artwork.colors[index + 1],
    b: artwork.colors[index + 2],
  }
  const brightness = (sampled.r + sampled.g + sampled.b) / 3

  if (brightness < 24 || artwork.colors[index + 3] < 120) {
    return artwork.palette[seed % artwork.palette.length]
  }

  return sampled
}

function buildParticles(width: number, height: number, artwork: SampledArtwork | null) {
  const particleCount = Math.round(clamp(width * height / 6300, 190, 390))
  const particles: AmbientParticle[] = []

  for (let index = 0; index < particleCount; index += 1) {
    const coverParticle = index < particleCount * 0.76
    const u = Math.random()
    const v = Math.random()
    const angle = -0.12
    const localX = (u - 0.5) * (coverParticle ? 1.08 : 1.34)
    const localY = (v - 0.5) * (coverParticle ? 0.82 : 1.18)
    const rotatedX = localX * Math.cos(angle) - localY * Math.sin(angle)
    const rotatedY = localX * Math.sin(angle) + localY * Math.cos(angle)
    const depth = 0.32 + Math.random() * 0.68
    const color = sampledColor(artwork, u, v, index)
    const brightness = (color.r + color.g + color.b) / 765

    particles.push({
      anchorX: 0.51 + rotatedX,
      anchorY: 0.49 + rotatedY,
      color,
      depth,
      opacity: coverParticle
        ? 0.10 + brightness * 0.22 + Math.random() * 0.08
        : 0.045 + Math.random() * 0.12,
      phase: Math.random() * Math.PI * 2,
      radius: coverParticle
        ? 0.65 + depth * 1.85 + Math.random() * 1.05
        : 0.45 + depth * 1.2,
      speed: 0.035 + Math.random() * 0.055,
      sway: 10 + Math.random() * 34,
      streak: !coverParticle && index % 9 === 0,
    })
  }

  return particles
}

function AmbientCoverVisual({ artworkUrl, isPlaying }: AmbientCoverVisualProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const isPlayingRef = useRef(isPlaying)
  const restartAnimationRef = useRef<() => void>(() => undefined)
  const resolvedArtwork = getNeteaseImageUrl(artworkUrl, 1200)
  const style = resolvedArtwork
    ? { '--ambient-artwork': `url("${resolvedArtwork}")` } as CSSProperties
    : undefined

  useEffect(() => {
    const root = rootRef.current
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!root || !canvas || !context) return

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    let animationFrame = 0
    let destroyed = false
    let artwork: SampledArtwork | null = null
    let particles: AmbientParticle[] = []

    const applyPalette = (palette: RgbColor[]) => {
      palette.forEach((color, index) => {
        root.style.setProperty(`--ambient-color-${index + 1}`, rgb(color))
      })
    }

    const resize = () => {
      const bounds = canvas.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 1.45)
      const width = Math.max(1, Math.round(bounds.width * dpr))
      const height = Math.max(1, Math.round(bounds.height * dpr))
      if (canvas.width === width && canvas.height === height && particles.length > 0) return
      canvas.width = width
      canvas.height = height
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      particles = buildParticles(bounds.width, bounds.height, artwork)
    }

    const draw = (timestamp: number) => {
      if (destroyed) return
      const bounds = canvas.getBoundingClientRect()
      const width = bounds.width
      const height = bounds.height
      const time = timestamp / 1000

      context.clearRect(0, 0, width, height)
      context.save()
      context.globalCompositeOperation = 'lighter'
      context.shadowBlur = 9

      particles.forEach((particle, index) => {
        const playing = isPlayingRef.current
        const motion = playing && !mediaQuery.matches ? 1 : 0
        const pulse = 0.72 + (Math.sin(time * (0.32 + particle.speed) + particle.phase) + 1) * 0.14
        const orbit = time * particle.speed * motion + particle.phase
        const x = particle.anchorX * width + Math.sin(orbit) * particle.sway * particle.depth * motion
        const y = particle.anchorY * height + Math.cos(orbit * 0.78) * particle.sway * 0.62 * particle.depth * motion
        const alpha = particle.opacity * pulse * (playing ? 1 : 0.72)

        context.fillStyle = rgb(particle.color, alpha)
        context.strokeStyle = rgb(particle.color, alpha * 0.72)
        context.shadowColor = rgb(particle.color, Math.min(0.44, alpha * 1.8))

        if (particle.streak) {
          const length = 9 + particle.depth * 17
          context.lineWidth = Math.max(0.55, particle.radius * 0.46)
          context.beginPath()
          context.moveTo(x - length * 0.42, y + length * 0.08)
          context.lineTo(x + length * 0.58, y - length * 0.12)
          context.stroke()
          return
        }

        const radius = particle.radius * (0.9 + pulse * 0.17)
        context.beginPath()
        context.arc(x, y, radius, 0, Math.PI * 2)
        context.fill()

        if (index % 31 === 0) {
          context.globalAlpha = alpha * 0.5
          context.beginPath()
          context.arc(x, y, radius * 3.8, 0, Math.PI * 2)
          context.fill()
          context.globalAlpha = 1
        }
      })

      context.restore()

      if (isPlayingRef.current && !document.hidden && !mediaQuery.matches) {
        animationFrame = window.requestAnimationFrame(draw)
      }
    }

    const restart = () => {
      window.cancelAnimationFrame(animationFrame)
      resize()
      animationFrame = window.requestAnimationFrame(draw)
    }

    const resizeObserver = new ResizeObserver(restart)
    restartAnimationRef.current = restart
    resizeObserver.observe(canvas)
    mediaQuery.addEventListener('change', restart)
    document.addEventListener('visibilitychange', restart)
    applyPalette(FALLBACK_PALETTE)
    restart()

    if (resolvedArtwork) {
      const image = new Image()
      image.crossOrigin = 'anonymous'
      image.decoding = 'async'
      image.onload = () => {
        if (destroyed) return
        artwork = sampleArtwork(image)
        if (artwork) applyPalette(artwork.palette)
        particles = []
        restart()
      }
      image.src = resolvedArtwork
    }

    return () => {
      destroyed = true
      restartAnimationRef.current = () => undefined
      window.cancelAnimationFrame(animationFrame)
      resizeObserver.disconnect()
      mediaQuery.removeEventListener('change', restart)
      document.removeEventListener('visibilitychange', restart)
    }
  }, [resolvedArtwork])

  useEffect(() => {
    isPlayingRef.current = isPlaying
    restartAnimationRef.current()
  }, [isPlaying])

  return (
    <div
      ref={rootRef}
      className={`ambient-cover-visual${resolvedArtwork ? ' has-artwork' : ''}${isPlaying ? ' is-playing' : ''}`}
      style={style}
      aria-hidden="true"
    >
      <div className="ambient-cover-visual__artwork ambient-cover-visual__artwork--primary" />
      <div className="ambient-cover-visual__artwork ambient-cover-visual__artwork--echo" />
      <div className="ambient-cover-visual__color-field" />
      <canvas ref={canvasRef} className="ambient-cover-visual__particles" />
      <div className="ambient-cover-visual__cinema-veil" />
    </div>
  )
}

export default AmbientCoverVisual
