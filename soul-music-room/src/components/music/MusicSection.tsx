import type { ReactNode } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router-dom'
import './music.css'

interface MusicSectionProps {
  eyebrow: string
  title: string
  description?: string
  children: ReactNode
  className?: string
  viewAllPath?: string
}

function MusicSection({
  eyebrow,
  title,
  description,
  children,
  className = '',
  viewAllPath = '/discover',
}: MusicSectionProps) {
  return (
    <section className={`music-section ${className}`.trim()}>
      <div className="music-section__heading">
        <div>
          <p>{eyebrow}</p>
          <h2>{title}</h2>
          {description && <span>{description}</span>}
        </div>
        <Link to={viewAllPath}>
          VIEW ALL
          <ArrowUpRight size={15} strokeWidth={1.5} aria-hidden="true" />
        </Link>
      </div>
      {children}
    </section>
  )
}

export default MusicSection
