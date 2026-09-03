import { Check, ListMusic, LoaderCircle, LockKeyhole, Music2, Plus, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BackendApiError } from '../../api/client'
import NeteaseImage from '../music/NeteaseImage'
import { addSongToPlaylist, getSelectablePlaylists } from '../../services/playlist'
import type { SelectablePlaylist } from '../../services/playlist'
import { usePlayerStore } from '../../store/playerStore'
import GlassButton from './GlassButton'
import GlassPanel from './GlassPanel'

type ModalStatus = 'loading' | 'ready' | 'adding' | 'success' | 'duplicate' | 'forbidden' | 'error'

function AddToPlaylistModalContent() {
  const currentSong = usePlayerStore((state) => state.currentSong)
  const closeModal = usePlayerStore((state) => state.closeAddToPlaylist)
  const [playlists, setPlaylists] = useState<SelectablePlaylist[]>([])
  const [status, setStatus] = useState<ModalStatus>('loading')
  const [activePlaylistId, setActivePlaylistId] = useState<number | null>(null)
  const [message, setMessage] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    getSelectablePlaylists(controller.signal)
      .then((nextPlaylists) => {
        setPlaylists(nextPlaylists)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setStatus('error')
        setMessage(
          error instanceof BackendApiError && error.status === 401
            ? '请先登录网易云音乐后再添加歌曲。'
            : error instanceof Error ? error.message : '歌单加载失败',
        )
      })

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeModal()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      controller.abort()
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [closeModal])

  const handleAdd = async (playlist: SelectablePlaylist) => {
    if (!currentSong || !playlist.editable || status === 'adding') return
    setStatus('adding')
    setActivePlaylistId(playlist.id)
    setMessage('')

    try {
      const result = await addSongToPlaylist(playlist.id, currentSong.id)
      setStatus(result.status === 'added' ? 'success' : result.status)
      setMessage(result.message)

      if (result.status === 'added') {
        setPlaylists((items) => items.map((item) => (
          item.id === playlist.id ? { ...item, trackCount: item.trackCount + 1 } : item
        )))
        window.setTimeout(closeModal, 900)
      }
    } catch (error) {
      setStatus('error')
      setMessage(error instanceof Error ? error.message : '添加歌曲失败')
    }
  }

  const statusIcon = status === 'success'
    ? <Check size={17} aria-hidden="true" />
    : status === 'forbidden'
      ? <LockKeyhole size={17} aria-hidden="true" />
      : status === 'adding'
        ? <LoaderCircle className="player-controls__loader" size={17} aria-hidden="true" />
        : <Plus size={17} aria-hidden="true" />

  return (
    <div className="playlist-modal-layer">
      <button className="player-overlay-backdrop" type="button" aria-label="关闭添加到歌单弹窗" onClick={closeModal} />
      <GlassPanel surface="overlay" className="playlist-modal" role="dialog" aria-modal="true" aria-labelledby="playlist-modal-title">
        <header className="playlist-modal__header">
          <div>
            <p>ADD TO PLAYLIST</p>
            <h2 id="playlist-modal-title">添加到歌单</h2>
            <span>{currentSong?.name ?? '当前歌曲'}</span>
          </div>
          <GlassButton tooltip="关闭弹窗" aria-label="关闭添加到歌单弹窗" onClick={closeModal}>
            <X size={18} aria-hidden="true" />
          </GlassButton>
        </header>

        {status === 'loading' ? (
          <div className="playlist-modal__state" aria-live="polite">
            <LoaderCircle className="player-controls__loader" size={25} aria-hidden="true" />
            <p>正在同步你的歌单…</p>
          </div>
        ) : playlists.length === 0 && status !== 'error' ? (
          <div className="playlist-modal__state">
            <ListMusic size={28} strokeWidth={1.2} aria-hidden="true" />
            <h3>还没有可用歌单</h3>
          </div>
        ) : (
          <div className="playlist-modal__list">
            {playlists.map((playlist) => {
              const adding = status === 'adding' && activePlaylistId === playlist.id
              return (
                <button
                  className="playlist-choice"
                  type="button"
                  key={playlist.id}
                  disabled={!playlist.editable || status === 'adding' || status === 'success'}
                  onClick={() => void handleAdd(playlist)}
                >
                  <span className="playlist-choice__cover">
                    <NeteaseImage
                      source={playlist.coverImgUrl}
                      size={128}
                      alt=""
                      fallback={<Music2 size={18} strokeWidth={1.3} aria-hidden="true" />}
                    />
                  </span>
                  <span className="playlist-choice__copy">
                    <strong>{playlist.name}</strong>
                    <small>{playlist.trackCount} 首歌曲</small>
                  </span>
                  <span className="playlist-choice__action">
                    {!playlist.editable
                      ? <><LockKeyhole size={14} aria-hidden="true" />不可编辑</>
                      : adding
                        ? <><LoaderCircle className="player-controls__loader" size={14} aria-hidden="true" />添加中</>
                        : <><Plus size={14} aria-hidden="true" />添加</>}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {(message || status === 'error') && (
          <div className={`playlist-modal__message is-${status}`} role="status">
            {statusIcon}
            <span>{message || '歌单请求失败，请稍后重试。'}</span>
          </div>
        )}
      </GlassPanel>
    </div>
  )
}

function AddToPlaylistModal() {
  const isOpen = usePlayerStore((state) => state.isAddToPlaylistOpen)
  return isOpen ? <AddToPlaylistModalContent /> : null
}

export default AddToPlaylistModal
