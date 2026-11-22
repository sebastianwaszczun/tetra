import { useCallback, useEffect, useState } from 'react'

import { getJSON } from './api.js'

export const ASSIGNMENT = {
  przypisany: { label: 'przypisany', tone: 'ok' },
  potwierdzony: { label: 'potwierdzony', tone: 'ok' },
  do_weryfikacji: { label: 'do weryfikacji', tone: 'warn' },
  nowy_wzorzec: { label: 'nowy wzorzec', tone: 'mute' },
  odrzucony: { label: 'odrzucony', tone: 'bad' },
}

export const MODULES = { text: 'tekst', osint: 'OSINT', image: 'obraz', av: 'wideo i audio' }

export const PERSUASION = {
  profit: 'obietnica zysku', urgency: 'presja czasu', secrecy: 'tajemnica', sensational: 'sensacja',
  authority: 'autorytet', fear: 'strach', social_proof: '„wszyscy już zarabiają”', scarcity: 'ograniczona oferta',
}

export const SIGNALS = { text: 'tekst', osint: 'OSINT', image: 'obraz', av: 'wideo' }

export function Ink({ double = false }) {
  return (
    <>
      <span className="ink" aria-hidden="true" />
      {double && <span className="ink b" aria-hidden="true" />}
    </>
  )
}

export function Box({ as: Tag = 'div', className = '', hatch = false, double = false, children, ...rest }) {
  return (
    <Tag className={'box ' + className} {...rest}>
      {hatch && <span className="hatch box-hatch" aria-hidden="true" />}
      {children}
      <Ink double={double} />
    </Tag>
  )
}

export function Badge({ status, children }) {
  const a = ASSIGNMENT[status] ?? { label: status ?? '—', tone: 'mute' }
  return <span className={'badge ' + a.tone}>{children ?? a.label}</span>
}

export function Score({ value, label }) {
  if (value == null) return null
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <span className="score" title={label ?? `wynik ${value.toFixed(2)}`}>
      <span className="score-track"><span className="score-fill" style={{ width: `${pct}%` }} /></span>
      <span className="score-num">{value.toFixed(2)}</span>
    </span>
  )
}

export function Note({ children, tone = '' }) {
  return <p className={'note ' + tone}>{children}</p>
}

export function Loading({ text = 'Przeglądam kartotekę…' }) {
  return (
    <div className="loading-note" role="status">
      <svg aria-hidden="true" width="44" height="44" viewBox="0 0 44 44">
        <g className="mini-lens">
          <circle cx="18" cy="18" r="11" />
          <path d="M26 26 L37 37" />
        </g>
      </svg>
      <span>{text}</span>
    </div>
  )
}

export function ErrorNote({ error, retry }) {
  return (
    <div className="error-note" role="alert">
      <span>Coś poszło nie tak: {error?.message ?? String(error)}</span>
      {retry && <button className="link-btn" type="button" onClick={retry}>Spróbuj ponownie</button>}
    </div>
  )
}

export function useFetch(path) {
  const [state, setState] = useState({ data: null, error: null, loading: Boolean(path) })
  const [tick, setTick] = useState(0)
  const reload = useCallback(() => setTick((t) => t + 1), [])
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return }
    const ctrl = new AbortController()
    setState((s) => ({ ...s, loading: true, error: null }))
    getJSON(path, ctrl.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => { if (error.name !== 'AbortError') setState({ data: null, error, loading: false }) })
    return () => ctrl.abort()
  }, [path, tick])
  return { ...state, reload }
}

export function shortDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('pl-PL', { day: 'numeric', month: 'short' })
}

export function ago(iso) {
  if (!iso) return ''
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'przed chwilą'
  if (min < 60) return `${min} min temu`
  const h = Math.round(min / 60)
  if (h < 24) return `${h} h temu`
  const d = Math.round(h / 24)
  return d === 1 ? 'wczoraj' : `${d} dni temu`
}

export const plural = (n, one, few, many) => {
  const d = n % 10, dd = n % 100
  return n === 1 ? one : d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many
}

export const materialy = (n) => `${n} ${plural(n, 'materiał', 'materiały', 'materiałów')}`
