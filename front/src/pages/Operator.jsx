import { useState } from 'react'

import WhyPanel from '../components/WhyPanel.jsx'
import { ErrorNote, Loading, useFetch } from '../components/ui.jsx'
import Graph from '../graph/Graph2D.jsx'
import { href } from '../lib/route.js'
import { CampaignCard, PageHead, labelsOf } from './shared.jsx'

export default function OperatorPage({ id }) {
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
