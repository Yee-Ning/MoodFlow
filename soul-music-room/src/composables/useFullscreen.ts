import { useCallback, useEffect, useRef } from 'react'
import { usePlayerStore } from '../store/playerStore'

export function useFullscreen() {
  const isFullscreen = usePlayerStore((state) => state.isFullscreen)
  const setFullscreen = usePlayerStore((state) => state.setFullscreen)
  const nativeFullscreenRef = useRef(false)

  useEffect(() => {
    const handleFullscreenChange = () => {
      if (document.fullscreenElement) {
        nativeFullscreenRef.current = true
        setFullscreen(true)
      } else if (nativeFullscreenRef.current) {
        nativeFullscreenRef.current = false
        setFullscreen(false)
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isFullscreen && !document.fullscreenElement) {
        setFullscreen(false)
      }
    }

    document.addEventListener('fullscreenchange', handleFullscreenChange)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isFullscreen, setFullscreen])

  const enterFullscreen = useCallback(async () => {
    setFullscreen(true)
    if (!document.fullscreenEnabled || document.fullscreenElement) return

    nativeFullscreenRef.current = true
    try {
      await document.documentElement.requestFullscreen()
    } catch {
      nativeFullscreenRef.current = false
      // The fixed overlay remains active as a browser-compatible fallback.
    }
  }, [setFullscreen])

  const exitFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
    } finally {
      nativeFullscreenRef.current = false
      setFullscreen(false)
    }
  }, [setFullscreen])

  return { isFullscreen, enterFullscreen, exitFullscreen }
}
