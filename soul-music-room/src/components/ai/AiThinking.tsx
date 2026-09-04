import { Sparkles } from 'lucide-react'
import type { AiChatStatus } from '../../types/aiChat'

interface AiThinkingProps {
  status: AiChatStatus
}

function AiThinking({ status }: AiThinkingProps) {
  const label = {
    sending: '正在发送',
    'loading-context': '正在读取当前歌曲',
    generating: '正在生成回答',
    'using-tools': '正在调用音乐服务',
    finalizing: '正在整理回答',
    idle: '即将完成',
    error: '暂时无法完成',
  }[status]

  return (
    <div className="ai-thinking" role="status">
      <Sparkles size={13} strokeWidth={1.45} aria-hidden="true" />
      <span>{label}</span>
      <span className="ai-thinking__dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  )
}

export default AiThinking
