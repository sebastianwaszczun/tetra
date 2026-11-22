import { useState } from 'react'

import { postJSON } from '../lib/api.js'
import { ErrorNote } from './ui.jsx'

export default function Decision({ materialId, assignment, onDone, compact = false }) {
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
