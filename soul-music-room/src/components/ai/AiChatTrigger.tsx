import { Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from 'react'
import {
  AI_CHAT_TRIGGER_POSITION_KEY,
  clampAiChatTriggerPosition,
  hasExceededDragThreshold,
  parseAiChatTriggerPosition,
  type AiChatTriggerPosition,
} from './aiChatTriggerPosition'

interface AiChatTriggerProps {
  isOpen: boolean
  onToggle: () => void
}

function AiChatTrigger({ isOpen, onToggle }: AiChatTriggerProps) {
  const triggerRef = useRef<HTMLButtonElement>(null)
  const dragRef = useRef<{
    pointerId: number
    startX: number
    startY: number
    originX: number
    originY: number
    moved: boolean
  } | null>(null)
  const suppressClickRef = useRef(false)
  const [position, setPosition] = useState<AiChatTriggerPosition | null>(() => {
    if (typeof window === 'undefined') return null
    try {
      const stored = parseAiChatTriggerPosition(
        window.localStorage.getItem(AI_CHAT_TRIGGER_POSITION_KEY),
      )
      if (!stored) return null
      const triggerSize = window.matchMedia('(max-width: 840px)').matches ? 46 : 52
      return clampAiChatTriggerPosition(stored, {
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        triggerWidth: triggerSize,
        triggerHeight: triggerSize,
        edgeGap: 10,
      })
    } catch {
      return null
    }
  })
  const positionRef = useRef(position)
  const [isDragging, setIsDragging] = useState(false)

  const updatePosition = useCallback((nextPosition: AiChatTriggerPosition) => {
    positionRef.current = nextPosition
    setPosition(nextPosition)
  }, [])

  const constrainToViewport = useCallback((nextPosition: AiChatTriggerPosition) => {
    const trigger = triggerRef.current
    return clampAiChatTriggerPosition(nextPosition, {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      triggerWidth: trigger?.offsetWidth ?? 52,
      triggerHeight: trigger?.offsetHeight ?? 52,
      edgeGap: 10,
    })
  }, [])

  const persistPosition = useCallback((nextPosition: AiChatTriggerPosition | null) => {
    if (!nextPosition) return
    try {
      window.localStorage.setItem(AI_CHAT_TRIGGER_POSITION_KEY, JSON.stringify(nextPosition))
    } catch {
      // Dragging still works when storage is unavailable; only persistence is skipped.
    }
  }, [])

  useEffect(() => {
    const handleResize = () => {
      const currentPosition = positionRef.current
      if (!currentPosition) return
      const constrained = constrainToViewport(currentPosition)
      updatePosition(constrained)
      persistPosition(constrained)
    }

    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [constrainToViewport, persistPosition, updatePosition])

  const handlePointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary || event.button !== 0) return
    const rect = event.currentTarget.getBoundingClientRect()
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: rect.left,
      originY: rect.top,
      moved: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setIsDragging(true)
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return

    const deltaX = event.clientX - drag.startX
    const deltaY = event.clientY - drag.startY
    if (!drag.moved) drag.moved = hasExceededDragThreshold(deltaX, deltaY)
    if (!drag.moved) return

    event.preventDefault()
    updatePosition(constrainToViewport({
      x: drag.originX + deltaX,
      y: drag.originY + deltaY,
    }))
  }

  const finishPointerInteraction = (event: ReactPointerEvent<HTMLButtonElement>, cancelled = false) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragRef.current = null
    setIsDragging(false)

    if (drag.moved) {
      persistPosition(positionRef.current)
      suppressClickRef.current = !cancelled
    }
  }

  const handleClick = (event: ReactMouseEvent<HTMLButtonElement>) => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false
      event.preventDefault()
      return
    }
    onToggle()
  }

  return (
    <button
      ref={triggerRef}
      className={`ai-chat-trigger${isOpen ? ' is-open' : ''}${position ? ' is-positioned' : ''}${isDragging ? ' is-dragging' : ''}`}
      type="button"
      aria-label={`${isOpen ? '收起' : '打开'} MoodFlow AI，可拖动`}
      aria-pressed={isOpen}
      title={`拖动可调整位置，点击${isOpen ? '收起' : '打开'} AI`}
      style={position ? { left: position.x, top: position.y } : undefined}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerInteraction}
      onPointerCancel={(event) => finishPointerInteraction(event, true)}
      onClick={handleClick}
    >
      <span className="ai-chat-trigger__glow" aria-hidden="true" />
      {isOpen ? <X size={19} strokeWidth={1.65} /> : <Sparkles size={20} strokeWidth={1.55} />}
      <span className="ai-chat-trigger__label">AI</span>
    </button>
  )
}

export default AiChatTrigger
