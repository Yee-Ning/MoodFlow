import { create } from 'zustand'
import { AiChatApiError, streamAiChatMessage } from '../services/aiChat'
import type {
  AiChatMessage,
  AiChatStatus,
} from '../types/aiChat'

interface AiChatState {
  messages: AiChatMessage[]
  isOpen: boolean
  isSending: boolean
  conversationId: string | null
  error: string
  status: AiChatStatus
  open: () => void
  close: () => void
  clearConversation: () => void
  clearError: () => void
  sendMessage: (message: string, currentSongId: number | null) => Promise<void>
  retryLastMessage: (currentSongId: number | null) => Promise<void>
}

function createMessageId(prefix: string) {
  const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${id}`
}

function createConversationId() {
  return createMessageId('conversation')
}

let activeController: AbortController | null = null
let activeRequestId: string | null = null

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}

export const useAiChatStore = create<AiChatState>((set, get) => ({
  messages: [],
  isOpen: false,
  isSending: false,
  conversationId: createConversationId(),
  error: '',
  status: 'idle',

  open: () => set({ isOpen: true }),
  close: () => set({ isOpen: false }),
  clearConversation: () => {
    activeRequestId = null
    activeController?.abort()
    activeController = null
    set({
      messages: [],
      conversationId: createConversationId(),
      isSending: false,
      error: '',
      status: 'idle',
    })
  },
  clearError: () => set((state) => ({
    error: '',
    status: state.isSending ? state.status : 'idle',
  })),

  sendMessage: async (rawMessage, currentSongId) => {
    const message = rawMessage.trim()
    if (!message || get().isSending) return
    const threadId = get().conversationId ?? createConversationId()
    const requestId = createMessageId('request')
    const assistantId = createMessageId('assistant')
    const controller = new AbortController()
    activeRequestId = requestId
    activeController = controller

    const userMessage: AiChatMessage = {
      id: createMessageId('user'),
      role: 'user',
      content: message,
      createdAt: Date.now(),
    }
    const assistantMessage: AiChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
    }

    set((state) => ({
      messages: [...state.messages, userMessage, assistantMessage],
      isSending: true,
      status: 'sending',
      error: '',
      conversationId: state.conversationId ?? threadId,
    }))

    await Promise.resolve()
    if (activeRequestId === requestId) set({ status: 'loading-context' })

    try {
      let receivedFinal = false
      let receivedDone = false
      const stream = streamAiChatMessage(
        {
          message,
          thread_id: threadId,
          current_song_id: currentSongId,
        },
        controller.signal,
      )

      for await (const event of stream) {
        if (activeRequestId !== requestId) return

        if (event.type === 'message') {
          set((state) => ({
            messages: state.messages.map((item) => item.id === assistantId
              ? { ...item, content: item.content + event.delta }
              : item),
            status: 'generating',
          }))
        } else if (event.type === 'update') {
          set({
            status: event.node === 'load_song_context'
              ? 'generating'
              : 'finalizing',
          })
        } else if (event.type === 'final') {
          receivedFinal = true
          set((state) => ({
            messages: state.messages.map((item) => item.id === assistantId
              ? {
                  ...item,
                  content: event.response.message,
                  recommendations: event.response.recommendations,
                }
              : item),
          }))
        } else if (event.type === 'error') {
          throw new AiChatApiError(event.message)
        } else if (event.type === 'done') {
          receivedDone = true
          set({ isSending: false, status: 'idle' })
        }
      }

      if (!receivedFinal || !receivedDone) {
        throw new AiChatApiError('AI 响应流意外中断，请重试')
      }
      set({ isSending: false, status: 'idle', error: '' })
    } catch (error) {
      if (isAbortError(error)) {
        if (activeRequestId === requestId) {
          set({ isSending: false, status: 'idle' })
        }
        return
      }
      if (activeRequestId === requestId) {
        set((state) => ({
          messages: state.messages.filter((item) => (
            item.id !== assistantId || item.content.length > 0
          )),
          isSending: false,
          status: 'error',
          error: error instanceof Error ? error.message : 'AI 暂时走神了，请稍后再试',
        }))
      }
    } finally {
      if (activeRequestId === requestId) {
        activeRequestId = null
        activeController = null
      }
    }
  },

  retryLastMessage: async (currentSongId) => {
    if (get().isSending) return
    const messages = get().messages
    const lastUserIndex = messages.findLastIndex((message) => message.role === 'user')
    if (lastUserIndex < 0) return
    const lastUserMessage = messages[lastUserIndex]
    set({
      messages: messages.slice(0, lastUserIndex),
      error: '',
      status: 'idle',
    })
    await get().sendMessage(lastUserMessage.content, currentSongId)
  },
}))
