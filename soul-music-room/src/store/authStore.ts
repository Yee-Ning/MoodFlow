import { create } from 'zustand'
import { getAuthStatus, logout as requestLogout } from '../api/auth'
import { usePlayerStore } from './playerStore'

interface AuthState {
  loggedIn: boolean
  initialized: boolean
  checking: boolean
  refreshAuthStatus: () => Promise<boolean>
  logout: () => Promise<void>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  loggedIn: false,
  initialized: false,
  checking: false,

  refreshAuthStatus: async () => {
    if (get().checking) return get().loggedIn

    set({ checking: true })
    try {
      const result = await getAuthStatus()
      set({
        loggedIn: result.loggedIn,
        initialized: true,
        checking: false,
      })
      if (result.loggedIn) await usePlayerStore.getState().refreshLikedSongs()
      else usePlayerStore.getState().resetLikedSongs()
      return result.loggedIn
    } catch {
      set({ loggedIn: false, initialized: true, checking: false })
      usePlayerStore.getState().resetLikedSongs()
      return false
    }
  },

  logout: async () => {
    const result = await requestLogout()
    set({ loggedIn: result.loggedIn, initialized: true, checking: false })
    usePlayerStore.getState().resetLikedSongs()
  },
}))
