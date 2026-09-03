import { Sparkles } from 'lucide-react'
import type { AiChatMessage } from '../../types/aiChat'
import AiSongCard from './AiSongCard'

interface AiMessageProps {
  message: AiChatMessage
}

function AiMessage({ message }: AiMessageProps) {
  const isAssistant = message.role === 'assistant'

  return (
    <article className={`ai-message ai-message--${message.role}`}>
      {isAssistant && (
        <span className="ai-message__mark" aria-hidden="true">
          <Sparkles size={12} strokeWidth={1.45} />
        </span>
      )}
      <div className="ai-message__content">
        <p>{message.content}</p>
        {message.recommendations?.length ? (
          <div className="ai-message__songs">
            {message.recommendations.map((recommendation) => (
              <AiSongCard key={recommendation.id} recommendation={recommendation} />
            ))}
          </div>
        ) : null}
      </div>
    </article>
  )
}

export default AiMessage

