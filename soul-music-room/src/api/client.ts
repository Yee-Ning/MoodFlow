const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8080'

interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export class BackendApiError extends Error {
  readonly status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = 'BackendApiError'
    this.status = status
  }
}

export async function apiRequest<T>(
  path: string,
  signal?: AbortSignal,
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
      signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new BackendApiError('暂时无法连接音乐服务，请稍后重试')
  }

  const payload = await response.json().catch(() => {
    throw new BackendApiError('音乐服务返回了无效响应', response.status)
  }) as ApiResponse<T>

  if (!response.ok || payload.code !== 200) {
    throw new BackendApiError(payload.message || '音乐服务请求失败', response.status)
  }

  return payload.data
}
