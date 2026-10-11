const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')

export interface MemoryPreferences { enabled: boolean; generation: number; projection_pending?: boolean }
export interface MemoryRecord {
  memory_id: string
  memory_type: 'profile' | 'episodic'
  content: string
  status: 'active' | 'superseded' | 'inactive'
  updated_at: string
  valid_until: string | null
  evidence_quote: string | null
  structured_content: { category: string; subject: string; scene: string | null }
}
export interface MemoryPage { items: MemoryRecord[]; next_cursor: string | null; preferences: MemoryPreferences }

async function request<T>(path: string, options: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${AI_API_BASE_URL}/api/ai/memory${path}`, {
    ...options, credentials: 'include', signal,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...options.headers },
  })
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
    throw new Error(typeof detail === 'string' ? detail : response.status === 401
      ? '登录后才能管理记忆' : '记忆服务暂不可用，请稍后重试')
  }
  return payload as T
}

export async function listMemories(cursor = '', signal?: AbortSignal) {
  const page = await request<MemoryPage>(`/records?limit=20&after_id=${encodeURIComponent(cursor)}`, {}, signal)
  if (!page || !Array.isArray(page.items) || typeof page.preferences?.enabled !== 'boolean'
    || !page.items.every((item) => item && typeof item.memory_id === 'string' && typeof item.content === 'string'
      && item.structured_content && typeof item.status === 'string')
    || !(page.next_cursor === null || typeof page.next_cursor === 'string')) {
    throw new Error('记忆服务返回了无效响应，请稍后重试')
  }
  return page
}
export function setMemoryEnabled(enabled: boolean) {
  return request<MemoryPreferences>('/preferences', { method: 'PATCH', body: JSON.stringify({ enabled }) })
}
export function forgetMemory(id: string) {
  return request<{ forgotten: boolean; projection_pending: boolean }>(`/records/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
export function correctMemory(id: string, text: string, requestId: string) {
  return request<{ projection_pending: boolean }>(`/records/${encodeURIComponent(id)}`, {
    method: 'PATCH', body: JSON.stringify({ text, request_id: requestId }),
  })
}
