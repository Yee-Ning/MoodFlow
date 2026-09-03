import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check,
  LoaderCircle,
  QrCode,
  RefreshCw,
  ScanLine,
  Smartphone,
  TriangleAlert,
} from 'lucide-react'
import {
  AuthApiError,
  checkQrCode,
  createQrCode,
  getQrKey,
  type QrStatusCode,
} from '../../api/auth'
import { useAuthStore } from '../../store/authStore'

type ViewState =
  | 'loading'
  | 'waitingScan'
  | 'waitingConfirm'
  | 'success'
  | 'expired'
  | 'error'

const statusView: Record<
  QrStatusCode,
  { view: ViewState; message: string }
> = {
  800: { view: 'expired', message: '二维码已过期，请刷新二维码' },
  801: { view: 'waitingScan', message: '请使用网易云音乐 App 扫码' },
  802: { view: 'waitingConfirm', message: '扫码成功，请在手机上确认' },
  803: { view: 'success', message: '登录成功' },
}

interface NeteaseQrLoginProps {
  onLoginSuccess: () => void
}

function NeteaseQrLogin({ onLoginSuccess }: NeteaseQrLoginProps) {
  const [view, setView] = useState<ViewState>('loading')
  const [message, setMessage] = useState('正在生成专属二维码…')
  const [qrImg, setQrImg] = useState('')
  const [key, setKey] = useState('')
  const loadControllerRef = useRef<AbortController | null>(null)
  const refreshAuthStatus = useAuthStore((state) => state.refreshAuthStatus)

  const loadQrCode = useCallback(async () => {
    loadControllerRef.current?.abort()
    const controller = new AbortController()
    loadControllerRef.current = controller

    setView('loading')
    setMessage('正在生成专属二维码…')
    setQrImg('')
    setKey('')

    try {
      const keyData = await getQrKey(controller.signal)
      const qrData = await createQrCode(keyData.key, controller.signal)
      setQrImg(qrData.qrimg)
      setKey(keyData.key)
      setView('waitingScan')
      setMessage('请使用网易云音乐 App 扫码')
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setView('error')
      setMessage(
        error instanceof AuthApiError && error.serviceUnavailable
          ? '登录服务暂不可用'
          : '获取二维码失败，请重新加载',
      )
    }
  }, [])

  useEffect(() => {
    const startTimer = window.setTimeout(() => void loadQrCode(), 0)
    return () => {
      window.clearTimeout(startTimer)
      loadControllerRef.current?.abort()
    }
  }, [loadQrCode])

  useEffect(() => {
    if (!key) return

    let cancelled = false
    let pollTimer: number | undefined
    let closeTimer: number | undefined
    let pollController: AbortController | null = null

    const poll = async () => {
      pollController = new AbortController()
      try {
        const result = await checkQrCode(key, pollController.signal)
        if (cancelled) return

        const next = statusView[result.statusCode]
        setView(next.view)
        setMessage(next.message)

        if (result.statusCode === 803) {
          await refreshAuthStatus()
          if (!cancelled) {
            closeTimer = window.setTimeout(onLoginSuccess, 1000)
          }
          return
        }

        if (result.statusCode === 800) return
        pollTimer = window.setTimeout(poll, 2000)
      } catch (error) {
        if (cancelled || (error instanceof DOMException && error.name === 'AbortError')) {
          return
        }
        setView('error')
        setMessage(
          error instanceof AuthApiError && error.serviceUnavailable
            ? '登录服务暂不可用'
            : '登录状态查询失败，请重新加载',
        )
      }
    }

    pollTimer = window.setTimeout(poll, 2000)

    return () => {
      cancelled = true
      if (pollTimer !== undefined) window.clearTimeout(pollTimer)
      if (closeTimer !== undefined) window.clearTimeout(closeTimer)
      pollController?.abort()
    }
  }, [key, onLoginSuccess, refreshAuthStatus])

  const isLoading = view === 'loading'
  const canReload = view === 'expired' || view === 'error'

  return (
    <div className={`netease-login netease-login--${view}`}>
      <div className="netease-login__heading">
        <span className="netease-login__mark" aria-hidden="true">
          <QrCode size={22} strokeWidth={1.6} />
        </span>
        <div>
          <p>NETEASE CLOUD MUSIC</p>
          <h2 id="netease-login-title">网易云音乐登录</h2>
        </div>
      </div>

      <div className="netease-login__qr-stage" aria-live="polite">
        {isLoading && (
          <LoaderCircle className="netease-login__spinner" size={34} aria-hidden="true" />
        )}

        {!isLoading && qrImg && (
          <img
            className={view === 'expired' ? 'is-muted' : ''}
            src={qrImg}
            alt="网易云音乐扫码登录二维码"
            draggable={false}
          />
        )}

        {view === 'expired' && (
          <span className="netease-login__qr-overlay" aria-hidden="true">
            <RefreshCw size={25} strokeWidth={1.5} />
          </span>
        )}

        {view === 'error' && !qrImg && (
          <TriangleAlert size={32} strokeWidth={1.4} aria-hidden="true" />
        )}
      </div>

      <div className="netease-login__status">
        <span className="netease-login__status-icon" aria-hidden="true">
          {view === 'loading' && <LoaderCircle size={17} />}
          {view === 'waitingScan' && <ScanLine size={17} />}
          {view === 'waitingConfirm' && <Smartphone size={17} />}
          {view === 'success' && <Check size={17} />}
          {view === 'expired' && <RefreshCw size={17} />}
          {view === 'error' && <TriangleAlert size={17} />}
        </span>
        <p>{message}</p>
      </div>

      {canReload && (
        <button className="netease-login__reload" type="button" onClick={loadQrCode}>
          <RefreshCw size={16} strokeWidth={1.6} aria-hidden="true" />
          {view === 'expired' ? '刷新二维码' : '重新加载'}
        </button>
      )}

      <p className="netease-login__hint">二维码仅用于本次登录，请勿分享给他人</p>
    </div>
  )
}

export default NeteaseQrLogin
