import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import LiquidGlassButton from '../liquid-glass/LiquidGlassButton'

export interface GlassIconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: ReactNode
  label: string
  active?: boolean
  primary?: boolean
  liked?: boolean
}

const GlassIconButton = forwardRef<HTMLButtonElement, GlassIconButtonProps>(
  ({
    active = false,
    className = '',
    disabled = false,
    icon,
    label,
    liked = false,
    primary = false,
    type = 'button',
    ...buttonProps
  }, ref) => (
    <LiquidGlassButton
      {...buttonProps}
      ref={ref}
      className={`glass-icon-button glass-button${primary ? ' glass-button--primary' : ''}${liked ? ' glass-button--liked' : ''}${active ? ' is-active' : ''} ${className}`.trim()}
      primary={primary}
      type={type}
      title={label}
      data-tooltip={label}
      aria-label={buttonProps['aria-label'] ?? label}
      disabled={disabled}
    >
      {icon}
    </LiquidGlassButton>
  ),
)

GlassIconButton.displayName = 'GlassIconButton'

export default GlassIconButton
