import { useMemo, useState } from 'react'

import Graph from './Graph.jsx'
import { Decision, Images, MaterialFacts, Modules, WhyPanel } from './parts.jsx'
import { href } from './route.js'
import {
  Badge, Box, ErrorNote, Loading, Note, SIGNALS, Score, ago, materialy, shortDate, useFetch,
} from './ui.jsx'

const reviewCount = (summary) => Number(/\((\d+) do weryfikacji\)/.exec(summary ?? '')?.[1] ?? 0)
const labelsOf = (graph) => Object.fromEntries((graph?.nodes ?? []).map((n) => [n.id, n.label]))

function PageHead({ kicker, title, children, back }) {
  return (
    <header className="page-head">
      {back && <a className="back" href={back.href}>← {back.label}</a>}
      {kicker && <span className="kicker">{kicker}</span>}
      <h1 className="page-title">{title}</h1>
      {children}
    </header>
  )
}

function Toggle({ on, onChange, children }) {
  return (
    <button type="button" className={'toggle' + (on ? ' on' : '')} aria-pressed={on} onClick={() => onChange(!on)}>
      <span className="tick" aria-hidden="true">{on ? '✓' : ''}</span>{children}
    </button>
  )
}

function Chips({ items, max = 4, tone = '' }) {
  if (!items?.length) return null
  return (
    <span className="chips">
      {items.slice(0, max).map((x) => <span className={'chip-s ' + tone} key={x}>{x}</span>)}
      {items.length > max && <span className="chip-s more-c">+{items.length - max}</span>}
    </span>
  )
}

// ============================================================================ przegląd kampanii

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

function CampaignCard({ c }) {
  const review = reviewCount(c.summary)
  const signals = Object.keys(c.evidence?.signals ?? {}).map((s) => SIGNALS[s] ?? s)
  return (
    <Box as="article" className="ccard" hatch>
      <div className="ccard-top">
        <a className="ccard-title" href={href.campaign(c.id)}>{c.label || `Kampania ${c.id}`}</a>
        <span className="ccard-size">{materialy(c.size)}</span>
      </div>
      <div className="ccard-row">
        <Chips items={c.persons} max={3} />
        <Chips items={c.domains} max={2} tone="mono" />
      </div>
      <div className="ccard-row meta">
        {signals.length > 0 && <span>łączy: {signals.join(' · ')}</span>}
        <span>{shortDate(c.first_seen)} → {shortDate(c.last_seen)}</span>
      </div>
      <div className="ccard-row">
        {review > 0 && <Badge status="do_weryfikacji">{review} do weryfikacji</Badge>}
        {c.operator_group != null && (
          <a className="op-link" href={href.operator(c.operator_group)}>⛓ operator #{c.operator_group}</a>
        )}
      </div>
    </Box>
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

export function CampaignsPage() {
  const camps = useFetch('/campaigns')
  const [tab, setTab] = useState('lista')
  const [multi, setMulti] = useState(true)
  const [review, setReview] = useState(false)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState(null)
  const map = useFetch(tab === 'mapa' ? '/graph?entities=false' : null)

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
            {map.data && <Graph data={map.data} onSelect={setSelected} height={640} />}
          </div>
          <WhyPanel selected={selected} labels={labelsOf(map.data)} />
        </div>
      )}
    </div>
  )
}

// ============================================================================ kampania

export function CampaignPage({ id }) {
  const c = useFetch(`/campaigns/${id}`)
  const g = useFetch(`/graph?campaign_id=${id}`)
  const [selected, setSelected] = useState(null)
  const [msg, setMsg] = useState(null)
  const labels = labelsOf(g.data)

  const afterDecision = (res) => {
    setSelected(null)
    setMsg(res.decision === 'odrzucony'
      ? <>Materiał wyłączony z kampanii – trafił do <a href={href.campaign(res.campaign_id)}>osobnej kampanii #{res.campaign_id}</a>.</>
      : res.decision === 'potwierdzony' ? 'Przynależność potwierdzona.' : 'Decyzja cofnięta, materiał przypisany od nowa.')
    c.reload()
    g.reload()
  }

  if (c.loading && !c.data) return <div className="view"><Loading /></div>
  if (c.error) return <div className="view"><ErrorNote error={c.error} retry={c.reload} /></div>
  const camp = c.data
  const members = camp.members ?? []
  const toReview = members.filter((m) => m.assignment === 'do_weryfikacji')

  return (
    <div className="view">
      <PageHead kicker={`Kampania #${camp.id}`} title={camp.label || `Kampania ${camp.id}`}
                back={{ href: href.campaigns, label: 'wszystkie kampanie' }}>
        <p className="page-lead">{camp.summary}</p>
        <div className="head-meta">
          <Chips items={camp.persons} max={5} />
          <Chips items={camp.domains} max={4} tone="mono" />
          <span>{shortDate(camp.first_seen)} → {shortDate(camp.last_seen)}</span>
          {camp.operator_group != null && (
            <a className="op-link" href={href.operator(camp.operator_group)}>⛓ operator #{camp.operator_group}</a>
          )}
        </div>
      </PageHead>

      {msg && <Note tone="ok">{msg}</Note>}
      {toReview.length > 0 && (
        <Note tone="warn">{toReview.length === 1 ? '1 materiał czeka' : `${toReview.length} materiały czekają`} na decyzję analityka – przerywane linie w grafie.</Note>
      )}

      <div className="split">
        <div>
          {g.loading && !g.data && <Loading text="Rysuję graf…" />}
          {g.error && <ErrorNote error={g.error} retry={g.reload} />}
          {g.data && <Graph data={g.data} onSelect={setSelected} hideTypes={['campaign', 'operator']} layout="concentric" />}
        </div>
        <WhyPanel selected={selected} labels={labels} onDecision={members.length > 1 ? afterDecision : null} />
      </div>

      <section>
        <h2 className="section-title">Materiały ({members.length})</h2>
        <div className="table">
          {members.map((m) => (
            <Box className="trow" key={m.id}>
              <a className="trow-title" href={href.material(m.id)}>{m.title || m.source || m.id}</a>
              <span className="mono small">{m.domain ?? ''}</span>
              <span><Badge status={m.assignment} /> {m.score != null && <Score value={m.score} />}</span>
              <span className="small">{ago(m.created_at)}</span>
              {members.length > 1
                ? <Decision materialId={m.id} assignment={m.assignment} onDone={afterDecision} compact />
                : <span />}
              {m.decision_note && <span className="trow-note">notatka: {m.decision_note}</span>}
            </Box>
          ))}
        </div>
      </section>

      {camp.links?.length > 0 && (
        <section>
          <h2 className="section-title">Najmocniejsze powiązania</h2>
          <div className="links">
            {camp.links.slice(0, 6).map((l) => (
              <Box className="link-card" key={`${l.a}-${l.b}`}>
                <div className="pair">
                  <a href={href.material(l.a)}>{labels[`material:${l.a}`] ?? l.a}</a>
                  <span className="pair-x">↔</span>
                  <a href={href.material(l.b)}>{labels[`material:${l.b}`] ?? l.b}</a>
                  <Score value={l.score} />
                </div>
                <ul className="reasons">{(l.reasons ?? []).slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}</ul>
              </Box>
            ))}
          </div>
        </section>
      )}

      {camp.related_campaigns?.length > 0 && (
        <section>
          <h2 className="section-title">Ten sam operator</h2>
          <div className="links">
            {camp.related_campaigns.map((r) => (
              <Box className="link-card" key={r.id}>
                <a className="ccard-title small" href={href.campaign(r.id)}>{r.label}</a>
                <span className="small"> · {materialy(r.size)}</span>
                <ul className="reasons">{(r.reasons ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul>
              </Box>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ============================================================================ operator

export function OperatorPage({ id }) {
  const g = useFetch(`/graph?operator=${id}`)
  const camps = useFetch('/campaigns')
  const [selected, setSelected] = useState(null)
  const mine = (camps.data?.items ?? []).filter((c) => String(c.operator_group) === String(id) || String(c.id) === String(id))

  return (
    <div className="view">
      <PageHead kicker="Operator" title={`Operator #${id}`} back={{ href: href.campaigns, label: 'wszystkie kampanie' }}>
        <p className="page-lead">
          Kampanie o różnej treści, ale ze wspólną infrastrukturą: tym samym pikselem, kontem reklamowym,
          katalogiem kitu albo serwerem. To najpewniej jeden zespół oszustów.
        </p>
      </PageHead>
      <div className="ccards">{mine.map((c) => <CampaignCard c={c} key={c.id} />)}</div>
      <div className="split">
        <div>
          {g.loading && <Loading text="Rysuję graf…" />}
          {g.error && <ErrorNote error={g.error} retry={g.reload} />}
          {g.data && <Graph data={g.data} onSelect={setSelected} height={620} />}
        </div>
        <WhyPanel selected={selected} labels={labelsOf(g.data)} />
      </div>
    </div>
  )
}

// ============================================================================ materiał

export function MaterialPage({ id }) {
  const m = useFetch(`/materials/${encodeURIComponent(id)}`)
  const g = useFetch(`/graph?material_id=${encodeURIComponent(id)}`)
  const [selected, setSelected] = useState(null)

  const afterDecision = () => { setSelected(null); m.reload(); g.reload() }

  if (m.loading && !m.data) return <div className="view"><Loading /></div>
  if (m.error) return <div className="view"><ErrorNote error={m.error} retry={m.reload} /></div>
  const d = m.data

  return (
    <div className="view">
      <PageHead kicker="Materiał" title={d.title || d.domain || d.id}
                back={d.campaign_id != null ? { href: href.campaign(d.campaign_id), label: `kampania #${d.campaign_id}` } : { href: href.campaigns, label: 'kampanie' }}>
        <div className="head-meta">
          <Badge status={d.assignment} />
          {d.assignment_score != null && <Score value={d.assignment_score} />}
          <Modules modules={d.modules} />
          <span className="small">{ago(d.created_at)}</span>
        </div>
      </PageHead>

      <div className="split">
        <div className="stack">
          <Box className="sheet" hatch>
            <h3 className="panel-title">Co w nim jest</h3>
            <MaterialFacts m={d} />
            {d.final_url && <p className="small mono">adres: {d.final_url}</p>}
            {d.source && <p className="small">źródło: {d.source}</p>}
            {d.missing?.length > 0 && <Note>Braki: {d.missing.slice(0, 4).join('; ')}</Note>}
          </Box>
          <Images materialId={d.id} />
          {d.text_preview && (
            <Box className="sheet">
              <h3 className="panel-title">Tekst</h3>
              <p className="text-preview">{d.text_preview}</p>
            </Box>
          )}
        </div>
        <div className="stack">
          {d.campaign_id != null && (
            <Box className="panel" double>
              <h3 className="panel-title">Kampania</h3>
              <a className="more" href={href.campaign(d.campaign_id)}>Otwórz kampanię #{d.campaign_id} →</a>
              <Decision materialId={d.id} assignment={d.assignment} onDone={afterDecision} />
            </Box>
          )}
        </div>
      </div>

      <section>
        <h2 className="section-title">Otoczenie materiału</h2>
        <div className="split">
          <div>
            {g.loading && !g.data && <Loading text="Rysuję graf…" />}
            {g.data && <Graph data={g.data} onSelect={setSelected} focusId={`material:${d.id}`} height={520} />}
          </div>
          <WhyPanel selected={selected} labels={labelsOf(g.data)} />
        </div>
      </section>
    </div>
  )
}
