import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'
import './LiquidGlassCursor.css'

interface LiquidGlassCursorProps {
  eventSource: RefObject<HTMLElement>
  backgroundSelector: string
  imageSrc?: string
}

interface Point {
  x: number
  y: number
}

interface CoverMetrics {
  width: number
  height: number
  left: number
  top: number
}

const defaultOceanImage = '/images/ocean-bg-generated-hd.webp'
const lensRadius = 116
const fullCircle = Math.PI * 2

function createLensPath(
  context: CanvasRenderingContext2D,
  center: Point,
  radius: number,
) {
  context.beginPath()
  context.arc(center.x, center.y, radius, 0, fullCircle)
  context.closePath()
}

function getCoverMetrics(
  image: HTMLImageElement,
  backgroundBounds: DOMRect,
): CoverMetrics {
  const coverScale = Math.max(
    backgroundBounds.width / image.naturalWidth,
    backgroundBounds.height / image.naturalHeight,
  )
  const width = image.naturalWidth * coverScale
  const height = image.naturalHeight * coverScale

  return {
    width,
    height,
    left: backgroundBounds.left + (backgroundBounds.width - width) / 2,
    top: backgroundBounds.top + (backgroundBounds.height - height) / 2,
  }
}

function drawImageAtZoom(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  cover: CoverMetrics,
  center: Point,
  zoom: number,
  offsetX: number,
  offsetY: number,
) {
  const sourceX = (center.x - cover.left) / cover.width
  const sourceY = (center.y - cover.top) / cover.height
  const width = cover.width * zoom
  const height = cover.height * zoom
  const left = center.x - sourceX * width + offsetX
  const top = center.y - sourceY * height + offsetY

  context.drawImage(image, left, top, width, height)
}

function drawFluidCaustics(
  context: CanvasRenderingContext2D,
  center: Point,
  radius: number,
  time: number,
  direction: number,
) {
  context.save()
  context.globalCompositeOperation = 'screen'
  context.filter = 'blur(3px)'
  context.lineCap = 'round'

  for (let band = 0; band < 3; band += 1) {
    const verticalOffset =
      (band - 1) * 42 + Math.sin(time * 1.15 + band * 1.7) * 13
    const wave = Math.sin(time * 1.4 + band * 2.1) * 20
    const gradient = context.createLinearGradient(
      center.x - radius,
      center.y,
      center.x + radius,
      center.y,
    )

    gradient.addColorStop(0, 'rgba(111, 210, 255, 0)')
    gradient.addColorStop(0.32, 'rgba(191, 239, 255, 0.08)')
    gradient.addColorStop(0.58, 'rgba(255, 255, 255, 0.16)')
    gradient.addColorStop(1, 'rgba(131, 213, 249, 0)')

    context.beginPath()
    context.moveTo(center.x - radius * 0.92, center.y + verticalOffset)
    context.bezierCurveTo(
      center.x - radius * 0.36,
      center.y + verticalOffset + wave,
      center.x + radius * 0.28,
      center.y + verticalOffset - wave,
      center.x + radius * 0.92,
      center.y + verticalOffset + Math.sin(direction) * 8,
    )
    context.strokeStyle = gradient
    context.lineWidth = 2.2 + band * 0.55
    context.stroke()
  }

  context.restore()
}

function drawChromaticDispersion(
  context: CanvasRenderingContext2D,
  image: HTMLImageElement,
  cover: CoverMetrics,
  center: Point,
  radius: number,
  direction: number,
) {
  const splitX = Math.cos(direction) * 3.4
  const splitY = Math.sin(direction) * 3.4

  context.save()
  context.beginPath()
  context.arc(center.x, center.y, radius - 3, 0, fullCircle)
  context.arc(center.x, center.y, radius - 21, 0, fullCircle, true)
  context.clip('evenodd')
  context.globalCompositeOperation = 'screen'

  context.save()
  context.globalAlpha *= 0.075
  context.filter = 'sepia(1) saturate(7) hue-rotate(305deg) brightness(1.06)'
  drawImageAtZoom(
    context,
    image,
    cover,
    center,
    1.075,
    -splitX,
    -splitY,
  )
  context.restore()

  context.save()
  context.globalAlpha *= 0.09
  context.filter = 'sepia(1) saturate(6) hue-rotate(138deg) brightness(1.08)'
  drawImageAtZoom(
    context,
    image,
    cover,
    center,
    1.075,
    splitX,
    splitY,
  )
  context.restore()

  context.restore()
}

function drawGlassEdge(
  context: CanvasRenderingContext2D,
  center: Point,
  radius: number,
  viewportWidth: number,
  viewportHeight: number,
) {
  const lightDirection =
    Math.atan2(center.y - viewportHeight / 2, center.x - viewportWidth / 2) +
    Math.PI

  context.save()
  createLensPath(context, center, radius)
  context.shadowColor = 'rgba(0, 11, 24, 0.42)'
  context.shadowBlur = 25
  context.shadowOffsetY = 11
  context.fillStyle = 'rgba(3, 22, 37, 0.035)'
  context.fill()
  context.restore()

  context.save()
  createLensPath(context, center, radius - 3)
  const depthGradient = context.createRadialGradient(
    center.x - radius * 0.28,
    center.y - radius * 0.3,
    radius * 0.12,
    center.x,
    center.y,
    radius,
  )
  depthGradient.addColorStop(0, 'rgba(255, 255, 255, 0.025)')
  depthGradient.addColorStop(0.7, 'rgba(130, 211, 246, 0.015)')
  depthGradient.addColorStop(0.88, 'rgba(5, 32, 51, 0.12)')
  depthGradient.addColorStop(0.965, 'rgba(136, 211, 241, 0.045)')
  depthGradient.addColorStop(1, 'rgba(255, 255, 255, 0.015)')
  context.fillStyle = depthGradient
  context.fill()
  context.restore()

  context.save()
  createLensPath(context, center, radius)
  const rimGradient = context.createLinearGradient(
    center.x - radius,
    center.y - radius,
    center.x + radius,
    center.y + radius,
  )
  rimGradient.addColorStop(0, 'rgba(244, 253, 255, 0.78)')
  rimGradient.addColorStop(0.22, 'rgba(178, 226, 246, 0.2)')
  rimGradient.addColorStop(0.48, 'rgba(5, 29, 46, 0.2)')
  rimGradient.addColorStop(0.74, 'rgba(128, 211, 246, 0.26)')
  rimGradient.addColorStop(1, 'rgba(250, 254, 255, 0.62)')
  context.strokeStyle = rimGradient
  context.lineWidth = 2.2
  context.stroke()
  context.restore()

  context.save()
  createLensPath(context, center, radius - 2)
  context.clip()
  const highlightX = center.x + Math.cos(lightDirection) * radius * 0.58
  const highlightY = center.y + Math.sin(lightDirection) * radius * 0.58
  const highlight = context.createRadialGradient(
    highlightX,
    highlightY,
    0,
    highlightX,
    highlightY,
    radius * 0.38,
  )
  highlight.addColorStop(0, 'rgba(255, 255, 255, 0.18)')
  highlight.addColorStop(0.42, 'rgba(176, 228, 250, 0.06)')
  highlight.addColorStop(1, 'rgba(255, 255, 255, 0)')
  context.fillStyle = highlight
  context.fillRect(
    center.x - radius,
    center.y - radius,
    radius * 2,
    radius * 2,
  )
  context.restore()
}

function LiquidGlassCursor({
  eventSource,
  backgroundSelector,
  imageSrc = defaultOceanImage,
}: LiquidGlassCursorProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const page = eventSource.current
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d', { alpha: true })
    const background = page?.querySelector<HTMLElement>(backgroundSelector)

    if (!page || !canvas || !context || !background) return

    const desktopQuery = window.matchMedia('(min-width: 901px)')
    const finePointerQuery = window.matchMedia('(hover: hover) and (pointer: fine)')
    const reducedMotionQuery = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    )
    const image = new Image()
    const pointer = {
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
      targetX: window.innerWidth / 2,
      targetY: window.innerHeight / 2,
      lastX: window.innerWidth / 2,
      lastY: window.innerHeight / 2,
    }
    let imageReady = false
    let frame = 0
    let viewportWidth = window.innerWidth
    let viewportHeight = window.innerHeight
    let pixelRatio = 1
    let opacity = 0
    let targetOpacity = 0

    image.src = imageSrc
    image.onload = () => {
      imageReady = true
    }

    const resizeCanvas = () => {
      viewportWidth = window.innerWidth
      viewportHeight = window.innerHeight
      pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.round(viewportWidth * pixelRatio)
      canvas.height = Math.round(viewportHeight * pixelRatio)
      canvas.style.width = `${viewportWidth}px`
      canvas.style.height = `${viewportHeight}px`
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
      context.imageSmoothingEnabled = true
      context.imageSmoothingQuality = 'high'
    }

    const updatePointer = (event: PointerEvent) => {
      pointer.targetX = event.clientX
      pointer.targetY = event.clientY
    }

    const showLens = (event: PointerEvent) => {
      updatePointer(event)

      if (opacity < 0.02) {
        pointer.x = event.clientX
        pointer.y = event.clientY
        pointer.lastX = event.clientX
        pointer.lastY = event.clientY
      }

      targetOpacity = 1
    }

    const hideLens = () => {
      targetOpacity = 0
    }

    const render = (timestamp: number) => {
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
      context.clearRect(0, 0, viewportWidth, viewportHeight)

      if (desktopQuery.matches && finePointerQuery.matches && imageReady) {
        const reducedMotion = reducedMotionQuery.matches
        const followStrength = reducedMotion ? 0.24 : 0.125
        const fadeStrength = reducedMotion ? 0.34 : 0.105
        const deltaX = pointer.targetX - pointer.x
        const deltaY = pointer.targetY - pointer.y

        pointer.x += deltaX * followStrength
        pointer.y += deltaY * followStrength
        opacity += (targetOpacity - opacity) * fadeStrength

        const velocityX = pointer.x - pointer.lastX
        const velocityY = pointer.y - pointer.lastY
        const speed = Math.min(Math.hypot(velocityX, velocityY), 22)
        const direction =
          speed > 0.08 ? Math.atan2(velocityY, velocityX) : -Math.PI / 4
        const time = reducedMotion ? 0 : timestamp / 1000
        const center = { x: pointer.x, y: pointer.y }
        const cover = getCoverMetrics(
          image,
          background.getBoundingClientRect(),
        )
        const ringCount = reducedMotion ? 24 : 42
        const pageBounds = page.getBoundingClientRect()

        context.save()
        context.beginPath()
        context.rect(
          pageBounds.left,
          pageBounds.top,
          pageBounds.width,
          pageBounds.height,
        )
        context.clip()
        context.globalAlpha = Math.max(0, Math.min(1, opacity))

        context.save()
        createLensPath(context, center, lensRadius)
        context.clip()
        context.filter = 'brightness(0.88) contrast(1.1) saturate(1.08)'

        for (let ring = 0; ring < ringCount; ring += 1) {
          const progress = ring / (ringCount - 1)
          const radius = lensRadius * (1 - progress * 0.94) + 1.5
          const curvedProgress = Math.pow(progress, 1.42)
          const zoom = 1 + curvedProgress * 0.29
          const waveStrength = reducedMotion ? 0 : curvedProgress * 4.2
          const flow =
            Math.sin(time * 1.22 + progress * 9.4) * waveStrength +
            Math.sin(time * 0.7 - progress * 5.8) * waveStrength * 0.45
          const movementX = velocityX * 0.065 * curvedProgress
          const movementY = velocityY * 0.065 * curvedProgress
          const offsetX =
            Math.cos(direction + progress * 1.4) * flow + movementX
          const offsetY =
            Math.sin(direction + progress * 1.4) * flow + movementY

          context.save()
          context.beginPath()
          context.arc(center.x, center.y, radius, 0, fullCircle)
          context.clip()
          drawImageAtZoom(
            context,
            image,
            cover,
            center,
            zoom,
            offsetX,
            offsetY,
          )
          context.restore()
        }

        drawFluidCaustics(
          context,
          center,
          lensRadius,
          time,
          direction,
        )
        context.restore()

        drawChromaticDispersion(
          context,
          image,
          cover,
          center,
          lensRadius,
          direction,
        )

        drawGlassEdge(
          context,
          center,
          lensRadius,
          viewportWidth,
          viewportHeight,
        )
        context.restore()

        pointer.lastX = pointer.x
        pointer.lastY = pointer.y
      }

      frame = window.requestAnimationFrame(render)
    }

    resizeCanvas()
    page.addEventListener('pointerenter', showLens, { passive: true })
    page.addEventListener('pointermove', updatePointer, { passive: true })
    page.addEventListener('pointerleave', hideLens, { passive: true })
    window.addEventListener('blur', hideLens)
    window.addEventListener('resize', resizeCanvas)
    frame = window.requestAnimationFrame(render)

    return () => {
      page.removeEventListener('pointerenter', showLens)
      page.removeEventListener('pointermove', updatePointer)
      page.removeEventListener('pointerleave', hideLens)
      window.removeEventListener('blur', hideLens)
      window.removeEventListener('resize', resizeCanvas)
      window.cancelAnimationFrame(frame)
      image.onload = null
    }
  }, [backgroundSelector, eventSource, imageSrc])

  return (
    <canvas
      className="liquid-glass-cursor"
      ref={canvasRef}
      aria-hidden="true"
    />
  )
}

export default LiquidGlassCursor
