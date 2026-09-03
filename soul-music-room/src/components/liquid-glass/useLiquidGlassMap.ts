import { useLayoutEffect, useRef, useState, type RefObject } from 'react'

export type LiquidGlassMapKind = 'panel' | 'compact'

interface UseLiquidGlassMapOptions {
  enabled: boolean
  kind?: LiquidGlassMapKind
  radius: number
}

const PANEL_MIN_WIDTH = 240
const MAP_MIN_HEIGHT = 48
const COMPACT_MIN_WIDTH = 48

export function generateControlGlassDisplacementMap(
  rawWidth: number,
  rawHeight: number,
  rawRadius: number,
  kind: LiquidGlassMapKind = 'panel',
) {
  const minimumWidth = kind === 'compact' ? COMPACT_MIN_WIDTH : PANEL_MIN_WIDTH
  const width = Math.max(minimumWidth, Math.round(rawWidth || 400))
  const height = Math.max(MAP_MIN_HEIGHT, Math.round(rawHeight || 92))
  const radius = Math.max(
    12,
    Math.min(Math.round(rawRadius || 50), Math.round(width / 2), Math.round(height / 2)),
  )
  const borderWidth = 0.07
  const edge = Math.min(width, height) * (borderWidth * 0.5)
  const innerWidth = Math.max(1, width - edge * 2)
  const innerHeight = Math.max(1, height - edge * 2)

  return [
    `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`,
    '<defs>',
    '<linearGradient id="glass-red" x1="100%" y1="0%" x2="0%" y2="0%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="red"/></linearGradient>',
    '<linearGradient id="glass-blue" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#0000"/><stop offset="100%" stop-color="blue"/></linearGradient>',
    '</defs>',
    `<rect x="0" y="0" width="${width}" height="${height}" fill="black"/>`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="${radius}" fill="url(#glass-red)"/>`,
    `<rect x="0" y="0" width="${width}" height="${height}" rx="${radius}" fill="url(#glass-blue)" style="mix-blend-mode:difference"/>`,
    `<rect x="${edge.toFixed(2)}" y="${edge.toFixed(2)}" width="${innerWidth.toFixed(2)}" height="${innerHeight.toFixed(2)}" rx="${radius}" fill="hsl(0 0% 50% / 1)" style="filter:blur(11px)"/>`,
    '</svg>',
  ].join('')
}

function measuredRadius(element: HTMLElement, fallback: number, width: number, height: number) {
  const computed = Number.parseFloat(window.getComputedStyle(element).borderRadius)
  const candidate = Number.isFinite(computed) ? computed : fallback
  return Math.max(12, Math.min(candidate, width / 2, height / 2))
}

export function useLiquidGlassMap<T extends HTMLElement>(
  targetRef: RefObject<T | null>,
  { enabled, kind = 'panel', radius }: UseLiquidGlassMapOptions,
) {
  const [mapHref, setMapHref] = useState('')
  const keyRef = useRef('')
  const urlRef = useRef('')

  useLayoutEffect(() => {
    if (!enabled) {
      keyRef.current = ''
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = ''
      return
    }

    const element = targetRef.current
    if (!element) return

    let frame = 0
    let disposed = false

    const measure = () => {
      frame = 0
      if (disposed) return

      const rect = element.getBoundingClientRect()
      if (rect.width < 2 || rect.height < 2) return

      const nextGeometry = {
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        radius: Math.round(measuredRadius(element, radius, rect.width, rect.height)),
      }
      const key = `${kind}:${nextGeometry.width}x${nextGeometry.height}:${nextGeometry.radius}`
      if (key === keyRef.current) return

      const svg = generateControlGlassDisplacementMap(
        nextGeometry.width,
        nextGeometry.height,
        nextGeometry.radius,
        kind,
      )
      const nextUrl = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
      const previousUrl = urlRef.current

      keyRef.current = key
      urlRef.current = nextUrl
      setMapHref(nextUrl)
      if (previousUrl) URL.revokeObjectURL(previousUrl)
    }

    const queueMeasure = () => {
      if (frame) return
      frame = window.requestAnimationFrame(measure)
    }

    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(queueMeasure)

    observer?.observe(element)
    window.addEventListener('resize', queueMeasure, { passive: true })
    queueMeasure()

    return () => {
      disposed = true
      observer?.disconnect()
      window.removeEventListener('resize', queueMeasure)
      if (frame) window.cancelAnimationFrame(frame)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = ''
      keyRef.current = ''
    }
  }, [enabled, kind, radius, targetRef])

  return { mapHref }
}
