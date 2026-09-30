import { create } from 'zustand'
import { emptySearchResults, searchCatalog, type SearchCategory, type SearchErrors, type SearchResults } from '../api/musicSearch'

interface SearchState {
  query: string
  category: SearchCategory
  status: 'idle' | 'loading' | 'ready'
  results: SearchResults
  errors: SearchErrors
  loadSearch: (query: string, category: SearchCategory) => () => void
}

let requestVersion = 0
let activeController: AbortController | undefined

export const useSearchStore = create<SearchState>((set) => ({
  query: '', category: 'all', status: 'idle', results: emptySearchResults(), errors: {},
  loadSearch: (query, category) => {
    activeController?.abort()
    const version = ++requestVersion
    const controller = new AbortController()
    activeController = controller
    const keyword = query.trim()
    set({ query: keyword, category, status: keyword ? 'loading' : 'idle', results: emptySearchResults(), errors: {} })
    if (keyword) {
      void searchCatalog(keyword, category, controller.signal).then(({ results, errors }) => {
        if (version === requestVersion && !controller.signal.aborted) set({ status: 'ready', results, errors })
      }).catch((error: unknown) => {
        if (version !== requestVersion || controller.signal.aborted) return
        const message = error instanceof Error ? error.message : '搜索暂时不可用，请重试'
        set({ status: 'ready', errors: category === 'all'
          ? { song: message, artist: message, album: message, playlist: message }
          : { [category]: message } })
      })
    }
    return () => controller.abort()
  },
}))
