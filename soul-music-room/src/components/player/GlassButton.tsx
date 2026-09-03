import { forwardRef } from 'react'
import type { ButtonHTMLAttributes } from 'react'
import LiquidGlassButton from '../liquid-glass/LiquidGlassButton'

interface GlassButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tooltip: string
  variant?: 'default' | 'primary' | 'liked'
}

const GlassButton = forwardRef<HTMLButtonElement, GlassButtonProps>(
  ({ children, className = '', tooltip, variant = 'default', type = 'button', ...props }, ref) => (
    <LiquidGlassButton
      ref={ref}
      className={`glass-button liquid-glass-button glass-button--${variant} ${className}`.trim()}
      primary={variant === 'primary'}
      type={type}
      title={tooltip}
      data-tooltip={tooltip}
      {...props}
    >
      {children}
    </LiquidGlassButton>
  ),
)

GlassButton.displayName = 'GlassButton'

export default GlassButton
