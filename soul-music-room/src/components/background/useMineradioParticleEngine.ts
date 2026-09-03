/*
 * Adapted from XxHuberrr/Mineradio public/index.html preset 5,
 * "WALLPAPER PULSE". GPL-3.0-only.
 */
import { useEffect, useRef, type RefObject } from 'react'
import * as THREE from 'three'
import {
  mineradioWallpaperFragmentShader,
  mineradioWallpaperVertexShader,
} from './mineradioWallpaperPulseShaders'

export type MineradioParticleEngineOptions = {
  playing: boolean
  coverUrl?: string
  primaryColor?: string
  secondaryColor?: string
  highlightColor?: string
  opacity?: number
  enablePointerParallax?: boolean
}

type EngineController = {
  destroy: () => void
  update: (options: MineradioParticleEngineOptions) => void
}

const GRID_SIZE = 183
const PARTICLE_COUNT = GRID_SIZE * GRID_SIZE
const RENDER_DPR_CAP = 1.35
const RENDER_PIXEL_BUDGET = 5_200_000
const RENDER_MIN_DPR = 0.72
const DEFAULT_PRIMARY = '#85dbff'
const DEFAULT_SECONDARY = '#b396ff'
const DEFAULT_HIGHLIGHT = '#f5faeb'

function createSeededRandom(seed: number) {
  let state = seed >>> 0
  return () => {
    state += 0x6D2B79F5
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

function createGeometry() {
  const geometry = new THREE.BufferGeometry()
  const positions = new Float32Array(PARTICLE_COUNT * 3)
  const uvs = new Float32Array(PARTICLE_COUNT * 2)
  const randoms = new Float32Array(PARTICLE_COUNT)
  const random = createSeededRandom(0x4D494E45)
  const texelStep = 1 / GRID_SIZE

  for (let index = 0; index < PARTICLE_COUNT; index += 1) {
    const gridX = index % GRID_SIZE
    const gridY = Math.floor(index / GRID_SIZE)
    const px = gridX / (GRID_SIZE - 1)
    const py = gridY / (GRID_SIZE - 1)

    positions[index * 3] = (px - 0.5) * 4.8
    positions[index * 3 + 1] = (py - 0.5) * 4.8
    positions[index * 3 + 2] = 0
    uvs[index * 2] = (gridX + 0.5) * texelStep
    uvs[index * 2 + 1] = (gridY + 0.5) * texelStep
    randoms[index] = random()
  }

  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setAttribute('aUv', new THREE.BufferAttribute(uvs, 2))
  geometry.setAttribute('aRand', new THREE.BufferAttribute(randoms, 1))
  return geometry
}

function createDotTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 64
  canvas.height = 64
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Unable to create Mineradio particle texture')

  const gradient = context.createRadialGradient(32, 32, 0, 32, 32, 31)
  gradient.addColorStop(0, 'rgba(255,255,255,0.96)')
  gradient.addColorStop(0.42, 'rgba(255,255,255,0.78)')
  gradient.addColorStop(0.72, 'rgba(255,255,255,0.22)')
  gradient.addColorStop(1, 'rgba(255,255,255,0)')
  context.fillStyle = gradient
  context.fillRect(0, 0, 64, 64)

  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  return texture
}

function createFallbackTexture() {
  const data = new Uint8Array([28, 28, 40, 255])
  const texture = new THREE.DataTexture(data, 1, 1, THREE.RGBAFormat)
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  texture.needsUpdate = true
  return texture
}

function getRenderPixelRatio(width: number, height: number) {
  const device = window.devicePixelRatio || 1
  const budgetCap = Math.sqrt(RENDER_PIXEL_BUDGET / Math.max(1, width * height))
  const cap = Math.min(RENDER_DPR_CAP, budgetCap)
  return Math.max(RENDER_MIN_DPR, Math.min(device, cap))
}

function safeColor(value: string | undefined, fallback: string) {
  try {
    return new THREE.Color(value || fallback)
  } catch {
    return new THREE.Color(fallback)
  }
}

function createEngine(
  canvas: HTMLCanvasElement,
  initialOptions: MineradioParticleEngineOptions,
): EngineController {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: false,
    alpha: true,
    powerPreference: 'high-performance',
  })
  renderer.setClearColor(0x000000, 0)

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100)
  const orbitRadius = 6.6
  const orbitPhi = 0.08
  camera.position.set(0, orbitRadius * Math.sin(orbitPhi), orbitRadius * Math.cos(orbitPhi))
  camera.lookAt(0, 0, 0)

  const geometry = createGeometry()
  const dotTexture = createDotTexture()
  const fallbackTexture = createFallbackTexture()
  const uniforms: Record<string, THREE.IUniform> = {
    uTime: { value: 0 },
    uBass: { value: 0 },
    uMid: { value: 0 },
    uTreble: { value: 0 },
    uBeat: { value: 0 },
    uEnergy: { value: 0 },
    uBurstAmt: { value: 0 },
    uPointScale: { value: 1 },
    uSpeed: { value: 1 },
    uColorBoost: { value: 1.1 },
    uPixel: { value: 1 },
    uColorMixT: { value: 1 },
    uCoverTex: { value: fallbackTexture },
    uPrevCoverTex: { value: fallbackTexture },
    uDotTex: { value: dotTexture },
    uAlpha: { value: 0 },
    uParticleDim: { value: 1 },
    uParallax: { value: new THREE.Vector2() },
    uPrimaryColor: { value: safeColor(initialOptions.primaryColor, DEFAULT_PRIMARY) },
    uSecondaryColor: { value: safeColor(initialOptions.secondaryColor, DEFAULT_SECONDARY) },
    uHighlightColor: { value: safeColor(initialOptions.highlightColor, DEFAULT_HIGHLIGHT) },
  }

  const material = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: mineradioWallpaperVertexShader,
    fragmentShader: mineradioWallpaperFragmentShader,
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending,
  })
  const particles = new THREE.Points(geometry, material)
  particles.frustumCulled = false
  scene.add(particles)

  const textureLoader = new THREE.TextureLoader()
  textureLoader.setCrossOrigin('anonymous')
  let options = initialOptions
  let targetPrimary = safeColor(initialOptions.primaryColor, DEFAULT_PRIMARY)
  let targetSecondary = safeColor(initialOptions.secondaryColor, DEFAULT_SECONDARY)
  let targetHighlight = safeColor(initialOptions.highlightColor, DEFAULT_HIGHLIGHT)
  let currentCoverTexture: THREE.Texture = fallbackTexture
  let previousCoverTexture: THREE.Texture = fallbackTexture
  let currentCoverUrl = ''
  let coverLoadToken = 0
  let colorMixStartedAt = 0
  let rafId = 0
  let lastFrameAt = performance.now()
  let destroyed = false
  let visible = !document.hidden
  let audioBass = 0
  let audioMid = 0
  let audioTreble = 0
  let audioEnergy = 0
  let alpha = 0
  let pointerUpdatedAt = 0
  const pointerTarget = new THREE.Vector2()
  const parallax = uniforms.uParallax.value as THREE.Vector2
  const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')

  const disposePreviousTexture = () => {
    if (previousCoverTexture !== fallbackTexture && previousCoverTexture !== currentCoverTexture) {
      previousCoverTexture.dispose()
    }
  }

  const beginCoverTransition = (nextTexture: THREE.Texture) => {
    disposePreviousTexture()
    previousCoverTexture = currentCoverTexture
    currentCoverTexture = nextTexture
    uniforms.uPrevCoverTex.value = previousCoverTexture
    uniforms.uCoverTex.value = currentCoverTexture
    uniforms.uColorMixT.value = 0
    uniforms.uBurstAmt.value = 0.05
    colorMixStartedAt = performance.now()
  }

  const setCover = (coverUrl?: string) => {
    const nextUrl = coverUrl || ''
    if (nextUrl === currentCoverUrl) return
    currentCoverUrl = nextUrl
    coverLoadToken += 1
    const token = coverLoadToken

    if (!nextUrl) {
      beginCoverTransition(fallbackTexture)
      return
    }

    const texture = textureLoader.load(
      nextUrl,
      () => {
        if (destroyed || token !== coverLoadToken) {
          texture.dispose()
          return
        }
        texture.minFilter = THREE.LinearFilter
        texture.magFilter = THREE.LinearFilter
        texture.wrapS = THREE.ClampToEdgeWrapping
        texture.wrapT = THREE.ClampToEdgeWrapping
        beginCoverTransition(texture)
      },
      undefined,
      () => texture.dispose(),
    )
  }

  const resize = () => {
    const width = Math.max(1, window.innerWidth)
    const height = Math.max(1, window.innerHeight)
    const dpr = getRenderPixelRatio(width, height)
    renderer.setPixelRatio(dpr)
    renderer.setSize(width, height, false)
    uniforms.uPixel.value = dpr
    camera.aspect = width / height
    camera.updateProjectionMatrix()
  }

  const handlePointerMove = (event: PointerEvent) => {
    if (!options.enablePointerParallax) return
    pointerTarget.set(
      ((event.clientX / Math.max(1, window.innerWidth)) - 0.5) * 0.34,
      -((event.clientY / Math.max(1, window.innerHeight)) - 0.5) * 0.22,
    )
    pointerUpdatedAt = performance.now()
  }

  const scheduleFrame = () => {
    if (!destroyed && visible && !rafId) rafId = requestAnimationFrame(renderFrame)
  }

  const handleVisibilityChange = () => {
    visible = !document.hidden
    if (!visible) {
      cancelAnimationFrame(rafId)
      rafId = 0
      return
    }
    lastFrameAt = performance.now()
    scheduleFrame()
  }

  const renderFrame = (now: number) => {
    rafId = 0
    if (destroyed || !visible) return

    const delta = Math.min(0.05, Math.max(0, (now - lastFrameAt) / 1000))
    lastFrameAt = now
    uniforms.uTime.value += delta * (reducedMotionQuery.matches ? 0.18 : 1)

    const playingScale = options.playing ? 1 : 0
    const audioEase = 1 - Math.exp(-delta * (options.playing ? 2.8 : 2.1))
    audioBass += (0.16 * playingScale - audioBass) * audioEase
    audioMid += (0.11 * playingScale - audioMid) * audioEase
    audioTreble += (0.085 * playingScale - audioTreble) * audioEase
    audioEnergy += (0.14 * playingScale - audioEnergy) * audioEase
    uniforms.uBass.value = audioBass
    uniforms.uMid.value = audioMid
    uniforms.uTreble.value = audioTreble
    uniforms.uBeat.value += (0 - uniforms.uBeat.value) * Math.min(1, delta * 5)
    uniforms.uEnergy.value = audioEnergy
    uniforms.uBurstAmt.value *= Math.pow(0.9, delta * 60)

    if (colorMixStartedAt) {
      const progress = Math.min(1, (now - colorMixStartedAt) / 920)
      uniforms.uColorMixT.value = progress * progress * (3 - 2 * progress)
      if (progress >= 1) {
        colorMixStartedAt = 0
        disposePreviousTexture()
        previousCoverTexture = currentCoverTexture
        uniforms.uPrevCoverTex.value = currentCoverTexture
      }
    }

    const targetAlpha = Math.min(1, Math.max(0, options.opacity ?? 1))
    alpha += (targetAlpha - alpha) * (1 - Math.exp(-delta * 4.2))
    uniforms.uAlpha.value = alpha

    if (!options.enablePointerParallax || now - pointerUpdatedAt > 1200) pointerTarget.set(0, 0)
    parallax.lerp(pointerTarget, 1 - Math.exp(-delta * 1.8))

    const colorEase = 1 - Math.exp(-delta * 2.4)
    ;(uniforms.uPrimaryColor.value as THREE.Color).lerp(targetPrimary, colorEase)
    ;(uniforms.uSecondaryColor.value as THREE.Color).lerp(targetSecondary, colorEase)
    ;(uniforms.uHighlightColor.value as THREE.Color).lerp(targetHighlight, colorEase)

    renderer.render(scene, camera)
    scheduleFrame()
  }

  const update = (nextOptions: MineradioParticleEngineOptions) => {
    options = nextOptions
    targetPrimary = safeColor(nextOptions.primaryColor, DEFAULT_PRIMARY)
    targetSecondary = safeColor(nextOptions.secondaryColor, DEFAULT_SECONDARY)
    targetHighlight = safeColor(nextOptions.highlightColor, DEFAULT_HIGHLIGHT)
    setCover(nextOptions.coverUrl)
  }

  resize()
  update(initialOptions)
  window.addEventListener('resize', resize)
  window.addEventListener('pointermove', handlePointerMove, { passive: true })
  document.addEventListener('visibilitychange', handleVisibilityChange)
  scheduleFrame()

  return {
    update,
    destroy: () => {
      if (destroyed) return
      destroyed = true
      coverLoadToken += 1
      cancelAnimationFrame(rafId)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', handlePointerMove)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      scene.remove(particles)
      material.dispose()
      geometry.dispose()
      dotTexture.dispose()
      disposePreviousTexture()
      if (currentCoverTexture !== fallbackTexture) currentCoverTexture.dispose()
      fallbackTexture.dispose()
      renderer.dispose()
      // StrictMode immediately mounts this effect again against the same DOM
      // canvas in development. Forcing a context loss here poisons that canvas
      // for the second mount; dispose() releases the Three.js resources while
      // keeping the canvas reusable.
    },
  }
}

export function useMineradioParticleEngine(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  options: MineradioParticleEngineOptions,
) {
  const engineRef = useRef<EngineController | null>(null)
  const initialOptionsRef = useRef(options)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const engine = createEngine(canvas, initialOptionsRef.current)
    engineRef.current = engine
    return () => {
      engine.destroy()
      engineRef.current = null
    }
  }, [canvasRef])

  useEffect(() => {
    engineRef.current?.update(options)
  }, [options])
}
