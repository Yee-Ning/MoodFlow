import { ListMusic, Repeat1, Repeat2, Shuffle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { memo } from 'react'
import { usePlayerStore } from '../../store/playerStore'
import type { PlayMode } from '../../store/playerStore'
import GlassIconButton from './GlassIconButton'

const MODE_PRESENTATION: Record<PlayMode, { label: string; Icon: LucideIcon }> = {
  sequence: { label: '顺序播放', Icon: ListMusic },
  'list-loop': { label: '列表循环', Icon: Repeat2 },
  'single-loop': { label: '单曲循环', Icon: Repeat1 },
  shuffle: { label: '随机播放', Icon: Shuffle },
}

function PlayModeButton() {
  const playMode = usePlayerStore((state) => state.playMode)
  const queueLength = usePlayerStore((state) => state.queue.length)
  const togglePlayMode = usePlayerStore((state) => state.togglePlayMode)
  const { label, Icon } = MODE_PRESENTATION[playMode]

  return (
    <GlassIconButton
      className="play-mode-button"
      icon={<Icon size={18} strokeWidth={1.75} aria-hidden="true" />}
      label={`${label} · 点击切换`}
      active={playMode !== 'sequence'}
      aria-label={`当前播放模式：${label}，点击切换`}
      disabled={queueLength === 0}
      onClick={togglePlayMode}
    />
  )
}

export default memo(PlayModeButton)
