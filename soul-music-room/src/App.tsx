import { useEffect, useState } from 'react'
import { useReducedMotion } from 'framer-motion'
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from 'react-router-dom'
import AppLayout from './layouts/AppLayout'
import IntroCover from './components/IntroCover'
import HomePage from './pages/HomePage'
import StaticDestinationPage from './pages/StaticDestinationPage'
import JazzHipHopPage from './pages/jazz-hiphop/JazzHipHopPage'
import LoginPage from './pages/LoginPage'
import LibraryPage from './pages/LibraryPage'
import PlaylistDetailPage from './pages/PlaylistDetailPage'
import { useAuthStore } from './store/authStore'
import {
  getPhaseDuration,
  type ExperiencePhase,
} from './transitionTimings'
import './App.css'

function EntryExperience() {
  const [phase, setPhase] = useState<ExperiencePhase>('idle')
  const reduceMotion = Boolean(useReducedMotion())
  const navigate = useNavigate()

  useEffect(() => {
    if (phase === 'idle') return

    const timeout = window.setTimeout(() => {
      if (phase === 'coverLeaving') {
        navigate('/login', { replace: true })
        return
      }

      setPhase('coverLeaving')
    }, getPhaseDuration(phase, reduceMotion))

    return () => window.clearTimeout(timeout)
  }, [navigate, phase, reduceMotion])

  const startExperience = () => {
    if (phase === 'idle') setPhase('airpodsOpening')
  }

  return (
    <IntroCover phase={phase} onExplore={startExperience} />
  )
}

function App() {
  const refreshAuthStatus = useAuthStore((state) => state.refreshAuthStatus)

  useEffect(() => {
    void refreshAuthStatus()
  }, [refreshAuthStatus])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<EntryExperience />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AppLayout />}>
          <Route path="/home" element={<HomePage />} />
          <Route path="/discover" element={<StaticDestinationPage page="discover" />} />
          <Route path="/session/create" element={<StaticDestinationPage page="session" />} />
          <Route path="/worldline" element={<StaticDestinationPage page="worldline" />} />
          <Route path="/search" element={<StaticDestinationPage page="search" />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/playlist/:id" element={<PlaylistDetailPage />} />
          <Route path="/liked" element={<StaticDestinationPage page="liked" />} />
          <Route path="/history" element={<StaticDestinationPage page="history" />} />
        </Route>
        <Route path="/genre/jazz-hiphop" element={<JazzHipHopPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
