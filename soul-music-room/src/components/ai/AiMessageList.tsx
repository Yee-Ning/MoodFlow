import { useEffect, useRef } from 'react'
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
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, isSending, error])

  return (
    <div className="ai-message-list" aria-live="polite">
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
          <div ref={endRef} />
        </div>
      )}
    </div>
  )
}

export default AiMessageList

