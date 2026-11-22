import { useState } from 'react'

import Decision from '../components/Decision.jsx'
import { Images, MaterialFacts, Modules } from '../components/MaterialInfo.jsx'
import WhyPanel from '../components/WhyPanel.jsx'
import { Badge, Box, ErrorNote, Loading, Note, Score, ago, useFetch } from '../components/ui.jsx'
import Graph from '../graph/Graph2D.jsx'
import { href } from '../lib/route.js'
import { PageHead, labelsOf } from './shared.jsx'

export default function MaterialPage({ id }) {
  const m = useFetch(`/materials/${encodeURIComponent(id)}`)
  const g = useFetch(`/graph?material_id=${encodeURIComponent(id)}`)
  const [selected, setSelected] = useState(null)

  const afterDecision = () => { setSelected(null); m.reload(); g.reload() }

  if (m.loading && !m.data) return <div className="view"><Loading /></div>
  if (m.error) {
    return (
      <div className="view">
        <PageHead kicker="Materiał" title="Brak w kartotece" back={{ href: href.campaigns, label: 'kampanie' }} />
        {/nie ma takiego/i.test(m.error.message)
          ? <Note>Tego materiału nie ma w kartotece. Mógł zostać przeanalizowany, zanim kartoteka zapisywała wyniki
              (ponowna analiza: <code>python -m fakecollector.adlib --ponow</code>), albo został usunięty.</Note>
          : <ErrorNote error={m.error} retry={m.reload} />}
      </div>
    )
  }
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
