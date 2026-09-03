import { ArrowUp, Paperclip } from 'lucide-react'
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from 'react'

interface AiChatInputProps {
  disabled: boolean
  onSend: (message: string) => void
}

function AiChatInput({ disabled, onSend }: AiChatInputProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const canSend = value.trim().length > 0 && !disabled

  useEffect(() => {
    const timer = window.setTimeout(() => textareaRef.current?.focus(), 320)
    return () => window.clearTimeout(timer)
  }, [])

  const resize = (element: HTMLTextAreaElement) => {
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 92)}px`
  }

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setValue(event.target.value)
    resize(event.target)
  }

  const submit = () => {
    if (!canSend) return
    onSend(value.trim())
    setValue('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className={`ai-chat-input${value ? ' has-value' : ''}`}>
      <button className="ai-chat-input__utility" type="button" aria-label="更多音乐功能" title="更多音乐功能">
        <Paperclip size={17} strokeWidth={1.55} aria-hidden="true" />
      </button>
      <textarea
        ref={textareaRef}
        rows={1}
        maxLength={4000}
        value={value}
        disabled={disabled}
        aria-label="给 MoodFlow AI 发消息"
        placeholder="问点音乐相关的…"
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
      <button
        className="ai-chat-input__send"
        type="button"
        aria-label="发送消息"
        disabled={!canSend}
        onClick={submit}
      >
        <ArrowUp size={18} strokeWidth={1.9} aria-hidden="true" />
      </button>
    </div>
  )
}

export default AiChatInput

