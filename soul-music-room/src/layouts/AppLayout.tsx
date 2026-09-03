import { Outlet } from 'react-router-dom'
import BackgroundOverlay from '../components/background/BackgroundOverlay'
import MineradioParticleBackground from '../components/background/MineradioParticleBackground'
import GlobalPlayer from '../components/player/GlobalPlayer'
import Sidebar from '../components/layout/Sidebar'
import TopBar from '../components/layout/TopBar'
import AiChatPanel from '../components/ai/AiChatPanel'
import { usePlayerStore } from '../store/playerStore'
import './AppLayout.css'

function AppLayout() {
  const isPlaying = usePlayerStore((state) => state.isPlaying)
  const currentSong = usePlayerStore((state) => state.currentSong)

  return (
    <div className="app-layout">
      <MineradioParticleBackground
        playing={isPlaying}
        coverUrl={currentSong?.coverUrl}
        opacity={1}
        enablePointerParallax
      />
      <BackgroundOverlay />
      <Sidebar />

      <div className="app-layout__main app-layout__viewport">
        <TopBar />
        <div className="app-layout__page">
          <Outlet />
        </div>
      </div>

      <GlobalPlayer />
      <Sidebar mobile />
      <AiChatPanel />
    </div>
  )
}

export default AppLayout
