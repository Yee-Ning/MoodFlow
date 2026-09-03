import { create } from 'zustand'
import { sendAiChatMessage } from '../services/aiChat'
import type {
  AiChatContext,
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
  toggleOpen: () => void
  close: () => void
  clearError: () => void
  sendMessage: (message: string, context?: AiChatContext) => Promise<void>
  retryLastMessage: (context?: AiChatContext) => Promise<void>
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

export const useAiChatStore = create<AiChatState>((set, get) => ({
  messages: [],
  isOpen: false,
  isSending: false,
  conversationId: null,
  error: '',
  status: 'idle',

  toggleOpen: () => set((state) => ({ isOpen: !state.isOpen })),
  close: () => set({ isOpen: false }),
  clearError: () => set((state) => ({
    error: '',
    status: state.isSending ? state.status : 'idle',
  })),

  sendMessage: async (rawMessage, context) => {
    const message = rawMessage.trim()
    if (!message || get().isSending) return

    const userMessage: AiChatMessage = {
      id: createMessageId('user'),
      role: 'user',
      content: message,
      createdAt: Date.now(),
    }

    set((state) => ({
      messages: [...state.messages, userMessage],
      isSending: true,
      status: 'sending',
      error: '',
      conversationId: state.conversationId ?? createConversationId(),
    }))

    await Promise.resolve()
    if (get().isSending) set({ status: 'loading' })

    try {
      const response = await sendAiChatMessage({ message, context })
      const assistantMessage: AiChatMessage = {
        id: createMessageId('assistant'),
        role: 'assistant',
        content: response.message,
        recommendations: response.recommendations,
        createdAt: Date.now(),
      }

      set((state) => ({
        messages: [...state.messages, assistantMessage],
        isSending: false,
        status: 'idle',
        error: '',
        conversationId: response.conversation_id ?? state.conversationId,
      }))
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      set({
        isSending: false,
        status: 'error',
        error: error instanceof Error ? error.message : 'AI 暂时走神了，请稍后再试',
      })
    }
  },

  retryLastMessage: async (context) => {
    if (get().isSending) return
    const messages = get().messages
    const lastUserIndex = messages.findLastIndex((message) => message.role === 'user')
    if (lastUserIndex < 0) return
    const lastUserMessage = messages[lastUserIndex]
    set({
      messages: messages.filter((_, index) => index !== lastUserIndex),
      error: '',
      status: 'idle',
    })
    await get().sendMessage(lastUserMessage.content, context)
  },
}))

