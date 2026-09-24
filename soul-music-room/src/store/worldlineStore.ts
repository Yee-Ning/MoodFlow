import { create } from 'zustand'
import { createWorldLineSession, exploreWorldLine } from '../services/worldline'
import type { WorldLineSessionResult } from '../types/worldline'

const STORAGE_KEY = 'moodflow-worldline-v21'

function restore(): WorldLineSessionResult | null {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY)
    if (!stored) return null
    const value = JSON.parse(stored) as WorldLineSessionResult
    return typeof value.session_id === 'string' && Array.isArray(value.graph?.nodes) ? value : null
  } catch {
    return null
  }
}

interface WorldLineStore {
  result: WorldLineSessionResult | null
  loading: boolean
  error: string
  createSession: (query: string) => Promise<void>
  explore: (branchId: string) => Promise<void>
}

export const useWorldLineStore = create<WorldLineStore>((set, get) => ({
  result: restore(),
  loading: false,
  error: '',
  createSession: async (query) => {
    if (!query.trim() || get().loading) return
    set({ loading: true, error: '' })
    try {
      const result = await createWorldLineSession(query.trim())
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(result))
      set({ result, loading: false })
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '音乐世界线暂时无法加载。' })
    }
  },
  explore: async (branchId) => {
    const previous = get().result
    if (!previous || get().loading) return
    const action = previous.available_actions.find((item) => item.id === branchId)
    if (!action?.enabled) return
    set({ loading: true, error: '' })
    try {
      const result = await exploreWorldLine(previous.session_id, branchId)
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(result))
      set({ result, loading: false })
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '探索失败，请稍后重试。' })
    }
  },
}))
