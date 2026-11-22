import { imageUrl } from '../lib/api.js'
import { Chips, MODULES, PERSUASION, useFetch } from './ui.jsx'

export function MaterialFacts({ m }) {
  const f = m.features ?? {}
  const t = f.text, o = f.osint, im = f.image, av = f.av
  const persuasion = Object.entries(t?.persuasion ?? {}).filter(([, v]) => v).map(([k]) => PERSUASION[k] ?? k)
  const domains = (o?.domains ?? []).filter((d) => d.role !== 'commodity' && d.registered_domain).slice(0, 4)
  return (
    <dl className="facts wide">
      {t?.narrative?.label && <><dt>narracja</dt><dd>{t.narrative.label}</dd></>}
      {persuasion.length > 0 && <><dt>presja</dt><dd><Chips items={persuasion} tone="red" /></dd></>}
      {(m.persons?.length > 0 || m.persons_visual?.length > 0) && (
        <><dt>osoby</dt><dd><Chips items={[...new Set([...(m.persons ?? []), ...(m.persons_visual ?? [])])]} /></dd></>
      )}
      {m.brands?.length > 0 && <><dt>marki</dt><dd><Chips items={m.brands} /></dd></>}
      {t?.money?.length > 0 && <><dt>kwoty</dt><dd>{t.money.slice(0, 4).join(', ')}</dd></>}
      {domains.length > 0 && (
        <><dt>domeny</dt><dd>{domains.map((d) => (
          <span className="dom" key={d.domain}>
            {d.domain}
            {d.age_days_at_observation != null && <em> · {d.age_days_at_observation} dni</em>}
            {d.rdap?.registrar && <em> · {d.rdap.registrar}</em>}
            {d.status_flags?.length > 0 && <em className="red"> · {d.status_flags.join(', ')}</em>}
          </span>
        ))}</dd></>
      )}
      {o?.pivots?.length > 0 && <><dt>ślady OSINT</dt><dd>{o.pivots.length}</dd></>}
      {im?.logo?.length > 0 && <><dt>logo</dt><dd><Chips items={im.logo} /></dd></>}
      {im?.deepfake_score != null && <><dt>deepfake</dt><dd>{(im.deepfake_score * 100).toFixed(0)}% (orientacyjnie)</dd></>}
      {im?.qr?.length > 0 && <><dt>kody QR</dt><dd>{im.qr.join(', ')}</dd></>}
      {av?.transcript && <><dt>transkrypcja</dt><dd className="quote">„{av.transcript.slice(0, 240)}{av.transcript.length > 240 ? '…' : ''}”</dd></>}
    </dl>
  )
}

export function Modules({ modules }) {
  if (!modules) return null
  return (
    <span className="modules">
      {Object.entries(MODULES).map(([k, name]) => {
        const s = modules[k]?.status
        const cls = s === 'ok' ? 'ok' : s === 'błąd' ? 'bad' : 'mute'
        return <span className={'mod ' + cls} key={k} title={modules[k]?.error ?? modules[k]?.reason ?? ''}>
          {cls === 'ok' ? '✓' : cls === 'bad' ? '✗' : '–'} {name}
        </span>
      })}
    </span>
  )
}

export function Images({ materialId }) {
  const { data } = useFetch(`/materials/${encodeURIComponent(materialId)}/images`)
  const items = data?.items ?? []
  if (!items.length) return null
  const shots = items.filter((i) => i.kind !== 'frame').slice(0, 8)
  const frames = items.filter((i) => i.kind === 'frame')
  const step = Math.max(1, Math.ceil(frames.length / 6))
  const shown = [...shots, ...frames.filter((_, i) => i % step === 0).slice(0, 6)]
  return (
    <div className="thumbs">
      {shown.map((i) => (
        <a className="thumb" key={i.n} href={imageUrl(materialId, i.n)} target="_blank" rel="noreferrer" title={`${i.label}: ${i.name}`}>
          <img src={imageUrl(materialId, i.n)} alt={`${i.label} ${i.name}`} loading="lazy" />
          <span>{i.label}</span>
        </a>
      ))}
    </div>
  )
}
