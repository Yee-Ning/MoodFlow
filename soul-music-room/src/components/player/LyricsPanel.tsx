import { AudioLines, LoaderCircle, Music2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { usePlayerStore } from '../../store/playerStore'
import GlassButton from './GlassButton'
import GlassPanel from './GlassPanel'

const AUTO_SCROLL_RESUME_MS = 3600

interface LyricsPanelProps {
  presentation?: 'stage' | 'overlay'
}

function LyricsPanel({ presentation = 'overlay' }: LyricsPanelProps) {
  const isOpen = usePlayerStore((state) => state.isLyricsOpen)
  const currentSong = usePlayerStore((state) => state.currentSong)
  const lyrics = usePlayerStore((state) => state.lyrics)
  const activeLyricIndex = usePlayerStore((state) => state.activeLyricIndex)
  const lyricsStatus = usePlayerStore((state) => state.lyricsStatus)
  const lyricsError = usePlayerStore((state) => state.lyricsError)
  const closeLyrics = usePlayerStore((state) => state.closeLyrics)
  const seek = usePlayerStore((state) => state.seek)
  const lineRefs = useRef<Array<HTMLButtonElement | null>>([])
  const resumeTimerRef = useRef<number | null>(null)
  const programmaticScrollRef = useRef(false)
  const manualScrollUntilRef = useRef(0)
  const [resumeKey, setResumeKey] = useState(0)
  const isStage = presentation === 'stage'

  useEffect(() => () => {
    if (resumeTimerRef.current) window.clearTimeout(resumeTimerRef.current)
  }, [])

  useEffect(() => {
    if (isStage || !isOpen || activeLyricIndex < 0 || Date.now() < manualScrollUntilRef.current) return
    const activeLine = lineRefs.current[activeLyricIndex]
    if (!activeLine) return

    programmaticScrollRef.current = true
    activeLine.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => {
      programmaticScrollRef.current = false
    }, 420)
  }, [activeLyricIndex, isOpen, isStage, resumeKey])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeLyrics()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [closeLyrics, isOpen])

  if (!isOpen) return null

  const stageStartIndex = Math.min(
    Math.max(0, activeLyricIndex >= 0 ? activeLyricIndex - 2 : 0),
    Math.max(0, lyrics.length - 5),
  )
  const displayedLyrics = isStage
    ? lyrics.slice(stageStartIndex, stageStartIndex + 5).map((line, index) => ({
        line,
        index: stageStartIndex + index,
      }))
    : lyrics.map((line, index) => ({ line, index }))

  const handleManualScroll = () => {
    if (programmaticScrollRef.current) return
    manualScrollUntilRef.current = Date.now() + AUTO_SCROLL_RESUME_MS
    if (resumeTimerRef.current) window.clearTimeout(resumeTimerRef.current)
    resumeTimerRef.current = window.setTimeout(() => {
      manualScrollUntilRef.current = 0
      setResumeKey((key) => key + 1)
    }, AUTO_SCROLL_RESUME_MS)
  }

  return (
    <GlassPanel
      surface={isStage ? 'default' : 'overlay'}
      className={`lyrics-panel lyrics-panel--${presentation}`}
      role={isStage ? 'region' : 'dialog'}
      aria-modal={isStage ? undefined : true}
      aria-label={isStage ? '沉浸歌词舞台' : '歌词面板'}
    >
      <header className="lyrics-panel__header">
        <div>
          <p>LYRICS</p>
          <strong>{currentSong?.name ?? '歌词'}</strong>
          <span>{currentSong?.artists.join('、') || '等待歌曲播放'}</span>
        </div>
        <GlassButton tooltip="关闭歌词" aria-label="关闭歌词" onClick={closeLyrics}>
          <X size={18} aria-hidden="true" />
        </GlassButton>
      </header>

      <div className="lyrics-panel__viewport" onScroll={isStage ? undefined : handleManualScroll}>
        {lyricsStatus === 'loading' && (
          <div className="lyrics-panel__state" aria-live="polite">
            <LoaderCircle className="player-controls__loader" size={24} aria-hidden="true" />
            <p>正在拾取歌词时间轴…</p>
          </div>
        )}
        {lyricsStatus === 'instrumental' && (
          <div className="lyrics-panel__state">
            <Music2 size={28} strokeWidth={1.2} aria-hidden="true" />
            <h3>纯音乐，请欣赏</h3>
            <p>这一段声音没有文字，也不需要文字。</p>
          </div>
        )}
        {lyricsStatus === 'empty' && (
          <div className="lyrics-panel__state">
            <AudioLines size={28} strokeWidth={1.2} aria-hidden="true" />
            <h3>暂时没有歌词</h3>
            <p>歌曲仍会继续播放。</p>
          </div>
        )}
        {lyricsStatus === 'error' && (
          <div className="lyrics-panel__state" role="status">
            <AudioLines size={28} strokeWidth={1.2} aria-hidden="true" />
            <h3>歌词没有抵达</h3>
            <p>{lyricsError || '稍后切换歌曲或重新打开歌词即可重试。'}</p>
          </div>
        )}
        {lyricsStatus === 'idle' && !currentSong && (
          <div className="lyrics-panel__state">
            <Music2 size={28} strokeWidth={1.2} aria-hidden="true" />
            <h3>先选择一首歌曲</h3>
          </div>
        )}
        {lyricsStatus === 'ready' && (
          <div className="lyrics-panel__lines">
            {displayedLyrics.map(({ line, index }) => (
              <button
                ref={isStage ? undefined : (element) => { lineRefs.current[index] = element }}
                className={`lyric-line${index === activeLyricIndex ? ' is-active' : ''}${index < activeLyricIndex ? ' is-past' : ''}${index > activeLyricIndex ? ' is-next' : ''}`}
                type="button"
                key={`${line.time}-${index}`}
                aria-current={index === activeLyricIndex ? 'true' : undefined}
                onClick={() => seek(line.time)}
              >
                <span>{line.text}</span>
                {line.translation && <small>{line.translation}</small>}
              </button>
            ))}
          </div>
        )}
      </div>
    </GlassPanel>
  )
}

export default LyricsPanel
