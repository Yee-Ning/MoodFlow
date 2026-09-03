import { useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import NeteaseQrLogin from './NeteaseQrLogin'

interface LoginModalProps {
  open: boolean
  onClose: () => void
  onLoginSuccess: () => void
}

function LoginModal({ open, onClose, onLoginSuccess }: LoginModalProps) {
  useEffect(() => {
    if (!open) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, open])

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="login-modal"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) onClose()
          }}
        >
          <motion.section
            className="login-modal__panel"
            initial={{ opacity: 0, y: 18, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.28, ease: [0.22, 0.8, 0.24, 1] }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="netease-login-title"
          >
            <LiquidGlassSurface
              className="login-modal__liquid"
              preset="overlay"
              radius={32}
              intensity={0.88}
            >
              <span />
            </LiquidGlassSurface>
            <button
              className="login-modal__close"
              type="button"
              aria-label="关闭登录弹窗"
              onClick={onClose}
            >
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
            <NeteaseQrLogin onLoginSuccess={onLoginSuccess} />
          </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default LoginModal
