import type { HTMLAttributes } from 'react'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'

interface GlassPanelProps extends HTMLAttributes<HTMLDivElement> {
  surface?: 'default' | 'dock' | 'overlay'
}

function GlassPanel({ children, className = '', surface = 'default', ...props }: GlassPanelProps) {
  return (
    <LiquidGlassSurface
      className={`glass-panel glass-panel--${surface} ${className}`.trim()}
      preset={surface === 'dock' ? 'dock' : surface === 'overlay' ? 'overlay' : 'panel'}
      radius={surface === 'dock' ? 38 : surface === 'overlay' ? 30 : 26}
      {...props}
    >
      {children}
    </LiquidGlassSurface>
  )
}

export default GlassPanel
