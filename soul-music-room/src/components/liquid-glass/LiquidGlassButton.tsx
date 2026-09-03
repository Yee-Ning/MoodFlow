import {
  forwardRef,
  useCallback,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type MutableRefObject,
} from 'react'
import LiquidGlassFilter from './LiquidGlassFilter'
import {
  useLiquidGlassRuntime,
  useLiquidGlassScope,
} from './LiquidGlassContext'
import { useLiquidGlassMap } from './useLiquidGlassMap'

export interface LiquidGlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  primary?: boolean
}

function stableId(prefix: string, id: string) {
  return `${prefix}-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`
}

const LiquidGlassButton = forwardRef<HTMLButtonElement, LiquidGlassButtonProps>(
  ({ children, className = '', disabled = false, primary = false, style, type = 'button', ...props }, forwardedRef) => {
    const localRef = useRef<HTMLButtonElement>(null)
    const reactId = useId()
    const filterId = stableId('moodflow-button-filter', reactId)
    const mapId = stableId('moodflow-button-map', reactId)
    const { mode, reducedMotion } = useLiquidGlassRuntime()
    const inLiquidPlayer = useLiquidGlassScope()
    const enabled = inLiquidPlayer && primary && mode === 'full'
    const presentation = enabled ? 'full' : inLiquidPlayer ? 'plain' : 'fallback'
    const { mapHref } = useLiquidGlassMap(localRef, {
      enabled,
      kind: 'compact',
      radius: 999,
    })

    const setRef = useCallback((node: HTMLButtonElement | null) => {
      localRef.current = node
      if (typeof forwardedRef === 'function') forwardedRef(node)
      else if (forwardedRef) (forwardedRef as MutableRefObject<HTMLButtonElement | null>).current = node
    }, [forwardedRef])

    return (
      <button
        {...props}
        ref={setRef}
        className={`liquid-glass-button is-liquid-button-${presentation}${enabled ? ' is-liquid-button' : ''}${primary ? ' is-liquid-button-primary' : ''} ${className}`.trim()}
        data-liquid-glass-button={presentation}
        disabled={disabled}
        type={type}
        style={{
          ...style,
          '--liquid-glass-button-filter': `url("#${filterId}") saturate(1)`,
        } as CSSProperties}
      >
        {enabled && mapHref && (
          <LiquidGlassFilter
            animated={!reducedMotion}
            compact
            filterId={filterId}
            mapHref={mapHref}
            mapId={mapId}
          />
        )}
        <span className="liquid-glass-button__optics" aria-hidden="true" />
        <span className="liquid-glass-button__content">{children}</span>
      </button>
    )
  },
)

LiquidGlassButton.displayName = 'LiquidGlassButton'

export default LiquidGlassButton
