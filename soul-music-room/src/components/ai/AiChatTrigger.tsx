import { Sparkles, X } from 'lucide-react'

interface AiChatTriggerProps {
  isOpen: boolean
  onToggle: () => void
}

function AiChatTrigger({ isOpen, onToggle }: AiChatTriggerProps) {
  return (
    <button
      className={`ai-chat-trigger${isOpen ? ' is-open' : ''}`}
      type="button"
      aria-label={isOpen ? '收起 MoodFlow AI' : '打开 MoodFlow AI'}
      aria-pressed={isOpen}
      onClick={onToggle}
    >
      <span className="ai-chat-trigger__glow" aria-hidden="true" />
      {isOpen ? <X size={19} strokeWidth={1.65} /> : <Sparkles size={20} strokeWidth={1.55} />}
      <span className="ai-chat-trigger__label">AI</span>
    </button>
  )
}

export default AiChatTrigger
