import LiquidGlass from 'liquid-glass-react'
import {
  useCallback,
  useId,
  useRef,
  type CSSProperties,
  type HTMLAttributes,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import LiquidGlassFilter from './LiquidGlassFilter'
import {
  LiquidGlassScopeContext,
  useLiquidGlassRuntime,
} from './LiquidGlassContext'
import { useLiquidGlassMap } from './useLiquidGlassMap'
import './liquid-glass.css'

export type LiquidGlassPreset = 'panel' | 'dock' | 'overlay' | 'control'

interface LiquidGlassSurfaceProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  children: ReactNode
  compact?: boolean
  disabled?: boolean
  intensity?: number
  interactive?: boolean
  preset?: LiquidGlassPreset
  radius?: number
  refract?: boolean
}

const LEGACY_PRESETS: Record<LiquidGlassPreset, {
  aberration: number
  blur: number
  displacement: number
  elasticity: number
  mode: 'standard' | 'polar' | 'prominent'
  saturation: number
}> = {
  panel: { aberration: 0.82, blur: 0.025, displacement: 48, elasticity: 0.26, mode: 'standard', saturation: 148 },
  dock: { aberration: 1.08, blur: 0.018, displacement: 84, elasticity: 0.36, mode: 'prominent', saturation: 158 },
  overlay: { aberration: 0.86, blur: 0.045, displacement: 52, elasticity: 0.2, mode: 'polar', saturation: 150 },
  control: { aberration: 0.98, blur: 0.018, displacement: 68, elasticity: 0.34, mode: 'prominent', saturation: 156 },
}

function stableId(prefix: string, id: string) {
  return `${prefix}-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`
}

function LiquidGlassSurface({
  children,
  className = '',
  compact = false,
  disabled = false,
  intensity = 1,
  interactive = false,
  onPointerLeave,
  onPointerMove,
  preset = 'panel',
  radius = 32,
  refract = false,
  style,
  ...surfaceProps
}: LiquidGlassSurfaceProps) {
  const surfaceRef = useRef<HTMLDivElement>(null)
  const pointerFrameRef = useRef(0)
  const pointerTargetRef = useRef({ x: 0, y: 0 })
  const reactId = useId()
  const filterId = stableId('moodflow-surface-filter', reactId)
  const mapId = stableId('moodflow-surface-map', reactId)
  const { mode, reducedMotion } = useLiquidGlassRuntime()
  const normalizedIntensity = Math.min(Math.max(intensity, 0), 1.5)
  const fullLiquidEnabled = refract && mode === 'full' && !disabled
  const legacyRuntimeEnabled = !refract && mode === 'full' && !disabled
  const legacySettings = LEGACY_PRESETS[preset]
  const { mapHref } = useLiquidGlassMap(surfaceRef, {
    enabled: fullLiquidEnabled,
    kind: compact ? 'compact' : 'panel',
    radius,
  })

  const updatePointer = useCallback(() => {
    pointerFrameRef.current = 0
    const element = surfaceRef.current
    if (!element) return
    element.style.setProperty('--liquid-glass-pointer-x', `${pointerTargetRef.current.x}px`)
    element.style.setProperty('--liquid-glass-pointer-y', `${pointerTargetRef.current.y}px`)
  }, [])

  const queuePointer = useCallback((x: number, y: number) => {
    pointerTargetRef.current = { x, y }
    if (!pointerFrameRef.current) {
      pointerFrameRef.current = window.requestAnimationFrame(updatePointer)
    }
  }, [updatePointer])

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    onPointerMove?.(event)
    if (!interactive || !fullLiquidEnabled || reducedMotion) return
    const rect = event.currentTarget.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width - 0.5) * 4
    const y = ((event.clientY - rect.top) / rect.height - 0.5) * 3
    queuePointer(x, y)
  }

  const handlePointerLeave = (event: ReactPointerEvent<HTMLDivElement>) => {
    onPointerLeave?.(event)
    if (interactive) queuePointer(0, 0)
  }

  const surfaceStyle = {
    ...style,
    '--liquid-glass-radius': `${radius}px`,
    '--liquid-glass-intensity': normalizedIntensity,
    '--liquid-glass-svg-filter': `url("#${filterId}") saturate(1)`,
  } as CSSProperties

  if (refract) {
    return (
      <LiquidGlassScopeContext.Provider value={fullLiquidEnabled}>
        <div
          {...surfaceProps}
          ref={surfaceRef}
          className={`liquid-glass-surface liquid-glass-surface--${preset} is-mineradio-liquid ${fullLiquidEnabled ? 'is-runtime' : 'is-fallback'} ${className}`.trim()}
          data-liquid-glass={fullLiquidEnabled ? 'full' : 'fallback'}
          onPointerMove={handlePointerMove}
          onPointerLeave={handlePointerLeave}
          style={surfaceStyle}
        >
          {fullLiquidEnabled && mapHref && (
            <LiquidGlassFilter
              animated={!reducedMotion}
              compact={compact}
              filterId={filterId}
              mapHref={mapHref}
              mapId={mapId}
            />
          )}
          <div className="liquid-glass-surface__optics" aria-hidden="true">
            <span className="liquid-glass-surface__refraction" />
            <span className="liquid-glass-surface__highlight" />
          </div>
          <div className="liquid-glass-surface__content">{children}</div>
        </div>
      </LiquidGlassScopeContext.Provider>
    )
  }

  return (
    <LiquidGlassScopeContext.Provider value={false}>
      <div
        {...surfaceProps}
        ref={surfaceRef}
        className={`liquid-glass-surface liquid-glass-surface--${preset} ${legacyRuntimeEnabled ? 'is-runtime' : 'is-fallback'} ${className}`.trim()}
        data-liquid-glass={legacyRuntimeEnabled ? 'legacy-runtime' : 'fallback'}
        style={surfaceStyle}
      >
        {legacyRuntimeEnabled && (
          <div className="liquid-glass-surface__runtime" aria-hidden="true">
            <LiquidGlass
              aberrationIntensity={legacySettings.aberration * normalizedIntensity}
              blurAmount={legacySettings.blur}
              cornerRadius={radius}
              displacementScale={legacySettings.displacement * normalizedIntensity}
              elasticity={reducedMotion ? 0 : legacySettings.elasticity}
              mode={legacySettings.mode}
              mouseContainer={surfaceRef}
              padding="0"
              saturation={legacySettings.saturation}
              style={{ position: 'absolute', top: '50%', left: '50%', width: '100%', height: '100%' }}
            >
              <span className="liquid-glass-surface__refracted-fill" />
            </LiquidGlass>
          </div>
        )}
        <span className="liquid-glass-surface__tint" aria-hidden="true" />
        <span className="liquid-glass-surface__rim" aria-hidden="true" />
        <div className="liquid-glass-surface__content">{children}</div>
      </div>
    </LiquidGlassScopeContext.Provider>
  )
}

export default LiquidGlassSurface
