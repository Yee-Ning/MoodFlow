import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { useEffect, useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { useAiChatStore } from '../../store/aiChatStore'
import { usePlayerStore } from '../../store/playerStore'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import AiChatHeader from './AiChatHeader'
import AiChatInput from './AiChatInput'
import AiChatTrigger from './AiChatTrigger'
import AiMessageList from './AiMessageList'
import './ai-chat.css'

function AiChatPanel() {
  const location = useLocation()
  const reduceMotion = Boolean(useReducedMotion())
  const currentSongId = usePlayerStore((state) => state.currentSong?.id ?? null)
  const {
    messages,
    isOpen,
    isSending,
    error,
    status,
    toggleOpen,
    close,
    sendMessage,
    retryLastMessage,
  } = useAiChatStore()

  const context = useMemo(() => {
    const playlistMatch = location.pathname.match(/^\/playlist\/(\d+)/)
    return {
      current_song_id: currentSongId,
      current_playlist_id: playlistMatch ? Number(playlistMatch[1]) : null,
      page: location.pathname,
    }
  }, [currentSongId, location.pathname])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [close, isOpen])

  const panelTransition = reduceMotion
    ? { duration: 0.01 }
    : { type: 'spring' as const, stiffness: 330, damping: 32, mass: 0.9 }

  return (
    <>
      <AiChatTrigger isOpen={isOpen} onToggle={toggleOpen} />
      <AnimatePresence>
        {isOpen && (
          <>
            <motion.button
              className="ai-chat-backdrop"
              type="button"
              aria-label="关闭 MoodFlow AI"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduceMotion ? 0.01 : 0.24 }}
              onClick={close}
            />
            <motion.aside
              className="ai-chat-panel-wrap"
              role="dialog"
              aria-modal="true"
              aria-label="MoodFlow AI 音乐助手"
              initial={{ opacity: 0, x: 42, scale: 0.975 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 34, scale: 0.985 }}
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
                <AiChatHeader onClose={close} />
                <AiMessageList
                  messages={messages}
                  status={status}
                  isSending={isSending}
                  error={error}
                  onSuggestion={(suggestion) => void sendMessage(suggestion, context)}
                  onRetry={() => void retryLastMessage(context)}
                />
                <AiChatInput
                  disabled={isSending}
                  onSend={(message) => void sendMessage(message, context)}
                />
              </LiquidGlassSurface>
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  )
}

export default AiChatPanel

