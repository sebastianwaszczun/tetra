import { Badge, Box, Chips, SIGNALS, materialy, shortDate } from '../components/ui.jsx'
import { href } from '../lib/route.js'

export const reviewCount = (summary) => Number(/\((\d+) do weryfikacji\)/.exec(summary ?? '')?.[1] ?? 0)
export const labelsOf = (graph) => Object.fromEntries((graph?.nodes ?? []).map((n) => [n.id, n.label]))

export function PageHead({ kicker, title, children, back }) {
  return (
    <header className="page-head">
      {back && <a className="back" href={back.href}>← {back.label}</a>}
      {kicker && <span className="kicker">{kicker}</span>}
      <h1 className="page-title">{title}</h1>
      {children}
    </header>
  )
}

export function CampaignCard({ c }) {
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
