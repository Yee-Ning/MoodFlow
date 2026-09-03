import { ArrowLeft } from 'lucide-react'

interface JazzFooterProps {
  onReturnHome: () => void
}

function JazzFooter({ onReturnHome }: JazzFooterProps) {
  return (
    <footer className="jazz-footer">
      <p className="jazz-footer__eyebrow">END OF SIDE A</p>
      <h2>LET THE CITY SOFTEN AT THE EDGES.</h2>
      <div className="jazz-footer__note">
        <p>Jazz HipHop</p>
        <p>A room for quiet thoughts and unfinished nights.</p>
      </div>
      <button type="button" onClick={onReturnHome}>
        <ArrowLeft size={18} strokeWidth={1.5} aria-hidden="true" />
        RETURN TO THE OCEAN
      </button>
      <p className="jazz-footer__folio" aria-hidden="true">
        01 — 05
      </p>
    </footer>
  )
}

export default JazzFooter
