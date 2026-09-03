import { AnimatePresence, motion } from 'framer-motion'
import { ListMusic, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { usePlayerStore } from '../../store/playerStore'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import QueueSongItem, { type MoveDirection } from './QueueSongItem'
import './play-queue-drawer.css'

const drawerTransition = {
  duration: 0.24,
  ease: [0.2, 0.76, 0.22, 1] as const,
}

function PlayQueueDrawerContent() {
  const queue = usePlayerStore((state) => state.queue)
  const currentIndex = usePlayerStore((state) => state.currentIndex)
  const currentSongId = usePlayerStore((state) => state.currentSong?.id ?? null)
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const isLoading = usePlayerStore((state) => state.isLoading)
  const closeQueue = usePlayerStore((state) => state.closeQueue)
  const clearQueueExceptCurrent = usePlayerStore((state) => state.clearQueueExceptCurrent)

  const [isConfirmingClear, setIsConfirmingClear] = useState(false)
  const listRef = useRef<HTMLOListElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const removableCount = Math.max(0, queue.length - (currentIndex >= 0 ? 1 : 0))

  const dismiss = useCallback(() => {
    setIsConfirmingClear(false)
    closeQueue()
  }, [closeQueue])

  useEffect(() => {
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus())

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.cancelAnimationFrame(focusFrame)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [dismiss])

  useEffect(() => {
    if (currentIndex < 0) return

    const frameId = window.requestAnimationFrame(() => {
      listRef.current
        ?.querySelector<HTMLElement>('[data-queue-current="true"]')
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })

    return () => window.cancelAnimationFrame(frameId)
  }, [currentIndex, currentSongId])

  const handlePlay = useCallback((songId: number) => {
    const state = usePlayerStore.getState()
    const index = state.queue.findIndex((song) => song.id === songId)
    if (index >= 0) void state.playQueueItem(index)
  }, [])

  const handleRemove = useCallback((songId: number) => {
    const state = usePlayerStore.getState()
    const index = state.queue.findIndex((song) => song.id === songId)
    if (index >= 0) void state.removeQueueItem(index)
  }, [])

  const handleMove = useCallback((songId: number, direction: MoveDirection) => {
    const state = usePlayerStore.getState()
    const fromIndex = state.queue.findIndex((song) => song.id === songId)
    if (fromIndex < 0) return

    const toIndex = direction === 'up' ? fromIndex - 1 : fromIndex + 1
    state.reorderQueue(fromIndex, toIndex)
  }, [])

  const confirmClear = () => {
    clearQueueExceptCurrent()
    setIsConfirmingClear(false)
  }

  return (
    <div className="play-queue-layer is-open">
      <motion.button
        className="play-queue-backdrop"
        type="button"
        aria-label="关闭当前播放队列"
        tabIndex={-1}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.22 }}
        onClick={dismiss}
      />

      <motion.aside
        className="play-queue-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="play-queue-title"
        initial={{
          opacity: 0,
          x: 'var(--queue-enter-x)',
          y: 'var(--queue-enter-y)',
          scale: 0.985,
        }}
        animate={{ opacity: 1, x: 0, y: 0, scale: 1 }}
        exit={{
          opacity: 0,
          x: 'var(--queue-enter-x)',
          y: 'var(--queue-enter-y)',
          scale: 0.985,
        }}
        transition={drawerTransition}
      >
        <LiquidGlassSurface
          className="play-queue-drawer__liquid"
          preset="overlay"
          radius={31}
          intensity={0.92}
        >
          <span />
        </LiquidGlassSurface>
        <span className="play-queue-drawer__handle" aria-hidden="true" />
        <span className="play-queue-drawer__edge" aria-hidden="true" />

        <header className="play-queue-drawer__header">
          <div className="play-queue-drawer__title">
            <span className="play-queue-drawer__eyebrow">PLAYING NEXT</span>
            <h2 id="play-queue-title">当前播放队列</h2>
            <p>{queue.length} 首歌曲{currentSongId ? ` · ${removableCount} 首可清除` : ''}</p>
          </div>

          <div className="play-queue-drawer__header-actions">
            {isConfirmingClear ? (
              <div className="play-queue-drawer__confirm" role="group" aria-label="确认清空队列">
                <button type="button" onClick={() => setIsConfirmingClear(false)}>取消</button>
                <button className="is-danger" type="button" onClick={confirmClear}>
                  确认清空
                </button>
              </div>
            ) : (
              <button
                className="play-queue-drawer__clear"
                type="button"
                disabled={removableCount === 0}
                onClick={() => setIsConfirmingClear(true)}
              >
                <Trash2 size={14} strokeWidth={1.7} aria-hidden="true" />
                清空
              </button>
            )}

            <button
              ref={closeButtonRef}
              className="play-queue-drawer__close"
              type="button"
              aria-label="关闭当前播放队列"
              onClick={dismiss}
            >
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="play-queue-drawer__rule" aria-hidden="true" />

        {queue.length > 0 ? (
          <ol ref={listRef} className="play-queue-list" aria-label="当前播放队列歌曲">
            {queue.map((song, index) => {
              const isCurrent = index === currentIndex && song.id === currentSongId
              return (
                <QueueSongItem
                  key={`${song.id}-${index}`}
                  song={song}
                  isCurrent={isCurrent}
                  isPlaying={isCurrent && isPlaying}
                  isLoading={isCurrent && isLoading}
                  canMoveUp={index > 0}
                  canMoveDown={index < queue.length - 1}
                  onPlay={handlePlay}
                  onRemove={handleRemove}
                  onMove={handleMove}
                />
              )
            })}
          </ol>
        ) : (
          <div className="play-queue-drawer__empty">
            <span><ListMusic size={28} strokeWidth={1.25} aria-hidden="true" /></span>
            <h3>队列还是空的</h3>
            <p>从歌单中选择歌曲后，它们会按播放顺序出现在这里。</p>
          </div>
        )}
      </motion.aside>
    </div>
  )
}

function PlayQueueDrawer() {
  const isOpen = usePlayerStore((state) => state.isQueueOpen)

  return (
    <AnimatePresence>
      {isOpen && <PlayQueueDrawerContent key="play-queue-drawer" />}
    </AnimatePresence>
  )
}

export default PlayQueueDrawer
