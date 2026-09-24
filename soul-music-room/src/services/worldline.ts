import type { WorldLineSessionResult } from '../types/worldline'

const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')

async function postWorldLine(path: string, body: Record<string, string>): Promise<WorldLineSessionResult> {
  const response = await fetch(`${AI_API_BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: { message?: string } } | null
    throw new Error(payload?.detail?.message ?? '音乐世界线暂时无法加载。')
  }
  return response.json() as Promise<WorldLineSessionResult>
}

export function createWorldLineSession(query: string) {
  return postWorldLine('/api/ai/worldline/sessions', { query })
}

export function exploreWorldLine(sessionId: string, branchId: string) {
  return postWorldLine('/api/ai/worldline/explore', { session_id: sessionId, branch_id: branchId })
}
