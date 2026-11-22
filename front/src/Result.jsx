import { MaterialFacts, Modules } from './parts.jsx'
import { href } from './route.js'
import { Box, Note, Score, materialy } from './ui.jsx'

const VERDICT = {
  przypisany: { tone: 'ok', title: 'Pasuje do znanej kampanii' },
  do_weryfikacji: { tone: 'warn', title: 'Podobne do znanej kampanii' },
  nowy_wzorzec: { tone: 'mute', title: 'Nowy wzorzec' },
}

function factsFromResponse(data) {
  const t = data.text_features ?? {}
  const im = data.image_features ?? {}
  return {
    persons: Object.keys(t.persons ?? {}),
    persons_visual: im.persons_visual ?? [],
    brands: Object.keys(t.brands ?? {}),
    features: { text: data.text_features, osint: data.osint, image: data.image_features, av: data.av_features },
  }
}

export default function Result({ data, error, onReset }) {
  if (error) {
    return (
      <Box className="result" double>
        <h2 className="result-title">Nie udało się sprawdzić materiału</h2>
        <Note tone="bad">{error.message ?? String(error)}</Note>
        <div className="result-actions"><button className="btn" type="button" onClick={onReset}>Spróbuj jeszcze raz</button></div>
      </Box>
    )
  }

  const a = data.assignment
  const v = VERDICT[a?.assignment]
  const camp = a?.campaign
  const best = a?.matches?.[0]
  const reasons = best?.reasons ?? []

  return (
    <Box className={'result ' + (v?.tone ?? 'mute')} double>
      <span className="hatch box-hatch" aria-hidden="true" />
      <div className="result-head">
        <span className={'stamp ' + (v?.tone ?? 'mute')}>{v ? v.title : 'Przeanalizowano'}</span>
        {a?.score != null && <Score value={a.score} label="podobieństwo do najbliższego materiału" />}
      </div>

      {camp && a.assignment !== 'nowy_wzorzec' && (
        <h2 className="result-title">
          <a href={href.campaign(a.campaign_id)}>{camp.label || `Kampania ${a.campaign_id}`}</a>
        </h2>
      )}
      {camp && a.assignment !== 'nowy_wzorzec' && <p className="result-sub">{camp.summary || `${materialy(camp.size)} w kartotece`}</p>}
      {a?.assignment === 'nowy_wzorzec' && (
        <p className="result-sub">Pierwszy taki materiał w kartotece – zaczyna nową kampanię. Jeśli pojawią się podobne, zostaną z nim połączone.</p>
      )}
      {a?.assignment === 'do_weryfikacji' && (
        <Note tone="warn">Podobieństwo jest średnie – o przynależności zdecyduje analityk.</Note>
      )}
      {!a && (
        <Note>{data.record_error ? `Wynik nie został zapisany w kartotece: ${data.record_error}` : 'Materiał nie został porównany z kartoteką.'}</Note>
      )}

      {reasons.length > 0 && (
        <>
          <h3 className="panel-sub">Dlaczego</h3>
          <ul className="reasons">{reasons.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}
      {best?.same_operator && best.operator_reasons?.length > 0 && (
        <>
          <h3 className="panel-sub">Ten sam operator</h3>
          <ul className="reasons">{best.operator_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}

      <h3 className="panel-sub">Co znalazł detektyw</h3>
      <MaterialFacts m={factsFromResponse(data)} />
      <div className="result-modules"><Modules modules={data.modules} /></div>

      <div className="result-actions">
        {a?.campaign_id != null && <a className="btn" href={href.campaign(a.campaign_id)}>Zobacz kampanię →</a>}
        {data.saved && <a className="btn ghost" href={href.material(data.material_id)}>Graf materiału</a>}
        <button className="link-btn" type="button" onClick={onReset}>Sprawdź kolejny</button>
      </div>
    </Box>
  )
}
