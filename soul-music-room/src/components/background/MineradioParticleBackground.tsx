import { useRef } from 'react'
import { useMineradioParticleEngine } from './useMineradioParticleEngine'
import './mineradio-particle-background.css'

export type MineradioParticleBackgroundProps = {
  playing: boolean
  coverUrl?: string
  primaryColor?: string
  secondaryColor?: string
  highlightColor?: string
  opacity?: number
  enablePointerParallax?: boolean
}

function MineradioParticleBackground({
  playing,
  coverUrl,
  primaryColor = '#85dbff',
  secondaryColor = '#b396ff',
  highlightColor = '#f5faeb',
  opacity = 1,
  enablePointerParallax = true,
}: MineradioParticleBackgroundProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useMineradioParticleEngine(canvasRef, {
    playing,
    coverUrl,
    primaryColor,
    secondaryColor,
    highlightColor,
    opacity,
    enablePointerParallax,
  })

  return (
    <canvas
      ref={canvasRef}
      className="mineradio-particle-background"
      aria-hidden="true"
    />
  )
}

export default MineradioParticleBackground
