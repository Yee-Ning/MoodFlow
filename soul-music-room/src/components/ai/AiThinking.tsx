import { Sparkles } from 'lucide-react'
import type { AiChatStatus } from '../../types/aiChat'

interface AiThinkingProps {
  status: AiChatStatus
}

function AiThinking({ status }: AiThinkingProps) {
  return (
    <div className="ai-thinking" role="status" aria-label={status === 'sending' ? '正在发送' : 'MoodFlow AI 正在思考'}>
      <Sparkles size={13} strokeWidth={1.45} aria-hidden="true" />
      <span className="ai-thinking__dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  )
}

export default AiThinking

