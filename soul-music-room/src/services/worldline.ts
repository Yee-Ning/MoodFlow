import type { WorldLineSessionResult } from '../types/worldline'

const AI_API_BASE_URL = (import.meta.env.VITE_AI_API_BASE_URL ?? '').replace(/\/$/, '')

interface WorldLineErrorPayload {
  detail?: {
    code?: string
    message?: string
  }
}

export class WorldLineApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'WorldLineApiError'
    this.code = code
    this.status = status
  }
}

async function requestWorldLine(
  path: string,
  init?: RequestInit,
): Promise<WorldLineSessionResult> {
  const response = await fetch(`${AI_API_BASE_URL}${path}`, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as WorldLineErrorPayload | null
    throw new WorldLineApiError(
      payload?.detail?.message ?? '音乐世界线暂时无法加载。',
      payload?.detail?.code ?? 'worldline_request_failed',
      response.status,
    )
  }
  return response.json() as Promise<WorldLineSessionResult>
}

function postWorldLine(path: string, body: Record<string, string>) {
  return requestWorldLine(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function createWorldLineSession(query: string) {
  return postWorldLine('/api/ai/worldline/sessions', { query })
}

export function recoverWorldLineSession(sessionId: string) {
  return requestWorldLine(`/api/ai/worldline/sessions/${encodeURIComponent(sessionId)}`)
}

export function exploreWorldLine(sessionId: string, branchId: string) {
  return postWorldLine('/api/ai/worldline/explore', {
    session_id: sessionId,
    branch_id: branchId,
  })
}
