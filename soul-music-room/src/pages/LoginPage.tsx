import { useCallback, useRef, useState } from 'react'
import { Check, LoaderCircle, LogOut, UserRound } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import LiquidGlassCursor from '../components/LiquidGlassCursor'
import LiquidGlassSurface from '../components/liquid-glass/LiquidGlassSurface'
import FlowingWelcome from '../components/login/FlowingWelcome'
import LoginModal from '../components/login/LoginModal'
import { useAuthStore } from '../store/authStore'
import './LoginPage.css'

function LoginPage() {
  const navigate = useNavigate()
  const pageRef = useRef<HTMLElement>(null!)
  const [modalOpen, setModalOpen] = useState(false)
  const [logoutError, setLogoutError] = useState('')
  const loggedIn = useAuthStore((state) => state.loggedIn)
  const initialized = useAuthStore((state) => state.initialized)
  const checking = useAuthStore((state) => state.checking)
  const logout = useAuthStore((state) => state.logout)

  const closeModal = useCallback(() => setModalOpen(false), [])

  const handleLoginSuccess = useCallback(() => {
    setModalOpen(false)
    navigate('/library', { replace: true })
  }, [navigate])

  const handleLogout = async () => {
    setLogoutError('')
    try {
      await logout()
    } catch {
      setLogoutError('退出登录失败，请稍后重试')
    }
  }

  return (
    <main className="login-page" ref={pageRef}>
      <div className="login-page__ocean-background" aria-hidden="true" />
      <div className="login-page__ambient login-page__ambient--one" aria-hidden="true" />
      <div className="login-page__ambient login-page__ambient--two" aria-hidden="true" />

      <LiquidGlassCursor
        eventSource={pageRef}
        backgroundSelector=".login-page__ocean-background"
      />

      <LiquidGlassSurface className="login-page__account" preset="control" radius={999} intensity={0.7}>
        {!initialized || checking ? (
          <span className="login-page__account-checking" aria-live="polite">
            <LoaderCircle size={16} aria-hidden="true" />
            正在连接
          </span>
        ) : loggedIn ? (
          <>
            <span className="login-page__account-status">
              <Check size={15} strokeWidth={1.8} aria-hidden="true" />
              已登录
            </span>
            <button type="button" onClick={handleLogout}>
              <LogOut size={16} strokeWidth={1.6} aria-hidden="true" />
              退出登录
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setModalOpen(true)}>
            <UserRound size={17} strokeWidth={1.6} aria-hidden="true" />
            登录
          </button>
        )}
        {logoutError && <small role="alert">{logoutError}</small>}
      </LiquidGlassSurface>

      <section className="login-page__center" aria-labelledby="login-page-title">
        <p className="login-page__eyebrow">YOUR MUSIC, IN SYNC</p>
        <h1 id="login-page-title">MoodFlow</h1>
        <FlowingWelcome />
      </section>

      <LoginModal
        open={modalOpen}
        onClose={closeModal}
        onLoginSuccess={handleLoginSuccess}
      />
    </main>
  )
}

export default LoginPage
