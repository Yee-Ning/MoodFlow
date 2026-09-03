import {
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  LiquidGlassRuntimeContext,
  type LiquidGlassMode,
} from './LiquidGlassContext'

let lastLoggedMode: LiquidGlassMode | null = null

function mediaMatches(query: string) {
  return typeof window !== 'undefined' && window.matchMedia(query).matches
}

function supportsSvgBackdropFilter() {
  if (typeof document === 'undefined' || typeof CSS === 'undefined') return false

  const value = 'url("#liquid-glass-support-probe") saturate(1)'
  const supportsDeclaration = (
    CSS.supports('backdrop-filter', value)
    || CSS.supports('-webkit-backdrop-filter', value)
  )
  if (!supportsDeclaration) return false

  const probe = document.createElement('div')
  probe.style.backdropFilter = value
  const webkitStyle = probe.style as CSSStyleDeclaration & {
    webkitBackdropFilter?: string
  }
  webkitStyle.webkitBackdropFilter = value

  return probe.style.backdropFilter !== '' || webkitStyle.webkitBackdropFilter !== ''
}

interface LiquidGlassProviderProps {
  children: ReactNode
}

export function LiquidGlassProvider({ children }: LiquidGlassProviderProps) {
  const [supportsFullMode] = useState(supportsSvgBackdropFilter)
  const [reducedMotion, setReducedMotion] = useState(() => (
    mediaMatches('(prefers-reduced-motion: reduce)')
  ))
  const [reducedTransparency, setReducedTransparency] = useState(() => (
    mediaMatches('(prefers-reduced-transparency: reduce)')
  ))
  const mode: LiquidGlassMode = supportsFullMode && !reducedTransparency
    ? 'full'
    : 'fallback'

  useEffect(() => {
    const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
    const transparencyQuery = window.matchMedia('(prefers-reduced-transparency: reduce)')
    const updateMotion = (event: MediaQueryListEvent) => setReducedMotion(event.matches)
    const updateTransparency = (event: MediaQueryListEvent) => setReducedTransparency(event.matches)

    motionQuery.addEventListener('change', updateMotion)
    transparencyQuery.addEventListener('change', updateTransparency)
    return () => {
      motionQuery.removeEventListener('change', updateMotion)
      transparencyQuery.removeEventListener('change', updateTransparency)
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('liquid-glass-supported', mode === 'full')
    root.classList.toggle('liquid-glass-fallback', mode === 'fallback')

    if (import.meta.env.DEV && lastLoggedMode !== mode) {
      lastLoggedMode = mode
      console.info(`[MoodFlow Liquid Glass] ${mode === 'full' ? 'full liquid mode' : 'fallback glass mode'}`)
    }

    return () => {
      root.classList.remove('liquid-glass-supported', 'liquid-glass-fallback')
    }
  }, [mode])

  const value = useMemo(() => ({ mode, reducedMotion }), [mode, reducedMotion])

  return (
    <LiquidGlassRuntimeContext.Provider value={value}>
      <svg
        className="liquid-glass-provider__defs"
        width="0"
        height="0"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <filter id="liquid-glass-support-probe" colorInterpolationFilters="sRGB">
            <feGaussianBlur stdDeviation="0" />
          </filter>
        </defs>
      </svg>
      {children}
    </LiquidGlassRuntimeContext.Provider>
  )
}
