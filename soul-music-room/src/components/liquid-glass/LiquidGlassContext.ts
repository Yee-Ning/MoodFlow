import { createContext, useContext } from 'react'

export type LiquidGlassMode = 'full' | 'fallback'

export interface LiquidGlassRuntimeValue {
  mode: LiquidGlassMode
  reducedMotion: boolean
}

export const LiquidGlassRuntimeContext = createContext<LiquidGlassRuntimeValue>({
  mode: 'fallback',
  reducedMotion: false,
})

export const LiquidGlassScopeContext = createContext(false)

export function useLiquidGlassRuntime() {
  return useContext(LiquidGlassRuntimeContext)
}

export function useLiquidGlassScope() {
  return useContext(LiquidGlassScopeContext)
}

