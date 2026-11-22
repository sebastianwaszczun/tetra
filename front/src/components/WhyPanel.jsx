import { href } from '../lib/route.js'
import Decision from './Decision.jsx'
import { Badge, Box, ErrorNote, Loading, MODULES, Note, Score, ago, materialy, useFetch } from './ui.jsx'

const LINK_TYPES = new Set(['ta_sama_kampania', 'do_weryfikacji', 'ten_sam_operator'])
const MODULE_ORDER = ['text', 'image', 'osint', 'av']

const strip = (id) => String(id ?? '').replace(/^[a-z]+:/, '')


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

export default function WhyPanel({ selected, labels = {}, onDecision }) {
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
