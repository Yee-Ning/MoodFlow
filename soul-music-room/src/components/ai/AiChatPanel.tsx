import { motion, useReducedMotion } from 'framer-motion'
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { useAiChatStore } from '../../store/aiChatStore'
import { createPlayerContextSnapshot } from '../../services/playerContext'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import AiChatHeader from './AiChatHeader'
import AiChatInput from './AiChatInput'
import AiChatTrigger from './AiChatTrigger'
import AiMessageList from './AiMessageList'
import {
  calculateAiChatPanelPlacement,
  type AiChatTriggerAnchor,
} from './aiChatTriggerPosition'
import './ai-chat.css'

function getViewportSize() {
  if (typeof window === 'undefined') return { width: 1440, height: 900 }
  return { width: window.innerWidth, height: window.innerHeight }
}

type AiChatPanelStyle = CSSProperties & {
  '--ai-chat-panel-left': string
  '--ai-chat-panel-top': string
}

function AiChatPanel() {
  const reduceMotion = Boolean(useReducedMotion())
  const {
    messages,
    isOpen,
    isSending,
    error,
    status,
    open,
    close,
    clearConversation,
    sendMessage,
    retryLastMessage,
  } = useAiChatStore()
  const [panelAnchor, setPanelAnchor] = useState<AiChatTriggerAnchor | null>(null)
  const [viewport, setViewport] = useState(getViewportSize)

  useEffect(() => {
    const handleResize = () => setViewport(getViewportSize())
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const handleOpen = useCallback((anchor: AiChatTriggerAnchor) => {
    setPanelAnchor(anchor)
    open()
  }, [open])

  const placement = useMemo(() => calculateAiChatPanelPlacement(
    panelAnchor ?? { x: viewport.width - 44, y: viewport.height / 2 },
    viewport,
  ), [panelAnchor, viewport])
  const panelStyle: AiChatPanelStyle = {
    '--ai-chat-panel-left': `${placement.left}px`,
    '--ai-chat-panel-top': `${placement.top}px`,
    transformOrigin: `${placement.originX}px ${placement.originY}px`,
  }

  const panelTransition = reduceMotion
    ? { duration: 0.01 }
    : { type: 'spring' as const, stiffness: 330, damping: 32, mass: 0.9 }

  return (
    <>
      {!isOpen && <AiChatTrigger onOpen={handleOpen} />}
      {isOpen && (
        <>
          <motion.div
            className="ai-chat-backdrop"
            aria-hidden="true"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: reduceMotion ? 0.01 : 0.24 }}
          />
          <motion.aside
            id="moodflow-ai-panel"
            className="ai-chat-panel-wrap"
            role="dialog"
            aria-modal="true"
            aria-label="MoodFlow AI 音乐助手"
            style={panelStyle}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={panelTransition}
          >
            <LiquidGlassSurface
              className="ai-chat-panel"
              preset="overlay"
              radius={34}
              intensity={0.92}
              interactive
              refract
            >
              <AiChatHeader
                resetDisabled={!isSending && messages.length === 0}
                onReset={clearConversation}
                onClose={close}
              />
              <AiMessageList
                messages={messages}
                status={status}
                isSending={isSending}
                error={error}
                onSuggestion={(suggestion) => void sendMessage(
                  suggestion,
                  createPlayerContextSnapshot(),
                )}
                onRetry={() => void retryLastMessage(createPlayerContextSnapshot())}
              />
              <AiChatInput
                disabled={isSending}
                onSend={(message) => void sendMessage(
                  message,
                  createPlayerContextSnapshot(),
                )}
              />
            </LiquidGlassSurface>
          </motion.aside>
        </>
      )}
    </>
  )
}

export default AiChatPanel
