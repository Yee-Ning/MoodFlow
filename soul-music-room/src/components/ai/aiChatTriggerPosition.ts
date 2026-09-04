export interface AiChatTriggerPosition {
  x: number
  y: number
}

interface TriggerBounds {
  viewportWidth: number
  viewportHeight: number
  triggerWidth: number
  triggerHeight: number
  edgeGap: number
}

export const AI_CHAT_TRIGGER_POSITION_KEY = 'moodflow.ai-chat-trigger-position.v1'
export const AI_CHAT_TRIGGER_DRAG_THRESHOLD = 5

export function clampAiChatTriggerPosition(
  position: AiChatTriggerPosition,
  bounds: TriggerBounds,
): AiChatTriggerPosition {
  const maxX = Math.max(bounds.edgeGap, bounds.viewportWidth - bounds.triggerWidth - bounds.edgeGap)
  const maxY = Math.max(bounds.edgeGap, bounds.viewportHeight - bounds.triggerHeight - bounds.edgeGap)

  return {
    x: Math.min(Math.max(position.x, bounds.edgeGap), maxX),
    y: Math.min(Math.max(position.y, bounds.edgeGap), maxY),
  }
}

export function hasExceededDragThreshold(deltaX: number, deltaY: number) {
  return Math.hypot(deltaX, deltaY) >= AI_CHAT_TRIGGER_DRAG_THRESHOLD
}

export function parseAiChatTriggerPosition(value: string | null): AiChatTriggerPosition | null {
  if (!value) return null

  try {
    const parsed = JSON.parse(value) as Partial<AiChatTriggerPosition>
    if (!Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) return null
    return { x: Number(parsed.x), y: Number(parsed.y) }
  } catch {
    return null
  }
}
