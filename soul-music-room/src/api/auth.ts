const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8080'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface QrKeyData {
  key: string
}

export interface QrCreateData {
  qrurl: string
  qrimg: string
}

export type QrStatusCode = 800 | 801 | 802 | 803

export interface QrCheckData {
  statusCode: QrStatusCode
  status: 'EXPIRED' | 'WAITING_SCAN' | 'WAITING_CONFIRM' | 'LOGIN_SUCCESS'
  statusMessage: string
  loggedIn: boolean
}

export interface AuthStatusData {
  loggedIn: boolean
}

export class AuthApiError extends Error {
  readonly serviceUnavailable: boolean

  constructor(message: string, serviceUnavailable = false) {
    super(message)
    this.name = 'AuthApiError'
    this.serviceUnavailable = serviceUnavailable
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...options.headers,
      },
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new AuthApiError('登录服务暂不可用', true)
  }

  const payload = await response
    .json()
    .catch(() => {
      throw new AuthApiError('登录服务返回了无效响应')
    }) as ApiResponse<T>

  if (!response.ok || payload.code !== 200) {
    throw new AuthApiError(payload.message || '登录请求失败')
  }

  return payload.data
}

export function getQrKey(signal?: AbortSignal) {
  return request<QrKeyData>('/api/auth/qr/key', { signal })
}

export function createQrCode(key: string, signal?: AbortSignal) {
  return request<QrCreateData>(
    `/api/auth/qr/create?key=${encodeURIComponent(key)}`,
    { signal },
  )
}

export function checkQrCode(key: string, signal?: AbortSignal) {
  return request<QrCheckData>(
    `/api/auth/qr/check?key=${encodeURIComponent(key)}`,
    { signal },
  )
}

export function getAuthStatus(signal?: AbortSignal) {
  return request<AuthStatusData>('/api/auth/status', { signal })
}

export function logout() {
  return request<AuthStatusData>('/api/auth/logout', { method: 'POST' })
}
