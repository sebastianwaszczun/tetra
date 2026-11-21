import { useEffect, useRef, useState } from 'react'
import { resolveInput, TYPE_LABELS } from './resolveInput.js'

import sit0 from './assets/cat/sit-0.webp'
import sit1 from './assets/cat/sit-1.webp'
import sit2 from './assets/cat/sit-2.webp'
import sit3 from './assets/cat/sit-3.webp'
import sit4 from './assets/cat/sit-4.webp'
import sit5 from './assets/cat/sit-5.webp'
import catJump from './assets/cat/jump.webp'
import catHang from './assets/cat/hang.webp'

const SIT_FRAMES = [sit0, sit1, sit2, sit3, sit4, sit5]

const STEPS = [
  'Zbieram odciski łap…',
  'Sprawdzam nadawcę i adres…',
  'Porównuję z kartoteką oszustw…',
  'Szukam haczyków w treści…',
  'Spisuję raport…',
]

const NAV = ['PRZEGLAD KAMPANII']

const CARDS = [
  {
    title: 'Obraz',
    hint: 'screen reklamy, zdjęcie',
    pick: 'image/*',
    icon: (
      <>
        <path d="M4 14 V4 H14 M50 4 H60 V14 M60 42 V52 H50 M14 52 H4 V42" />
        <rect x="13" y="12" width="38" height="32" rx="2" />
        <circle cx="23" cy="21" r="3.2" />
        <path d="M13 40 L25 29 L33 36 L39 31 L51 41" />
      </>
    ),
  },
  {
    title: 'Plik',
    hint: 'PDF, mail .eml, dokument',
    pick: '*/*',
    icon: (
      <>
        <path d="M16 4 H38 L50 16 V52 H16 Z" />
        <path d="M38 4 V16 H50" />
        <path d="M24 26 H42 M24 34 H42 M24 42 H35" />
      </>
    ),
  },
  {
    title: 'Link',
    hint: 'adres podejrzanej strony',
    icon: (
      <>
        <rect x="3" y="5" width="58" height="44" rx="3" />
        <path d="M3 16 H61" />
        <circle cx="10" cy="10.5" r="1.4" />
        <circle cx="16" cy="10.5" r="1.4" />
        <path d="M26 33 L22 37 C19 40 19 44 22 46 C24.5 48.5 28.5 48 31 45 L35 41" />
        <path d="M36 33 L40 29 C43 26 43 22 40 20 C37.5 17.5 33.5 18 31 21 L27 25" />
        <path d="M27 37 L35 29" />
      </>
    ),
  },
  {
    title: 'Tekst',
    hint: 'treść SMS-a lub maila',
    icon: (
      <>
        <rect x="3" y="14" width="40" height="28" rx="2" />
        <path d="M3 16 L23 31 L43 16" />
        <path d="M30 4 H60 V26 H50 L44 33 V26 H38" />
        <path d="M44 12 H54 M44 18 H51" />
      </>
    ),
  },
  {
    title: 'Produkt',
    hint: 'nazwa z podejrzanej oferty',
    icon: (
      <>
        <path d="M5 30 L29 5 H57 V29 L33 53 Z" />
        <circle cx="46" cy="16" r="4" />
        <path d="M22 35 L34 23 M28 41 L40 29" />
      </>
    ),
  },
]

function SvgDefs() {
  return (
    <svg className="svg-defs" aria-hidden="true" width="0" height="0">
      <filter id="rough">
        <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3" />
        <feDisplacementMap in="SourceGraphic" scale="3.5" />
      </filter>
      <filter id="rough2">
        <feTurbulence type="fractalNoise" baseFrequency="0.05" numOctaves="2" seed="11" />
        <feDisplacementMap in="SourceGraphic" scale="5" />
      </filter>
      <filter id="grain">
        <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch" />
        <feColorMatrix values="0 0 0 0 0.09  0 0 0 0 0.08  0 0 0 0 0.07  0 0 0 0.10 0" />
      </filter>
    </svg>
  )
}

function NavLink({ children, href = '#' }) {
  return (
    <a className="nl" href={href}>
      <span className="nh" aria-hidden="true" />
      {children}
      <span className="ink" aria-hidden="true" />
    </a>
  )
}

function RoughLine({ d }) {
  return (
    <svg className="rough-line" aria-hidden="true" viewBox="0 0 1000 8" preserveAspectRatio="none">
      <path d={d} />
    </svg>
  )
}

function DashedLine() {
  return (
    <svg className="dashed-line" aria-hidden="true" viewBox="0 0 600 8" preserveAspectRatio="none">
      <path d="M0 4 C150 1 300 7 600 3" />
    </svg>
  )
}

export default function App() {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const [typing, setTyping] = useState(false)
  const [frame, setFrame] = useState(0)
  const [glance, setGlance] = useState(false)
  const [phase, setPhase] = useState('idle')
  const [stepIdx, setStepIdx] = useState(0)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [resolved, setResolved] = useState(null)

  const timers = useRef({})
  const inputRef = useRef(null)
  const fileRef = useRef(null)
  const live = useRef({ focused, phase })
  live.current = { focused, phase }

  const later = (name, fn, ms) => {
    clearTimeout(timers.current[name])
    timers.current[name] = setTimeout(fn, ms)
  }

  useEffect(() => () => Object.values(timers.current).forEach(clearTimeout), [])

  useEffect(() => {
    if (!typing) return
    const id = setInterval(() => setFrame((f) => (f + 1) % 5), 140)
    return () => clearInterval(id)
  }, [typing])

  useEffect(() => {
    const id = setInterval(() => {
      if (!live.current.focused && live.current.phase === 'idle') {
        setGlance(true)
        later('glance', () => setGlance(false), 1400)
      }
    }, 5200)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (phase !== 'open') return
    const id = setInterval(() => setStepIdx((i) => i + 1), 1700)
    return () => clearInterval(id)
  }, [phase])

  useEffect(() => {
    if (!file || resolveInput(file).type !== 'image') { setPreview(null); return }
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const idle = phase === 'idle'
  const expanded = phase === 'open'
  const activeFrame = typing ? frame : focused || glance ? 5 : 0
  const barH = expanded ? 520 : 80
  const hangTop = expanded || phase === 'closing' ? 520 : 80

  const anchorCls =
    phase === 'jump' ? 'jumping'
      : phase === 'pull' || phase === 'open' ? 'hanging'
        : phase === 'closing' ? 'letgo'
          : ''

  const handleSearch = (result) => {
    setResolved(result)
    console.log('search', result)
  }

  const attach = (f) => {
    if (!f || !idle) return
    setFile(f)
    setFocused(false)
    setTyping(false)
    setGlance(true)
    later('glance', () => setGlance(false), 1400)
  }

  const clearFile = () => {
    setFile(null)
    if (fileRef.current) fileRef.current.value = ''
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const pickFile = (accept) => {
    if (!idle || !fileRef.current) return
    fileRef.current.accept = accept
    fileRef.current.click()
  }

  const onPaste = (e) => {
    const f = e.clipboardData?.files?.[0]
    if (f) { e.preventDefault(); attach(f) }
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer?.files?.[0]
    if (f) attach(f)
    else {
      const t = e.dataTransfer?.getData('text')
      if (t && idle) setQ(t)
    }
  }

  const submit = () => {
    if (phase !== 'idle') return
    const result = resolveInput(file ?? q)
    if (!result) { inputRef.current?.focus(); return }
    handleSearch(result)
    document.activeElement?.blur?.()
    setPhase('jump')
    setTyping(false)
    setFocused(false)
    setStepIdx(0)
    later('jump', () => {
      setPhase('pull')
      later('pull', () => setPhase('open'), 60)
    }, 950)
  }

  const cancel = () => {
    if (phase !== 'open') return
    setPhase('closing')
    later('close', () => setPhase('idle'), 750)
  }

  const onType = (e) => {
    if (!idle) return
    setQ(e.target.value)
    setTyping(true)
    later('typing', () => setTyping(false), 650)
  }

  return (
    <div className="page">
      <SvgDefs />
      <svg className="grain" aria-hidden="true">
        <rect width="100%" height="100%" filter="url(#grain)" />
      </svg>

      <header className="header">
        <a className="logo" href="#">TETRA</a>
        <nav className="nav" aria-label="Główna">
          {NAV.map((label) => <NavLink key={label}>{label}</NavLink>)}
        </nav>
        <RoughLine d="M0 4 C300 6 600 1 1000 4" />
      </header>

      <main className={'main' + (idle ? '' : ' deep')}>
        <div className="title-wrap">
          <h1 className="title">
            <span className="title-1">
              Węszysz{' '}
              <span className="underlined">
                podstęp?
                <svg className="underline" aria-hidden="true" viewBox="0 0 300 30" preserveAspectRatio="none">
                  <path d="M4 18 C60 8 140 6 296 12" />
                  <path d="M30 26 C110 18 190 17 270 22" strokeWidth="3" />
                </svg>
              </span>
            </span>
            <span className="title-2">Wklej, sprawdź, śpij spokojnie.</span>
          </h1>
        </div>

        <p className="lead">
          Wklej podejrzany link, SMS, e-mail lub reklamę. Nasz detektyw prześwietli każdy trop
          i w sekundę oceni, czy to bezpieczna oferta, czy próba oszustwa.
        </p>

        <div className="search-area">
          <div className="hatch" aria-hidden="true" style={{ height: barH }} />

          <div className="sitwrap">
            <div className={'sitter ' + (idle ? 'peek' : 'gone')} role="img" aria-label="Kot detektyw z notesem">
              {SIT_FRAMES.map((src, i) => (
                <img key={i} className={'frame' + (i === activeFrame ? ' on' : '')} src={src} alt="" />
              ))}
            </div>
          </div>

          <div
            className={'bar' + (focused || dragging ? ' focus' : '')}
            style={{ height: barH }}
            onDragOver={(e) => { if (idle) { e.preventDefault(); setDragging(true) } }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <div className="clip">
              <div className="search-row" role="search">
                <svg className="search-icon" aria-hidden="true" width="26" height="26" viewBox="0 0 34 34">
                  <circle cx="14" cy="14" r="9.5" />
                  <path d="M21 21 L31 31" />
                </svg>
                {file ? (
                  <div className="chip">
                    {preview
                      ? <img className="chip-thumb" src={preview} alt="" />
                      : <svg className="chip-icon" aria-hidden="true" width="22" height="22" viewBox="0 0 64 56"><path d="M16 4 H38 L50 16 V52 H16 Z" /><path d="M38 4 V16 H50" /></svg>}
                    <span className="chip-name" title={file.name}>{file.name}</span>
                    <button className="chip-x" type="button" onClick={clearFile} aria-label="Usuń plik" disabled={!idle}>×</button>
                    <span className="ink" aria-hidden="true" />
                  </div>
                ) : (
                  <>
                    <label htmlFor="hero-q" className="sr-only">Co sprawdzić?</label>
                    <input
                      ref={inputRef}
                      id="hero-q"
                      className="q"
                      type="search"
                      autoComplete="off"
                      placeholder={dragging ? 'Upuść tutaj…' : 'Wklej tekst, link, nazwę produktu albo dodaj plik…'}
                      value={q}
                      onChange={onType}
                      onPaste={onPaste}
                      onFocus={() => setFocused(true)}
                      onBlur={() => { setFocused(false); setTyping(false) }}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
                    />
                  </>
                )}
                <button className="attach" type="button" onClick={() => pickFile('*/*')} aria-label="Dodaj obraz lub plik" title="Dodaj obraz lub plik">
                  <svg aria-hidden="true" width="26" height="26" viewBox="0 0 34 34">
                    <path d="M23 10 L12 21 C9.5 23.5 9.5 27 12 28.5 C14.5 30.5 17.5 30 20 27.5 L28 19.5 C32 15.5 32 10 28.5 6.5 C25 3 19.5 3 15.5 7 L6 16.5" />
                  </svg>
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  hidden
                  onChange={(e) => attach(e.target.files?.[0])}
                />
                <button className="go" type="button" onClick={submit}>Szukaj</button>
              </div>

              <div className={'loading' + (expanded ? ' show' : '')} aria-live="polite">
                <DashedLine />
                <div className="loading-body">
                  <svg className="doc-svg" aria-hidden="true" width="230" height="140" viewBox="0 0 230 140">
                    <path d="M48 8 H160 L182 30 V132 H48 Z" fill="#FFFBF3" />
                    <path d="M160 8 V30 H182" />
                    <path d="M64 34 H140 M64 50 H166 M64 66 H150 M64 82 H166 M64 98 H128 M64 114 H156" strokeWidth="1.6" />
                    <path className="clue" d="M62 70 C90 64 120 72 152 68" stroke="#C62828" strokeWidth="3" />
                    <path className="clue c2" d="M62 102 C82 97 104 104 130 100" stroke="#C62828" strokeWidth="3" />
                    <g className="lens">
                      <circle cx="78" cy="58" r="22" fill="rgba(255,251,243,.55)" strokeWidth="3" />
                      <path d="M94 74 L114 96" strokeWidth="5" />
                      <path d="M66 50 C69 45 73 43 78 43" strokeWidth="1.6" />
                    </g>
                  </svg>
                  <div className="prog">
                    <div className="fill" />
                    <div className="ink" />
                  </div>
                  <div className="step" key={stepIdx}>{STEPS[stepIdx % STEPS.length]}</div>
                  {resolved && (
                    <div className="trop">Rozpoznany trop: <b>{TYPE_LABELS[resolved.type]}</b></div>
                  )}
                </div>
                <DashedLine />
                <div className="loading-actions">
                  <button className="cancel" type="button" onClick={cancel}>Anuluj</button>
                </div>
              </div>
            </div>
            <div className="ink" />
            <div className="ink b" />
          </div>

          <div className={'anchor ' + anchorCls} style={{ top: hangTop }} aria-hidden="true">
            <img className="j" src={catJump} alt="" />
            <img className="h" src={catHang} alt="" />
          </div>

          <div className={'cards' + (idle ? '' : ' hide')}>
            {CARDS.map((c) => (
              <button
                className="card"
                type="button"
                key={c.title}
                tabIndex={idle ? 0 : -1}
                onClick={() => (c.pick ? pickFile(c.pick) : inputRef.current?.focus())}
              >
                <span className="hatch" aria-hidden="true" />
                <svg className="card-icon" aria-hidden="true" width="56" height="49" viewBox="0 0 64 56">
                  {c.icon}
                </svg>
                <span className="card-title">{c.title}</span>
                <span className="card-hint">{c.hint}</span>
                <span className="ink" aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
      </main>

      <footer className="footer">
        <RoughLine d="M0 4 C250 2 520 7 1000 3" />
        <div className="footer-links">
          <NavLink>Polityka prywatności</NavLink>
        </div>
        <div className="logos">
          {[0, 1, 2].map((i) => (
            <div className="logo-box" key={i}>
              TETRA
              <div className="ink" aria-hidden="true" />
            </div>
          ))}
        </div>
      </footer>
    </div>
  )
}
