export interface AiChatTriggerPosition {
  x: number
  y: number
}

export interface AiChatTriggerAnchor {
  x: number
  y: number
}

interface ViewportSize {
  width: number
  height: number
}

export interface AiChatPanelPlacement {
  left: number
  top: number
  width: number
  height: number
  originX: number
  originY: number
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

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), maximum)
}

export function clampAiChatTriggerPosition(
  position: AiChatTriggerPosition,
  bounds: TriggerBounds,
): AiChatTriggerPosition {
  const maxX = Math.max(bounds.edgeGap, bounds.viewportWidth - bounds.triggerWidth - bounds.edgeGap)
  const maxY = Math.max(bounds.edgeGap, bounds.viewportHeight - bounds.triggerHeight - bounds.edgeGap)

  return {
    x: clamp(position.x, bounds.edgeGap, maxX),
    y: clamp(position.y, bounds.edgeGap, maxY),
  }
}

export function calculateAiChatPanelPlacement(
  anchor: AiChatTriggerAnchor,
  viewport: ViewportSize,
): AiChatPanelPlacement {
  const isPhone = viewport.width <= 430
  const isCompact = viewport.width <= 840
  const edgeGap = isPhone ? 0 : isCompact ? 8 : 16
  const availableWidth = Math.max(0, viewport.width - edgeGap * 2)
  const availableHeight = Math.max(0, viewport.height - edgeGap * 2)
  const width = isCompact ? availableWidth : Math.min(422, availableWidth)
  const height = isCompact ? availableHeight : Math.min(760, availableHeight)
  const maxLeft = Math.max(edgeGap, viewport.width - width - edgeGap)
  const maxTop = Math.max(edgeGap, viewport.height - height - edgeGap)
  const left = isCompact ? edgeGap : clamp(anchor.x - width / 2, edgeGap, maxLeft)
  const top = isCompact ? edgeGap : clamp(anchor.y - height / 2, edgeGap, maxTop)

  return {
    left,
    top,
    width,
    height,
    originX: clamp(anchor.x - left, 0, width),
    originY: clamp(anchor.y - top, 0, height),
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
