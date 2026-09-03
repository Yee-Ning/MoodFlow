import { Clock3, Library, Search, Waves } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import LiquidGlassSurface from '../liquid-glass/LiquidGlassSurface'
import './layout.css'

const navItems = [
  { label: 'Search', path: '/search', icon: Search },
  { label: 'Library', path: '/library', icon: Library },
] as const

interface SidebarProps {
  mobile?: boolean
}

function Sidebar({ mobile = false }: SidebarProps) {
  return (
    <aside
      className={mobile ? 'mobile-nav' : 'sidebar'}
      aria-label={mobile ? '移动端导航' : '主导航'}
    >
      <LiquidGlassSurface
        className={mobile ? 'mobile-nav__surface' : 'sidebar__surface'}
        preset="panel"
        radius={mobile ? 22 : 34}
        intensity={mobile ? 0.62 : 0.58}
      >
        {!mobile && (
          <NavLink className="sidebar__brand" to="/home" aria-label="MoodFlow 首页">
            <span className="sidebar__brand-mark" aria-hidden="true">
              <Waves size={20} strokeWidth={1.7} />
            </span>
            <span>
              <strong>MoodFlow</strong>
              <small>YOUR MUSIC WORLD</small>
            </span>
          </NavLink>
        )}

        <nav className={mobile ? 'mobile-nav__items' : 'sidebar__nav'}>
          {navItems.map(({ label, path, icon: Icon }) => (
            <NavLink
              className={({ isActive }) =>
                `${mobile ? 'mobile-nav__item' : 'sidebar__item'}${isActive ? ' is-active' : ''}`
              }
              key={path}
              to={path}
            >
              <Icon size={mobile ? 19 : 18} strokeWidth={1.65} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {!mobile && (
          <div className="sidebar__slow-note">
            <Clock3 size={15} strokeWidth={1.5} aria-hidden="true" />
            <span>Take your time.<br />The tide is still here.</span>
          </div>
        )}
      </LiquidGlassSurface>
    </aside>
  )
}

export default Sidebar
