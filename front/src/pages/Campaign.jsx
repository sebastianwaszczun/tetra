import { useState } from 'react'

import Decision from '../components/Decision.jsx'
import WhyPanel from '../components/WhyPanel.jsx'
import { Badge, Box, Chips, ErrorNote, Loading, Note, Score, ago, materialy, shortDate, useFetch } from '../components/ui.jsx'
import Graph from '../graph/Graph2D.jsx'
import { href } from '../lib/route.js'
import { PageHead, labelsOf } from './shared.jsx'

export default function CampaignPage({ id }) {
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
          {g.data && <Graph data={g.data} onSelect={setSelected} hideTypes={['campaign', 'operator']} />}
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
