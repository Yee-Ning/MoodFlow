import { Sparkles } from 'lucide-react'

const suggestions = [
  '根据现在这首歌推荐',
  '给我来点适合写代码的',
  '帮我生成一个深夜歌单',
  '换点符合我口味的',
]

interface AiWelcomeProps {
  onSelect: (suggestion: string) => void
}

function AiWelcome({ onSelect }: AiWelcomeProps) {
  return (
    <section className="ai-welcome">
      <span className="ai-welcome__spark" aria-hidden="true">
        <Sparkles size={22} strokeWidth={1.35} />
      </span>
      <div className="ai-welcome__copy">
        <p>MOODFLOW LISTENS</p>
        <h2>今天想听点什么？</h2>
        <span>告诉我此刻的心情、场景，或从正在播放的歌开始。</span>
      </div>
      <div className="ai-welcome__suggestions" aria-label="快捷建议">
        {suggestions.map((suggestion) => (
          <button type="button" key={suggestion} onClick={() => onSelect(suggestion)}>
            {suggestion}
          </button>
        ))}
      </div>
    </section>
  )
}

export default AiWelcome

