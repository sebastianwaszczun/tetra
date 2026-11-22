import { useState } from 'react'

import { imageUrl, postJSON } from './api.js'
import { href } from './route.js'
import {
  ASSIGNMENT, Badge, Box, ErrorNote, Loading, MODULES, Note, PERSUASION, Score, ago, materialy, useFetch,
} from './ui.jsx'

const LINK_TYPES = new Set(['ta_sama_kampania', 'do_weryfikacji', 'ten_sam_operator'])
const MODULE_ORDER = ['text', 'image', 'osint', 'av']

const strip = (id) => String(id ?? '').replace(/^[a-z]+:/, '')

// ---------------------------------------------------------------------------- „dlaczego”

// Kampania = ta sama treść (wkład `contribution`), operator = wspólna infrastruktura (`operator_contribution`:
// OSINT poza „tożsamością” i wspólne grafiki stron). Dla krawędzi operatora pokazujemy ten drugi.
function ModuleBreakdown({ a, b, operator = false }) {
  const { data, error, loading, reload } = useFetch(`/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)
  if (loading) return <Loading text="Porównuję moduły…" />
  if (error) return <ErrorNote error={error} retry={reload} />
  const key = operator ? 'operator_contribution' : 'contribution'
  const mods = MODULE_ORDER.filter((m) => data.modules?.[m] && !data.modules[m].error && data.modules[m][key] != null)
  return (
    <div className="breakdown">
      {mods.map((m) => {
        const r = data.modules[m]
        return (
          <div className="bd-row" key={m}>
            <span className="bd-name">{operator && m === 'image' ? 'wspólne grafiki stron' : operator && m === 'osint' ? 'infrastruktura (OSINT)' : MODULES[m]}</span>
            <Score value={r[key] ?? 0} label={operator ? 'wkład do wyniku „ten sam operator”' : `wkład do wyniku kampanii (surowy wynik ${(+r.score || 0).toFixed(2)})`} />
          </div>
        )
      })}
      {!mods.length && <Note>Żaden moduł nie ma cech do porównania.</Note>}
      <p className="small">
        {operator
          ? <>Treść porównana osobno: wynik kampanii {(+data.score || 0).toFixed(2)} – to różne kampanie jednego operatora.</>
          : data.same_operator ? <>Wspólna infrastruktura: {(+data.operator_score || 0).toFixed(2)} – też ten sam operator.</> : null}
      </p>
      {!operator && data.operator_reasons?.length > 0 && (
        <>
          <h4 className="panel-sub">Wspólna infrastruktura</h4>
          <ul className="reasons">{data.operator_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}
    </div>
  )
}

export function WhyPanel({ selected, labels = {}, onDecision }) {
  if (!selected) {
    return (
      <Box className="panel" double>
        <h3 className="panel-title">Dlaczego połączone?</h3>
        <Note>Kliknij linię między materiałami, żeby zobaczyć przesłanki, albo węzeł, żeby zobaczyć szczegóły.</Note>
        <ul className="hint-list">
          <li><b>gruba linia</b> – pewne powiązanie treści</li>
          <li><b>przerywana</b> – podobne, decyzja analityka</li>
          <li><b>kropkowana czerwona</b> – ten sam operator</li>
        </ul>
      </Box>
    )
  }

  if (selected.kind === 'edge' && LINK_TYPES.has(selected.type)) {
    const a = strip(selected.source), b = strip(selected.target)
    return (
      <Box className="panel" double>
        <h3 className="panel-title">{selected.type === 'ten_sam_operator' ? 'Ten sam operator' : 'Powiązanie'}</h3>
        <div className="pair">
          <a href={href.material(a)}>{labels[selected.source] ?? a}</a>
          <span className="pair-x">↔</span>
          <a href={href.material(b)}>{labels[selected.target] ?? b}</a>
        </div>
        <div className="panel-score">
          <Score value={selected.weight} />
          <Badge status={selected.type === 'do_weryfikacji' ? 'do_weryfikacji' : 'przypisany'}>
            {selected.type === 'ta_sama_kampania' ? 'ta sama kampania' : selected.type === 'do_weryfikacji' ? 'do weryfikacji' : 'ten sam operator'}
          </Badge>
        </div>
        <h4 className="panel-sub">Przesłanki</h4>
        {selected.reasons?.length
          ? <ul className="reasons">{selected.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          : <Note>Brak zapisanych przesłanek.</Note>}
        <h4 className="panel-sub">Wkład modułów</h4>
        <ModuleBreakdown a={a} b={b} operator={selected.type === 'ten_sam_operator'} key={`${a}-${b}-${selected.type}`} />
      </Box>
    )
  }

  if (selected.kind === 'node' && selected.type === 'material') {
    const id = selected.material_id ?? strip(selected.id)
    return (
      <Box className="panel" double>
        <h3 className="panel-title">Materiał</h3>
        <p className="panel-lead">{selected.title || selected.source || id}</p>
        <dl className="facts">
          {selected.domain && <><dt>domena</dt><dd>{selected.domain}</dd></>}
          <dt>status</dt><dd><Badge status={selected.assignment} /> {selected.score != null && <Score value={selected.score} />}</dd>
          {selected.created_at && <><dt>dodany</dt><dd>{ago(selected.created_at)}</dd></>}
          {selected.input_type && <><dt>wejście</dt><dd>{selected.input_type}</dd></>}
        </dl>
        {onDecision && <Decision materialId={id} assignment={selected.assignment} onDone={onDecision} compact />}
        <a className="more" href={href.material(id)}>Szczegóły materiału →</a>
      </Box>
    )
  }

  if (selected.kind === 'node' && selected.type === 'campaign') {
    return (
      <Box className="panel" double>
        <h3 className="panel-title">Kampania</h3>
        <p className="panel-lead">{selected.label}</p>
        {selected.summary && <Note>{selected.summary}</Note>}
        <a className="more" href={href.campaign(selected.campaign_id)}>Otwórz kampanię →</a>
      </Box>
    )
  }

  if (selected.kind === 'node' && selected.type === 'operator') {
    return (
      <Box className="panel" double>
        <h3 className="panel-title">{selected.label}</h3>
        <Note>Kampanie połączone wspólną infrastrukturą: pikselem, kontem reklamowym, kitem albo serwerem.</Note>
        <a className="more" href={href.operator(selected.operator_group)}>Pokaż operatora →</a>
      </Box>
    )
  }

  if (selected.kind === 'node') {
    return (
      <Box className="panel" double>
        <h3 className="panel-title">Wspólny ślad</h3>
        {selected.entityKind && <dl className="facts"><dt>rodzaj</dt><dd>{selected.entityKind}</dd></dl>}
        {selected.values?.length > 1 ? (
          <>
            <p className="panel-lead">{selected.values.length} wspólnych śladów tego rodzaju</p>
            <ul className="reasons mono">{selected.values.slice(0, 12).map((v) => <li key={v}>{v}</li>)}</ul>
            {selected.values.length > 12 && <Note>…i {selected.values.length - 12} więcej.</Note>}
          </>
        ) : <p className="panel-lead mono">{selected.value ?? selected.label}</p>}
        {selected.materials != null && <Note>Łączy: {materialy(selected.materials)}.</Note>}
      </Box>
    )
  }

  return (
    <Box className="panel" double>
      <h3 className="panel-title">{selected.label || 'Powiązanie'}</h3>
      <Note>Ślad łączący materiał z elementem infrastruktury albo osobą.</Note>
    </Box>
  )
}

// ---------------------------------------------------------------------------- decyzja analityka

export function Decision({ materialId, assignment, onDone, compact = false }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const decided = assignment === 'potwierdzony' || assignment === 'odrzucony'

  const send = (decision) => {
    setBusy(true)
    setError(null)
    postJSON(`/materials/${encodeURIComponent(materialId)}/decision`, { decision, note })
      .then((res) => { setNote(''); onDone?.(res) })
      .catch(setError)
      .finally(() => setBusy(false))
  }

  return (
    <div className={'decision' + (compact ? ' compact' : '')}>
      {!compact && <h4 className="panel-sub">Decyzja analityka</h4>}
      {!decided && !compact && (
        <input className="note-input" placeholder="Notatka (opcjonalnie)" value={note}
               onChange={(e) => setNote(e.target.value)} disabled={busy} />
      )}
      <div className="decision-btns">
        {decided ? (
          <button className="btn ghost" type="button" disabled={busy} onClick={() => send('cofnij')}>Cofnij decyzję</button>
        ) : (
          <>
            <button className="btn" type="button" disabled={busy} onClick={() => send('potwierdz')}>Potwierdź</button>
            <button className="btn ghost" type="button" disabled={busy} onClick={() => send('odrzuc')}>
              {assignment === 'do_weryfikacji' ? 'Odrzuć' : 'Wyłącz z kampanii'}
            </button>
          </>
        )}
      </div>
      {error && <ErrorNote error={error} />}
    </div>
  )
}

// ---------------------------------------------------------------------------- materiał

function Chips({ items, tone = '' }) {
  if (!items?.length) return null
  return <span className="chips">{items.map((x) => <span className={'chip-s ' + tone} key={x}>{x}</span>)}</span>
}

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

export { ASSIGNMENT }
