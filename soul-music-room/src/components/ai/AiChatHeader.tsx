import { RotateCcw, Sparkles, X } from 'lucide-react'

interface AiChatHeaderProps {
  resetDisabled: boolean
  onReset: () => void
  onClose: () => void
}

function AiChatHeader({ resetDisabled, onReset, onClose }: AiChatHeaderProps) {
  return (
    <header className="ai-chat-header">
      <div className="ai-chat-header__identity">
        <span className="ai-chat-header__mark" aria-hidden="true">
          <Sparkles size={17} strokeWidth={1.55} />
        </span>
        <span className="ai-chat-header__copy">
          <strong>MoodFlow AI</strong>
          <small><i aria-hidden="true" />你的音乐搭子</small>
        </span>
      </div>
      <div className="ai-chat-header__actions">
        <button
          className="ai-chat-header__close"
          type="button"
          aria-label="新建对话"
          title="新建对话"
          disabled={resetDisabled}
          onClick={onReset}
        >
          <RotateCcw size={17} strokeWidth={1.6} aria-hidden="true" />
        </button>
        <button className="ai-chat-header__close" type="button" aria-label="关闭 MoodFlow AI" onClick={onClose}>
          <X size={18} strokeWidth={1.6} aria-hidden="true" />
        </button>
      </div>
    </header>
  )
}

export default AiChatHeader
