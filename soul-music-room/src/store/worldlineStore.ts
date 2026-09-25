import { create } from 'zustand'
import {
  createWorldLineSession,
  exploreWorldLine,
  recoverWorldLineSession,
  WorldLineApiError,
} from '../services/worldline'
import type { WorldLineSessionResult } from '../types/worldline'

const STORAGE_KEY = 'moodflow-worldline-session'
const LEGACY_STORAGE_KEY = 'moodflow-worldline-v21'

type SessionStatus = 'idle' | 'recovering' | 'ready' | 'expired'
type WorldLineOperation = 'create' | 'recover' | 'explore' | null

function storage(): Storage | null {
  return typeof sessionStorage === 'undefined' ? null : sessionStorage
}

function readStoredSessionId(): string | null {
  const store = storage()
  if (!store) return null
  const current = store.getItem(STORAGE_KEY)
  if (current) return current
  const legacy = store.getItem(LEGACY_STORAGE_KEY)
  if (!legacy) return null
  try {
    const sessionId = (JSON.parse(legacy) as { session_id?: unknown }).session_id
    if (typeof sessionId !== 'string' || !sessionId) return null
    store.setItem(STORAGE_KEY, sessionId)
    store.removeItem(LEGACY_STORAGE_KEY)
    return sessionId
  } catch {
    return null
  }
}

function persistSessionId(sessionId: string) {
  storage()?.setItem(STORAGE_KEY, sessionId)
}

function clearPersistedSession() {
  storage()?.removeItem(STORAGE_KEY)
  storage()?.removeItem(LEGACY_STORAGE_KEY)
}

function message(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

interface WorldLineStore {
  result: WorldLineSessionResult | null
  sessionId: string | null
  status: SessionStatus
  operation: WorldLineOperation
  loading: boolean
  pendingBranchId: string | null
  error: string
  createSession: (query: string) => Promise<void>
  recoverSession: () => Promise<void>
  explore: (branchId: string) => Promise<void>
  clearSession: () => void
}

const initialSessionId = readStoredSessionId()

export const useWorldLineStore = create<WorldLineStore>((set, get) => ({
  result: null,
  sessionId: initialSessionId,
  status: 'idle',
  operation: null,
  loading: false,
  pendingBranchId: null,
  error: '',
  createSession: async (query) => {
    if (!query.trim() || get().loading) return
    set({ loading: true, operation: 'create', pendingBranchId: null, error: '' })
    try {
      const result = await createWorldLineSession(query.trim())
      persistSessionId(result.session_id)
      set({
        result,
        sessionId: result.session_id,
        status: 'ready',
        loading: false,
        operation: null,
      })
    } catch (error) {
      set({
        loading: false,
        operation: null,
        error: message(error, '音乐世界线暂时无法加载。'),
      })
    }
  },
  recoverSession: async () => {
    const { sessionId, loading, result } = get()
    if (!sessionId || loading || result) return
    set({ loading: true, operation: 'recover', status: 'recovering', error: '' })
    try {
      const recovered = await recoverWorldLineSession(sessionId)
      persistSessionId(recovered.session_id)
      set({
        result: recovered,
        sessionId: recovered.session_id,
        status: 'ready',
        loading: false,
        operation: null,
      })
    } catch (error) {
      if (error instanceof WorldLineApiError && error.code === 'session_not_found') {
        clearPersistedSession()
        set({
          result: null,
          sessionId: null,
          status: 'expired',
          loading: false,
          operation: null,
          error: '这条 WorldLine 会话已失效，请重新创建。',
        })
        return
      }
      set({
        status: 'idle',
        loading: false,
        operation: null,
        error: message(error, 'WorldLine 会话恢复失败，请重试。'),
      })
    }
  },
  explore: async (branchId) => {
    const previous = get().result
    if (!previous || get().loading) return
    const action = previous.available_actions.find((item) => item.id === branchId)
    if (!action?.enabled) return
    set({
      loading: true,
      operation: 'explore',
      pendingBranchId: branchId,
      error: '',
    })
    try {
      const result = await exploreWorldLine(previous.session_id, action.id)
      persistSessionId(result.session_id)
      set({
        result,
        sessionId: result.session_id,
        status: 'ready',
        loading: false,
        operation: null,
        pendingBranchId: null,
      })
    } catch (error) {
      set({
        loading: false,
        operation: null,
        pendingBranchId: null,
        error: message(error, '探索失败，请稍后重试。'),
      })
    }
  },
  clearSession: () => {
    clearPersistedSession()
    set({
      result: null,
      sessionId: null,
      status: 'idle',
      operation: null,
      loading: false,
      pendingBranchId: null,
      error: '',
    })
  },
}))
