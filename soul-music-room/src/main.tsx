import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LiquidGlassProvider } from './components/liquid-glass/LiquidGlassProvider'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LiquidGlassProvider>
      <App />
    </LiquidGlassProvider>
  </StrictMode>,
)
