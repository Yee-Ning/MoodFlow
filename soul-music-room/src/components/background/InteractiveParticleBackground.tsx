import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { particleFragmentShader, particleVertexShader } from './particleShaders'
import './particle-background.css'

interface InteractiveParticleBackgroundProps {
  isPlaying: boolean
}

interface ParticleLayerDefinition {
  count: number
  flowStrength: number
  glowStrength: number
  opacity: readonly [number, number]
  parallaxStrength: number
  pointSize: readonly [number, number]
  repulsionRadius: number
  repulsionStrength: number
  speed: readonly [number, number]
}

interface ParticleLayer {
  definition: ParticleLayerDefinition
  geometry: THREE.BufferGeometry
  material: THREE.ShaderMaterial
  points: THREE.Points
}

interface DeviceProfile {
  dpr: number
  farCount: number
  midCount: number
  nearCount: number
  mobile: boolean
  reducedMotion: boolean
}

const PARTICLE_COLORS = [
  [0.874, 0.969, 1],
  [0.667, 0.839, 0.91],
  [0.424, 0.529, 0.592],
  [0.494, 0.78, 0.847],
  [0.4, 0.451, 0.651],
  [0.48, 0.66, 0.58],
] as const

const clamp = (value: number, minimum: number, maximum: number) => (
  Math.min(maximum, Math.max(minimum, value))
)

function createRandom(seed: number) {
  let state = seed >>> 0
  return () => {
    state += 0x6D2B79F5
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

function getDeviceProfile(): DeviceProfile {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const mobile = window.matchMedia('(max-width: 840px), (pointer: coarse)').matches
  const memory = (window.navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8
  const cores = window.navigator.hardwareConcurrency || 8
  const constrained = memory <= 4 || cores <= 4

  if (mobile) {
    return {
      dpr: Math.min(window.devicePixelRatio || 1, 1),
      farCount: reducedMotion ? 340 : 420,
      midCount: reducedMotion ? 125 : 175,
      nearCount: reducedMotion ? 14 : 24,
      mobile: true,
      reducedMotion,
    }
  }

  if (constrained || reducedMotion) {
    return {
      dpr: Math.min(window.devicePixelRatio || 1, reducedMotion ? 1 : 1.15),
      farCount: reducedMotion ? 620 : 900,
      midCount: reducedMotion ? 185 : 340,
      nearCount: reducedMotion ? 20 : 44,
      mobile: false,
      reducedMotion,
    }
  }

  return {
    dpr: Math.min(window.devicePixelRatio || 1, 1.5),
    farCount: 1180,
    midCount: 480,
    nearCount: 68,
    mobile: false,
    reducedMotion: false,
  }
}

function createLayer(
  definition: ParticleLayerDefinition,
  seed: number,
  resolution: THREE.Vector2,
) {
  const random = createRandom(seed)
  const positions = new Float32Array(definition.count * 3)
  const depths = new Float32Array(definition.count)
  const opacities = new Float32Array(definition.count)
  const phases = new Float32Array(definition.count)
  const sizes = new Float32Array(definition.count)
  const speeds = new Float32Array(definition.count)
  const tints = new Float32Array(definition.count * 3)

  for (let index = 0; index < definition.count; index += 1) {
    let x = random() * 2.08 - 1.04
    let y = random() * 2.08 - 1.04

    if (random() < 0.34) {
      const edge = Math.floor(random() * 4)
      const inset = random() * 0.2
      if (edge === 0) x = -1.04 + inset
      if (edge === 1) x = 1.04 - inset
      if (edge === 2) y = -1.04 + inset
      if (edge === 3) y = 1.04 - inset
    } else if (Math.abs(x) < 0.38 && Math.abs(y) < 0.3 && random() < 0.58) {
      x += Math.sign(x || random() - 0.5) * (0.2 + random() * 0.28)
      y += Math.sign(y || random() - 0.5) * random() * 0.16
    }

    const positionOffset = index * 3
    const depth = 0.18 + random() * 0.82
    const color = PARTICLE_COLORS[Math.floor(random() * PARTICLE_COLORS.length)]
    positions[positionOffset] = x
    positions[positionOffset + 1] = y
    positions[positionOffset + 2] = 0
    depths[index] = depth
    opacities[index] = definition.opacity[0]
      + random() * (definition.opacity[1] - definition.opacity[0])
    phases[index] = random() * Math.PI * 2
    sizes[index] = definition.pointSize[0]
      + random() * (definition.pointSize[1] - definition.pointSize[0])
    speeds[index] = definition.speed[0]
      + random() * (definition.speed[1] - definition.speed[0])
    tints[positionOffset] = color[0]
    tints[positionOffset + 1] = color[1]
    tints[positionOffset + 2] = color[2]
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setAttribute('aDepth', new THREE.BufferAttribute(depths, 1))
  geometry.setAttribute('aOpacity', new THREE.BufferAttribute(opacities, 1))
  geometry.setAttribute('aPhase', new THREE.BufferAttribute(phases, 1))
  geometry.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1))
  geometry.setAttribute('aSpeed', new THREE.BufferAttribute(speeds, 1))
  geometry.setAttribute('aTint', new THREE.BufferAttribute(tints, 3))

  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthTest: false,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    vertexShader: particleVertexShader,
    fragmentShader: particleFragmentShader,
    uniforms: {
      uTime: { value: 0 },
      uPlayback: { value: 0 },
      uFlowStrength: { value: definition.flowStrength },
      uGlowStrength: { value: definition.glowStrength },
      uParallaxStrength: { value: definition.parallaxStrength },
      uRepulsionStrength: { value: definition.repulsionStrength },
      uRepulsionRadius: { value: definition.repulsionRadius },
      uPointScale: { value: 1 },
      uPixelRatio: { value: 1 },
      uMouse: { value: new THREE.Vector2() },
      uResolution: { value: resolution },
    },
  })

  const points = new THREE.Points(geometry, material)
  points.frustumCulled = false
  return { definition, geometry, material, points } satisfies ParticleLayer
}

function InteractiveParticleBackground({ isPlaying }: InteractiveParticleBackgroundProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const isPlayingRef = useRef(isPlaying)

  useEffect(() => {
    isPlayingRef.current = isPlaying
  }, [isPlaying])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const profile = getDeviceProfile()
    const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
    const resolution = new THREE.Vector2(window.innerWidth, window.innerHeight)
    const scene = new THREE.Scene()
    const camera = new THREE.Camera()
    const mouseTarget = new THREE.Vector2()
    const mouseCurrent = new THREE.Vector2()
    let renderer: THREE.WebGLRenderer | null = null
    let layers: ParticleLayer[] = []
    let animationFrame = 0
    let elapsedTime = 0
    let lastTimestamp = performance.now()
    let lastPointerMove = lastTimestamp
    let playbackMix = isPlayingRef.current ? 1 : 0
    let qualityLevel = 0
    let currentDpr = profile.dpr
    let benchmarkStarted = lastTimestamp
    let benchmarkFrames = 0
    let destroyed = false

    const layerDefinitions: ParticleLayerDefinition[] = [
      {
        count: profile.farCount,
        flowStrength: 0.007,
        glowStrength: 0.46,
        opacity: [0.16, 0.34],
        parallaxStrength: 0.004,
        pointSize: [1.5, 2.8],
        repulsionRadius: 0,
        repulsionStrength: 0,
        speed: [0.08, 0.15],
      },
      {
        count: profile.midCount,
        flowStrength: 0.021,
        glowStrength: 0.72,
        opacity: [0.32, 0.64],
        parallaxStrength: 0.016,
        pointSize: [2.8, 5.2],
        repulsionRadius: 0.27,
        repulsionStrength: 0.042,
        speed: [0.14, 0.25],
      },
      {
        count: profile.nearCount,
        flowStrength: 0.035,
        glowStrength: 1.02,
        opacity: [0.46, 0.8],
        parallaxStrength: 0.028,
        pointSize: [4.8, 8.6],
        repulsionRadius: 0.32,
        repulsionStrength: 0.06,
        speed: [0.18, 0.31],
      },
    ]

    const updateQuality = (nextLevel: number) => {
      if (!renderer) return
      qualityLevel = clamp(nextLevel, 0, 2)
      const drawRatio = qualityLevel === 0 ? 1 : qualityLevel === 1 ? 0.72 : 0.5
      const nearRatio = qualityLevel === 0 ? 1 : qualityLevel === 1 ? 0.52 : 0.28
      currentDpr = Math.min(
        profile.dpr,
        qualityLevel === 0 ? profile.dpr : qualityLevel === 1 ? 1.05 : 0.85,
      )
      renderer.setPixelRatio(currentDpr)
      renderer.setSize(window.innerWidth, window.innerHeight, false)

      layers.forEach((layer, index) => {
        const ratio = index === 2 ? nearRatio : drawRatio
        layer.geometry.setDrawRange(0, Math.max(1, Math.floor(layer.definition.count * ratio)))
        layer.material.uniforms.uPixelRatio.value = currentDpr
        layer.material.uniforms.uGlowStrength.value = layer.definition.glowStrength
          * (qualityLevel === 0 ? 1 : qualityLevel === 1 ? 0.76 : 0.58)
      })
    }

    const resize = () => {
      if (!renderer) return
      resolution.set(window.innerWidth, window.innerHeight)
      renderer.setSize(window.innerWidth, window.innerHeight, false)
      layers.forEach((layer) => {
        layer.material.uniforms.uResolution.value.copy(resolution)
        layer.material.uniforms.uPixelRatio.value = currentDpr
      })
    }

    const pointerMove = (event: PointerEvent) => {
      if (profile.mobile || motionPreference.matches || qualityLevel > 0) return
      mouseTarget.set(
        event.clientX / Math.max(1, window.innerWidth) * 2 - 1,
        -(event.clientY / Math.max(1, window.innerHeight) * 2 - 1),
      )
      lastPointerMove = performance.now()
    }

    const renderFrame = (timestamp: number) => {
      if (destroyed || document.hidden || !renderer) return
      const delta = Math.min(0.05, Math.max(0, (timestamp - lastTimestamp) / 1000))
      lastTimestamp = timestamp
      elapsedTime += delta * (motionPreference.matches ? 0.18 : 1)
      playbackMix += ((isPlayingRef.current ? 1 : 0) - playbackMix) * 0.035

      if (timestamp - lastPointerMove > 1150) mouseTarget.multiplyScalar(0.982)
      mouseCurrent.x += (mouseTarget.x - mouseCurrent.x) * 0.04
      mouseCurrent.y += (mouseTarget.y - mouseCurrent.y) * 0.04

      const repulsionEnabled = !profile.mobile && !motionPreference.matches && qualityLevel === 0
      layers.forEach((layer) => {
        layer.material.uniforms.uTime.value = elapsedTime
        layer.material.uniforms.uPlayback.value = playbackMix
        layer.material.uniforms.uMouse.value.copy(mouseCurrent)
        layer.material.uniforms.uFlowStrength.value = layer.definition.flowStrength
          * (1 + playbackMix * 0.08)
        layer.material.uniforms.uRepulsionStrength.value = repulsionEnabled
          ? layer.definition.repulsionStrength
          : 0
      })

      renderer.render(scene, camera)
      benchmarkFrames += 1
      const benchmarkDuration = timestamp - benchmarkStarted
      if (benchmarkDuration >= 4200) {
        const averageFps = benchmarkFrames * 1000 / benchmarkDuration
        if (averageFps < 43 && qualityLevel < 2) updateQuality(qualityLevel + 1)
        benchmarkStarted = timestamp
        benchmarkFrames = 0
      }

      animationFrame = window.requestAnimationFrame(renderFrame)
    }

    const visibilityChange = () => {
      if (document.hidden) {
        window.cancelAnimationFrame(animationFrame)
        animationFrame = 0
        return
      }
      lastTimestamp = performance.now()
      benchmarkStarted = lastTimestamp
      benchmarkFrames = 0
      if (!animationFrame) animationFrame = window.requestAnimationFrame(renderFrame)
    }

    const motionPreferenceChange = () => {
      mouseTarget.set(0, 0)
      mouseCurrent.set(0, 0)
    }

    const contextLost = () => container.classList.add('is-fallback')
    const contextRestored = () => container.classList.remove('is-fallback')

    try {
      renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: false,
        depth: false,
        powerPreference: 'high-performance',
        stencil: false,
      })
      renderer.setClearColor(0x000000, 0)
      renderer.outputColorSpace = THREE.SRGBColorSpace
      renderer.domElement.className = 'interactive-particle-background__canvas'
      renderer.domElement.dataset.particleCanvas = 'global'
      renderer.domElement.setAttribute('aria-hidden', 'true')
      container.classList.remove('is-fallback')
      container.appendChild(renderer.domElement)

      layers = layerDefinitions.map((definition, index) => (
        createLayer(definition, 9301 + index * 7919, resolution)
      ))
      layers.forEach((layer) => scene.add(layer.points))
      updateQuality(0)
      resize()

      window.addEventListener('resize', resize, { passive: true })
      window.addEventListener('pointermove', pointerMove, { passive: true })
      document.addEventListener('visibilitychange', visibilityChange)
      motionPreference.addEventListener('change', motionPreferenceChange)
      renderer.domElement.addEventListener('webglcontextlost', contextLost)
      renderer.domElement.addEventListener('webglcontextrestored', contextRestored)
      animationFrame = window.requestAnimationFrame(renderFrame)
    } catch {
      layers.forEach((layer) => {
        scene.remove(layer.points)
        layer.geometry.dispose()
        layer.material.dispose()
      })
      if (renderer) {
        renderer.dispose()
        if (renderer.domElement.parentElement === container) {
          container.removeChild(renderer.domElement)
        }
      }
      container.classList.add('is-fallback')
      return
    }

    const activeRenderer = renderer

    return () => {
      destroyed = true
      window.cancelAnimationFrame(animationFrame)
      window.removeEventListener('resize', resize)
      window.removeEventListener('pointermove', pointerMove)
      document.removeEventListener('visibilitychange', visibilityChange)
      motionPreference.removeEventListener('change', motionPreferenceChange)
      activeRenderer.domElement.removeEventListener('webglcontextlost', contextLost)
      activeRenderer.domElement.removeEventListener('webglcontextrestored', contextRestored)
      layers.forEach((layer) => {
        scene.remove(layer.points)
        layer.geometry.dispose()
        layer.material.dispose()
      })
      activeRenderer.dispose()
      activeRenderer.forceContextLoss()
      if (activeRenderer.domElement.parentElement === container) {
        container.removeChild(activeRenderer.domElement)
      }
    }
  }, [])

  return (
    <div
      ref={containerRef}
      className="interactive-particle-background"
      aria-hidden="true"
    />
  )
}

export default InteractiveParticleBackground
