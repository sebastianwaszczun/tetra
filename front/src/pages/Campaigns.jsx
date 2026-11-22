import { Suspense, lazy, useMemo, useState } from 'react'

import WhyPanel from '../components/WhyPanel.jsx'
import { Box, Chips, ErrorNote, Loading, Note, Score, ago, useFetch } from '../components/ui.jsx'
import Graph from '../graph/Graph2D.jsx'
import { href } from '../lib/route.js'
import { CampaignCard, PageHead, labelsOf, reviewCount } from './shared.jsx'

const Graph3D = lazy(() => import('../graph/Graph3D.jsx'))

function Toggle({ on, onChange, children }) {
  return (
    <button type="button" className={'toggle' + (on ? ' on' : '')} aria-pressed={on} onClick={() => onChange(!on)}>
      <span className="tick" aria-hidden="true">{on ? '✓' : ''}</span>{children}
    </button>
  )
}

function Stats() {
  const { data } = useFetch('/stats')
  if (!data) return null
  const tiles = [
    ['materiałów', data.materials], ['kampanii', data.campaigns], ['operatorów', data.operators],
    ['do weryfikacji', data.to_review], ['domen', data.domains],
  ]
  return (
    <div className="stats">
      {tiles.map(([label, value]) => (
        <Box className={'stat' + (label === 'do weryfikacji' && value ? ' warn' : '')} key={label} hatch>
          <span className="stat-num">{value ?? 0}</span>
          <span className="stat-label">{label}</span>
        </Box>
      ))}
    </div>
  )
}

function ScannerFeed() {
  const { data, error } = useFetch('/adlib/ads?status=przeanalizowana&limit=6')
  const status = useFetch('/adlib/status')
  if (error || !data) return null
  const last = status.data?.ostatnie_skany?.[0]
  return (
    <section className="feed">
      <h2 className="section-title">Nowe z Biblioteki Reklam</h2>
      <Note>
        Skaner co kilka godzin przegląda reklamy wyświetlane w Polsce i wysyła podejrzane do analizy.
        {last && <> Ostatni skan: {ago(last.start)} ({last.status}).</>}
      </Note>
      {!data.length && <Note>Skaner nie przeanalizował jeszcze żadnej reklamy.</Note>}
      <div className="feed-list">
        {data.map((ad) => (
          <Box className="feed-item" key={ad.id}>
            <div className="feed-top">
              <b>{ad.page_name || ad.page_id}</b>
              <Score value={ad.wynik} label="wynik filtra wstępnego" />
            </div>
            {ad.tresc && <p className="feed-text">{ad.tresc.slice(0, 140)}{ad.tresc.length > 140 ? '…' : ''}</p>}
            <Chips items={(ad.powody ?? []).map((p) => p.opis).slice(0, 2)} max={2} tone="red" />
            <div className="feed-links">
              {ad.id_zgloszenia && <a href={href.material(ad.id_zgloszenia)}>wynik analizy →</a>}
              <a href={`https://www.facebook.com/ads/library/?id=${ad.id}`} target="_blank" rel="noreferrer">Biblioteka Reklam ↗</a>
            </div>
          </Box>
        ))}
      </div>
    </section>
  )
}

export default function CampaignsPage() {
  const camps = useFetch('/campaigns')
  const [tab, setTab] = useState('lista')
  const [multi, setMulti] = useState(true)
  const [review, setReview] = useState(false)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState(null)
  const [dim, setDim] = useState('2d')
  const map = useFetch(tab === 'mapa' ? '/graph' : null)

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (camps.data?.items ?? []).filter((c) => {
      if (multi && c.size < 2) return false
      if (review && !reviewCount(c.summary)) return false
      if (!needle) return true
      return [c.label, ...(c.persons ?? []), ...(c.domains ?? []), ...(c.brands ?? [])]
        .some((x) => String(x ?? '').toLowerCase().includes(needle))
    })
  }, [camps.data, multi, review, q])
  const hiddenSingles = (camps.data?.items ?? []).filter((c) => c.size < 2).length

  return (
    <div className="view">
      <PageHead kicker="Kartoteka" title="Przegląd kampanii">
        <p className="page-lead">
          Materiały o tej samej treści tworzą kampanię. Kampanie ze wspólną infrastrukturą (piksel, kit, serwer)
          należą do jednego operatora. Każde powiązanie ma przesłanki – kliknij, żeby je zobaczyć.
        </p>
      </PageHead>
      <Stats />

      <div className="tabs" role="tablist">
        {[['lista', 'Lista'], ['mapa', 'Mapa powiązań']].map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={'tab' + (tab === k ? ' on' : '')}
                  type="button" onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === 'lista' && (
        <>
          <div className="filters">
            <Toggle on={multi} onChange={setMulti}>co najmniej 2 materiały</Toggle>
            <Toggle on={review} onChange={setReview}>z materiałami do weryfikacji</Toggle>
            <Box className="search-mini">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="osoba, domena, marka…"
                     aria-label="Szukaj kampanii" />
            </Box>
          </div>
          {camps.loading && <Loading />}
          {camps.error && <ErrorNote error={camps.error} retry={camps.reload} />}
          {camps.data && !list.length && (
            <Note>{camps.data.count ? 'Żadna kampania nie pasuje do filtrów.' : 'Nie ma jeszcze kampanii – sprawdź materiał na stronie głównej, a kartoteka zacznie się układać.'}</Note>
          )}
          <div className="ccards">{list.map((c) => <CampaignCard c={c} key={c.id} />)}</div>
          {multi && hiddenSingles > 0 && (
            <Note>
              Ukryto {hiddenSingles} {hiddenSingles === 1 ? 'kampanię' : 'kampanii'} z jednym materiałem (nowe wzorce, jeszcze bez podobnych).{' '}
              <button className="link-btn" type="button" onClick={() => setMulti(false)}>Pokaż wszystkie</button>
            </Note>
          )}
          <ScannerFeed />
        </>
      )}

      {tab === 'mapa' && (
        <div className="split">
          <div>
            {map.loading && <Loading text="Rysuję mapę…" />}
            {map.error && <ErrorNote error={map.error} retry={map.reload} />}
            <div className="dim-switch" role="group" aria-label="Widok mapy">
              {[['2d', 'szkic 2D'], ['3d', 'przestrzeń 3D']].map(([k, label]) => (
                <button key={k} type="button" className={'tab small' + (dim === k ? ' on' : '')} aria-pressed={dim === k}
                        onClick={() => { setDim(k); setSelected(null) }}>{label}</button>
              ))}
            </div>
            {map.data && dim === '2d' && <Graph data={map.data} onSelect={setSelected} height={640} />}
            {map.data && dim === '3d' && (
              <Suspense fallback={<Loading text="Przestawiam kartotekę w 3D…" />}>
                <Graph3D data={map.data} onSelect={setSelected} height={640} />
              </Suspense>
            )}
          </div>
          <WhyPanel selected={selected} labels={labelsOf(map.data)} />
        </div>
      )}
    </div>
  )
}
