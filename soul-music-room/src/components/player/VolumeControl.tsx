import { useEffect, useId, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Volume1, Volume2, VolumeX } from 'lucide-react'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import { useLiquidGlassScope } from '../liquid-glass/LiquidGlassContext'
import { usePlayerStore } from '../../store/playerStore'
import GlassIconButton from './GlassIconButton'

function rangeStyle(progress: number) {
  return {
    '--player-range-progress': `${progress}%`,
    '--range-progress': `${progress}%`,
  } as CSSProperties
}

function VolumeControl() {
  const volume = usePlayerStore((state) => state.volume)
  const isMuted = usePlayerStore((state) => state.isMuted)
  const setVolume = usePlayerStore((state) => state.setVolume)
  const toggleMute = usePlayerStore((state) => state.toggleMute)
  const [isOpen, setIsOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const panelId = useId()
  const inLiquidPlayer = useLiquidGlassScope()
  const percentage = Math.round(volume * 100)
  const muted = isMuted || volume === 0

  useEffect(() => {
    if (!isOpen) return

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsOpen(false)
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  const VolumeIcon = muted ? VolumeX : volume < 0.5 ? Volume1 : Volume2

  return (
    <div className="volume-control" ref={rootRef}>
      {isOpen && (
        <LiquidGlassSurface
          className="volume-control__popover"
          compact
          id={panelId}
          intensity={0.82}
          preset="control"
          radius={22}
          refract={inLiquidPlayer}
          role="group"
          aria-label="音量调节"
        >
          <div className="volume-control__heading">
            <span>音量</span>
            <output>{percentage}%</output>
          </div>
          <div className="volume-control__adjustment">
            <GlassIconButton
              className="volume-control__mute"
              icon={<VolumeIcon size={16} strokeWidth={1.8} aria-hidden="true" />}
              label={muted ? '恢复音量' : '静音'}
              active={muted}
              aria-label={muted ? '恢复音量' : '静音'}
              onClick={toggleMute}
            />
            <input
              className="player-range volume-control__range"
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={volume}
              onInput={(event) => setVolume(Number(event.currentTarget.value))}
              onChange={(event) => setVolume(Number(event.currentTarget.value))}
              aria-label="音量"
              aria-valuetext={`${percentage}%`}
              style={rangeStyle(percentage)}
            />
          </div>
        </LiquidGlassSurface>
      )}

      <GlassIconButton
        className={muted ? 'is-muted' : ''}
        icon={<VolumeIcon size={18} strokeWidth={1.75} aria-hidden="true" />}
        label={isOpen ? '收起音量控制' : muted ? '已静音，展开音量控制' : '展开音量控制'}
        active={isOpen}
        aria-label={isOpen ? '收起音量控制' : '展开音量控制'}
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={() => setIsOpen((open) => !open)}
      />
    </div>
  )
}

export default VolumeControl
