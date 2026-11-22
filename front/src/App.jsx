import { Suspense, lazy } from 'react'

import { NavLink, RoughLine, SvgDefs } from './components/sketch.jsx'
import { Loading } from './components/ui.jsx'
import { href, useRoute } from './lib/route.js'
import Home from './pages/Home.jsx'

// Widoki kartoteki ładowane na żądanie (graf i jego biblioteki nie obciążają strony głównej).
const CampaignsPage = lazy(() => import('./pages/Campaigns.jsx'))
const CampaignPage = lazy(() => import('./pages/Campaign.jsx'))
const OperatorPage = lazy(() => import('./pages/Operator.jsx'))
const MaterialPage = lazy(() => import('./pages/Material.jsx'))

const NAV = [
  { label: 'SPRAWDŹ', href: href.home, views: ['home'] },
  { label: 'PRZEGLĄD KAMPANII', href: href.campaigns, views: ['campaigns', 'campaign', 'operator', 'material'] },
]

export default function App() {
  const route = useRoute()

  return (
    <div className="page">
      <SvgDefs />
      <svg className="grain" aria-hidden="true">
        <rect width="100%" height="100%" filter="url(#grain)" />
      </svg>

      <header className="header">
        <a className="logo" href={href.home}>TETRA</a>
        <nav className="nav" aria-label="Główna">
          {NAV.map((n) => <NavLink key={n.label} href={n.href} active={n.views.includes(route.view)}>{n.label}</NavLink>)}
        </nav>
        <RoughLine d="M0 4 C300 6 600 1 1000 4" />
      </header>

      <Home hidden={route.view !== 'home'} />

      {route.view !== 'home' && (
        <main className="main view-main">
          <Suspense fallback={<Loading text="Otwieram kartotekę…" />}>
            {route.view === 'campaigns' && <CampaignsPage />}
            {route.view === 'campaign' && <CampaignPage id={route.id} key={route.id} />}
            {route.view === 'operator' && <OperatorPage id={route.id} key={route.id} />}
            {route.view === 'material' && <MaterialPage id={route.id} key={route.id} />}
          </Suspense>
        </main>
      )}

      <footer className="footer">
        <RoughLine d="M0 4 C250 2 520 7 1000 3" />
        <div className="footer-links">
          <NavLink>Polityka prywatności</NavLink>
        </div>
        <div className="logos">
          {[0, 1, 2].map((i) => (
            <div className="logo-box" key={i}>
              TETRA
              <div className="ink" aria-hidden="true" />
            </div>
          ))}
        </div>
      </footer>
    </div>
  )
}
