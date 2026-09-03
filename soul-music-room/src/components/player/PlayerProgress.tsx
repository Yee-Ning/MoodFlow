import { memo, useCallback, useEffect, useRef } from 'react'
import type { FormEvent, PointerEvent as ReactPointerEvent } from 'react'
import { usePlayerStore } from '../../store/playerStore'

function formatTime(time: number) {
  if (!Number.isFinite(time) || time < 0) return '0:00'
  const totalSeconds = Math.floor(time)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }

  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function PlayerProgress() {
  const songId = usePlayerStore((state) => state.currentSong?.id)
  const duration = usePlayerStore((state) => state.duration)
  const seek = usePlayerStore((state) => state.seek)
  const inputRef = useRef<HTMLInputElement>(null)
  const currentTimeRef = useRef<HTMLTimeElement>(null)
  const draggingRef = useRef(false)
  const frameRef = useRef<number | null>(null)
  const latestTimeRef = useRef(0)
  const safeDuration = Math.max(duration, 0)
  const disabled = !songId || safeDuration <= 0

  const updateVisual = useCallback((time: number) => {
    const displayedTime = Math.min(Math.max(time, 0), safeDuration || 0)
    const progress = safeDuration > 0 ? (displayedTime / safeDuration) * 100 : 0
    const formattedTime = formatTime(displayedTime)

    if (inputRef.current) {
      inputRef.current.value = String(displayedTime)
      inputRef.current.style.setProperty('--player-range-progress', `${progress}%`)
      inputRef.current.style.setProperty('--range-progress', `${progress}%`)
      inputRef.current.setAttribute(
        'aria-valuetext',
        `${formattedTime} / ${formatTime(safeDuration)}`,
      )
    }

    if (currentTimeRef.current) {
      currentTimeRef.current.textContent = formattedTime
      currentTimeRef.current.dateTime = `PT${Math.floor(displayedTime)}S`
    }
  }, [safeDuration])

  useEffect(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }

    draggingRef.current = false
    latestTimeRef.current = usePlayerStore.getState().currentTime
    updateVisual(latestTimeRef.current)

    const unsubscribe = usePlayerStore.subscribe((state, previousState) => {
      if (state.currentTime === previousState.currentTime || draggingRef.current) return
      latestTimeRef.current = state.currentTime
      if (frameRef.current !== null) return

      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null
        updateVisual(latestTimeRef.current)
      })
    })

    return () => {
      unsubscribe()
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current)
        frameRef.current = null
      }
    }
  }, [songId, updateVisual])

  const finishDrag = (value: number) => {
    if (!draggingRef.current) return
    draggingRef.current = false
    seek(value)
    updateVisual(value)
  }

  const handlePointerDown = (event: ReactPointerEvent<HTMLInputElement>) => {
    if (disabled) return
    draggingRef.current = true
    event.currentTarget.setPointerCapture(event.pointerId)
    updateVisual(Number(event.currentTarget.value))
  }

  const handleInput = (event: FormEvent<HTMLInputElement>) => {
    updateVisual(Number(event.currentTarget.value))
  }

  return (
    <div className="player-progress">
      <input
        ref={inputRef}
        className="player-range player-progress__range"
        type="range"
        min="0"
        max={safeDuration || 0}
        step="0.1"
        defaultValue="0"
        disabled={disabled}
        onPointerDown={handlePointerDown}
        onPointerUp={(event) => finishDrag(Number(event.currentTarget.value))}
        onPointerCancel={(event) => finishDrag(Number(event.currentTarget.value))}
        onBlur={(event) => finishDrag(Number(event.currentTarget.value))}
        onInput={handleInput}
        onChange={(event) => {
          if (!draggingRef.current) seek(Number(event.currentTarget.value))
        }}
        aria-label="播放进度"
        aria-valuetext={`0:00 / ${formatTime(safeDuration)}`}
      />
      <span className="player-progress__time-display">
        <time ref={currentTimeRef} dateTime="PT0S">0:00</time>
        <span aria-hidden="true">/</span>
        <time dateTime={`PT${Math.floor(safeDuration)}S`}>{formatTime(safeDuration)}</time>
      </span>
    </div>
  )
}

export default memo(PlayerProgress)
