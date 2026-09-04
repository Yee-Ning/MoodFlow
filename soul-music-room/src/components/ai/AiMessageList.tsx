import { useLayoutEffect, useRef } from 'react'
import type { AiChatMessage, AiChatStatus } from '../../types/aiChat'
import AiMessage from './AiMessage'
import AiThinking from './AiThinking'
import AiWelcome from './AiWelcome'

interface AiMessageListProps {
  messages: AiChatMessage[]
  status: AiChatStatus
  isSending: boolean
  error: string
  onSuggestion: (suggestion: string) => void
  onRetry: () => void
}

function AiMessageList({
  messages,
  status,
  isSending,
  error,
  onSuggestion,
  onRetry,
}: AiMessageListProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const pinnedToBottomRef = useRef(true)
  const previousMessageCountRef = useRef(messages.length)

  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return

    const hasNewExchange = messages.length > previousMessageCountRef.current
      && messages.at(-2)?.role === 'user'
    if (hasNewExchange) pinnedToBottomRef.current = true
    if (pinnedToBottomRef.current) list.scrollTop = list.scrollHeight
    previousMessageCountRef.current = messages.length
  }, [messages, isSending, error])

  const handleScroll = () => {
    const list = listRef.current
    if (!list) return
    const distanceToBottom = list.scrollHeight - list.scrollTop - list.clientHeight
    pinnedToBottomRef.current = distanceToBottom < 72
  }

  return (
    <div
      ref={listRef}
      className="ai-message-list"
      role="log"
      aria-label="聊天记录"
      aria-live="polite"
      tabIndex={0}
      onScroll={handleScroll}
    >
      {messages.length === 0 && !isSending ? (
        <AiWelcome onSelect={onSuggestion} />
      ) : (
        <div className="ai-message-list__thread">
          {messages.map((message) => <AiMessage key={message.id} message={message} />)}
          {isSending && <AiThinking status={status} />}
          {error && (
            <div className="ai-chat-error" role="alert">
              <span>{error}</span>
              <button type="button" onClick={onRetry}>再试一次</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default AiMessageList
