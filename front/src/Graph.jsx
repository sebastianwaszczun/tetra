import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import rough from 'roughjs'
import { forceCollide } from 'd3-force'

import { canvasImageUrl, getJSON } from './api.js'
import { PAIR_EDGES, asSelection, gravity, prepareGraph } from './graphData.js'
import { Box } from './ui.jsx'

const INK = '#161412'
const PAPER = '#F3EBDC'
const PAPER_HI = '#FFFBF3'
const RED = '#C62828'
const OCHRE = '#B7791F'

// wygląd węzłów: rozmiar (promień w jednostkach grafu), kształt i wypełnienie „ołówkiem” Rough.js
export const NODE_STYLE = {
  material: { r: 15, name: 'materiał (miniatura zrzutu)', fill: PAPER_HI, fillStyle: 'solid' },
  campaign: { r: 15, name: 'kampania (teczka)', fill: INK, fillStyle: 'hachure', gap: 2.6 },
  operator: { r: 15, name: 'operator', fill: RED, fillStyle: 'solid' },
  domain: { r: 9, name: 'domena', fill: '#C8B48C', fillStyle: 'hachure', gap: 2.4 },
  person: { r: 8.5, name: 'osoba', fill: '#B08A55', fillStyle: 'cross-hatch', gap: 2.8 },
  account: { r: 9, name: 'konto: piksel, analityka, kontakt', fill: RED, fillStyle: 'hachure', gap: 2.2 },
  kit: { r: 9, name: 'element kitu', fill: '#3F7A5A', fillStyle: 'hachure', gap: 2.4 },
  infra: { r: 9, name: 'infrastruktura', fill: '#3B5B9A', fillStyle: 'zigzag', gap: 2.8 },
}

export const EDGE_STYLE = {
  ta_sama_kampania: { color: INK, width: 2.6, dash: null, name: 'ta sama kampania (≥ 0,75)' },
  do_weryfikacji: { color: OCHRE, width: 2, dash: [5, 4], name: 'do weryfikacji (0,5–0,75)' },
  ten_sam_operator: { color: RED, width: 1.8, dash: [2, 3], name: 'ten sam operator' },
  w_kampanii: { color: '#a89f92', width: 0.8, dash: null, name: 'materiał w kampanii' },
  operator: { color: RED, width: 1, dash: [2, 3], name: 'kampania operatora' },
  uzywa: { color: '#b3a999', width: 0.7, dash: null, name: 'używa śladu' },
  wymienia: { color: '#b3a999', width: 0.7, dash: [3, 3], name: 'wymienia osobę' },
  pokazuje: { color: '#b3a999', width: 0.7, dash: [3, 3], name: 'pokazuje osobę' },
}

const STRONG = new Set(['material', 'campaign', 'operator'])
export const radius = (n) => (NODE_STYLE[n.type]?.r ?? 9) * (n.count > 1 ? 1 + 0.32 * Math.log2(n.count) : 1)
const idOf = (x) => (typeof x === 'object' ? x.id : x)

function hash(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (Math.abs(h) % 100000) + 1
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`
}

const shorten = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

// ---------------------------------------------------------------- kształty Rough.js (liczone raz, rysowane co klatkę)

const gen = rough.generator()
const drawables = new Map()
const painters = new WeakMap()
const painter = (canvas) => painters.get(canvas) ?? painters.set(canvas, rough.canvas(canvas)).get(canvas)

function shape(type, r, seed, outline = false) {
  const key = `${type}|${r.toFixed(1)}|${seed}|${outline}`
  if (drawables.has(key)) return drawables.get(key)
  const st = NODE_STYLE[type] ?? NODE_STYLE.kit
  const o = {
    seed, stroke: INK, strokeWidth: STRONG.has(type) ? 1.5 : 1.1, roughness: 1.25, bowing: 1.2,
    ...(outline ? {} : { fill: st.fill, fillStyle: st.fillStyle, hachureGap: st.gap ?? 4, fillWeight: 0.8 }),
  }
  let d
  if (type === 'material' || type === 'person') d = gen.circle(0, 0, r * 2, o)
  else if (type === 'operator') d = gen.polygon([[0, -r * 1.15], [r * 1.15, 0], [0, r * 1.15], [-r * 1.15, 0]], o)
  else if (type === 'campaign') {
    d = gen.path(`M ${-r * 1.15} ${-r * 0.55} L ${-r * 0.35} ${-r * 0.55} L ${-r * 0.1} ${-r * 0.85} L ${r * 1.15} ${-r * 0.85} `
      + `L ${r * 1.15} ${r * 0.8} L ${-r * 1.15} ${r * 0.8} Z`, o)
  } else if (type === 'kit') {
    d = gen.polygon([0, 1, 2, 3, 4, 5].map((i) => [r * Math.cos((Math.PI / 3) * i), r * Math.sin((Math.PI / 3) * i)]), o)
  } else if (type === 'account') d = gen.polygon([[-r, -r * 0.7], [r * 0.45, -r * 0.7], [r * 1.1, 0], [r * 0.45, r * 0.7], [-r, r * 0.7]], o)
  else if (type === 'domain') d = gen.rectangle(-r * 1.2, -r * 0.8, r * 2.4, r * 1.6, o)
  else d = gen.rectangle(-r, -r, r * 2, r * 2, o)
  drawables.set(key, d)
  return d
}

function ring(r, color, dash, seed) {
  const key = `ring|${r.toFixed(1)}|${color}|${dash}|${seed}`
  if (!drawables.has(key)) {
    drawables.set(key, gen.circle(0, 0, r * 2, { seed, stroke: color, strokeWidth: 1.8, roughness: 1.6,
                                                strokeLineDash: dash ?? undefined }))
  }
  return drawables.get(key)
}

// ---------------------------------------------------------------- miniatury zrzutów materiałów

export function useThumbs(nodes, max = 40) {
  const [tick, setTick] = useState(0)
  const cache = useRef(new Map())
  const ids = nodes.filter((n) => n.type === 'material').slice(0, max)
    .map((n) => n.meta.material_id ?? n.id.replace(/^material:/, ''))
  const key = ids.join(',')
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  useEffect(() => {
    for (const id of ids) {
      if (cache.current.has(id)) continue
      cache.current.set(id, null)       // pobieranie w toku – nie pytamy drugi raz
      getJSON(`/materials/${encodeURIComponent(id)}/images`).then((d) => {
        const items = d?.items ?? []
        const pick = items.find((i) => i.kind === 'screenshot') ?? items.find((i) => i.kind === 'image') ?? items[0]
        if (!pick) return
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.onload = () => { cache.current.set(id, img); if (mounted.current) setTick((t) => t + 1) }
        img.src = canvasImageUrl(id, pick.n)
      }).catch(() => cache.current.delete(id))
    }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  return [cache.current, tick]
}

// ---------------------------------------------------------------- legenda

function LegendIcon({ type }) {
  const st = NODE_STYLE[type]
  const common = { fill: st.fill, stroke: INK, strokeWidth: 1.4 }
  const hatch = st.fillStyle !== 'solid' ? { fill: `url(#lg-${type})` } : {}
  return (
    <svg width="20" height="18" viewBox="-10 -9 20 18" aria-hidden="true">
      <defs>
        <pattern id={`lg-${type}`} width="3" height="3" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="3" height="3" fill={PAPER_HI} />
          <line x1="0" y1="0" x2="0" y2="3" stroke={st.fill} strokeWidth="1.6" />
        </pattern>
      </defs>
      {type === 'material' || type === 'person' ? <circle r="7" {...common} {...hatch} />
        : type === 'operator' ? <polygon points="0,-8 8,0 0,8 -8,0" {...common} />
          : type === 'campaign' ? <path d="M-8 -4 L-3 -4 L-1 -6 L8 -6 L8 6 L-8 6 Z" {...common} {...hatch} />
            : type === 'kit' ? <polygon points="7,0 3.5,6 -3.5,6 -7,0 -3.5,-6 3.5,-6" {...common} {...hatch} />
              : type === 'account' ? <polygon points="-7,-5 3,-5 8,0 3,5 -7,5" {...common} {...hatch} />
                : <rect x="-7" y="-5" width="14" height="10" {...common} {...hatch} />}
    </svg>
  )
}

export function Legend({ nodes, links }) {
  const nt = [...new Set(nodes.map((n) => n.type))].filter((t) => NODE_STYLE[t])
  const et = [...new Set(links.map((l) => l.type))].filter((t) => EDGE_STYLE[t])
  return (
    <div className="legend">
      {nt.map((t) => <span key={t} className="legend-item"><LegendIcon type={t} />{NODE_STYLE[t].name}</span>)}
      {et.map((t) => {
        const st = EDGE_STYLE[t]
        const style = st.dash ? (st.dash[0] <= 2 ? 'dotted' : 'dashed') : 'solid'
        return (
          <span key={t} className="legend-item">
            <i className="sw-edge" style={{ borderTop: `${Math.max(2, st.width)}px ${style} ${st.color}` }} />{st.name}
          </span>
        )
      })}
      {nodes.some((n) => n.group) && <span className="legend-item"><b>N×</b>&nbsp;zgrupowane ślady tego samego rodzaju</span>}
    </div>
  )
}

// ---------------------------------------------------------------- graf 2D

export default function Graph({ data, onSelect, hideTypes = [], focusId = null, height = 560 }) {
  const wrap = useRef(null)
  const fg = useRef(null)
  const fitted = useRef(false)
  const [width, setWidth] = useState(800)
  const [hover, setHover] = useState(null)
  const [active, setActive] = useState(null)       // { node } albo { link }

  const graph = useMemo(() => prepareGraph(data, { hideTypes }), [data, hideTypes.join()]) // eslint-disable-line react-hooks/exhaustive-deps
  const [thumbs, thumbTick] = useThumbs(graph.nodes)

  const neighbors = useMemo(() => {
    const m = new Map(graph.nodes.map((n) => [n.id, new Set([n.id])]))
    for (const l of graph.links) {
      m.get(idOf(l.source))?.add(idOf(l.target))
      m.get(idOf(l.target))?.add(idOf(l.source))
    }
    return m
  }, [graph])

  const lit = useMemo(() => {
    if (active?.link) return new Set([idOf(active.link.source), idOf(active.link.target)])
    const id = active?.node?.id ?? hover
    return id ? neighbors.get(id) ?? null : null
  }, [active, hover, neighbors])

  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.floor(e.contentRect.width))))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    fitted.current = false
    setActive(null)
    const g = fg.current
    if (!g) return
    g.d3Force('charge').strength((n) => (STRONG.has(n.type) ? -420 : -140))
    g.d3Force('link')
      .distance((l) => (PAIR_EDGES.has(l.type) ? 150 : l.type === 'w_kampanii' || l.type === 'operator' ? 110 : 58))
      .strength((l) => (PAIR_EDGES.has(l.type) ? 0.25 : 0.7))
    g.d3Force('collide', forceCollide((n) => radius(n) + 10))
    g.d3Force('gravity', gravity(0.07))
    g.d3ReheatSimulation()
    const t = setTimeout(() => g.zoomToFit(400, 46), 1600)
    return () => clearTimeout(t)
  }, [graph])

  const drawNode = useCallback((node, ctx, scale) => {
    const r = radius(node)
    const seed = hash(node.id)
    ctx.save()
    ctx.globalAlpha = lit && !lit.has(node.id) ? 0.16 : 1
    ctx.translate(node.x, node.y)
    const rc = painter(ctx.canvas)
    rc.draw(shape(node.type, r, seed))

    if (node.type === 'material') {
      const img = thumbs.get(node.meta.material_id ?? node.id.replace(/^material:/, ''))
      if (img) {
        const side = Math.min(img.width, img.height)
        ctx.save()
        ctx.beginPath()
        ctx.arc(0, 0, r - 1.6, 0, Math.PI * 2)
        ctx.clip()
        ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, -r, -r, r * 2, r * 2)
        ctx.restore()
        rc.draw(shape('material', r, seed, true))
      }
      const a = node.meta.assignment
      if (a === 'do_weryfikacji') rc.draw(ring(r + 4, OCHRE, [4, 3], seed))
      if (a === 'odrzucony') rc.draw(ring(r + 4, '#8a8279', [1.5, 3], seed))
    }
    if (node.id === focusId || active?.node?.id === node.id) rc.draw(ring(r + 7, RED, null, seed + 7))

    if (node.count > 1) {
      ctx.fillStyle = PAPER_HI
      ctx.strokeStyle = INK
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.arc(r * 0.85, -r * 0.85, 6.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.fillStyle = INK
      ctx.font = '8px "Patrick Hand", cursive'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(String(node.count), r * 0.85, -r * 0.85 + 0.5)
    }

    const showLabel = STRONG.has(node.type) || (lit && lit.has(node.id)) || scale > 2.2
    if (showLabel && node.label) {
      const fs = Math.max(4, (STRONG.has(node.type) ? 13 : 11.5) / scale)
      const text = shorten(node.label, STRONG.has(node.type) ? 34 : 30)
      ctx.font = `${fs}px "Patrick Hand", cursive`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      const w = ctx.measureText(text).width
      const y = r + (node.type === 'operator' ? 6 : 4)
      ctx.fillStyle = rgba(PAPER, 0.86)
      ctx.fillRect(-w / 2 - 2 / scale, y - 1 / scale, w + 4 / scale, fs + 2 / scale)
      ctx.fillStyle = STRONG.has(node.type) ? INK : '#4a443d'
      ctx.fillText(text, 0, y)
    }
    ctx.restore()
  }, [lit, thumbs, thumbTick, focusId, active]) // eslint-disable-line react-hooks/exhaustive-deps

  const linkLit = useCallback((l) => !lit || (lit.has(idOf(l.source)) && lit.has(idOf(l.target))), [lit])

  const drawLinkLabel = useCallback((link, ctx, scale) => {
    if (!PAIR_EDGES.has(link.type) || link.weight == null || !linkLit(link)) return
    const s = link.source, t = link.target
    if (typeof s !== 'object' || typeof t !== 'object') return
    const x = (s.x + t.x) / 2 + (t.y - s.y) * 0.1        // środek łuku przy krzywiźnie 0.2
    const y = (s.y + t.y) / 2 - (t.x - s.x) * 0.1
    const fs = Math.max(4, 10.5 / scale)
    const text = link.weight.toFixed(2)
    ctx.font = `${fs}px "Patrick Hand", cursive`
    const w = ctx.measureText(text).width
    ctx.fillStyle = PAPER_HI
    ctx.fillRect(x - w / 2 - 2 / scale, y - fs / 2 - 1 / scale, w + 4 / scale, fs + 2 / scale)
    ctx.fillStyle = EDGE_STYLE[link.type].color
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, x, y)
  }, [linkLit])

  return (
    <div className="graph">
      <Box className="graph-frame" style={{ height }}>
        <div ref={wrap} className="graph-canvas" aria-label="Graf powiązań" role="img">
          {graph.nodes.length > 0 && (
            <ForceGraph2D
              ref={fg}
              graphData={graph}
              width={width}
              height={height - 12}
              backgroundColor="rgba(0,0,0,0)"
              nodeRelSize={1}
              nodeVal={(n) => radius(n) ** 2}
              nodeCanvasObject={drawNode}
              nodePointerAreaPaint={(n, color, ctx) => {
                ctx.fillStyle = color
                ctx.beginPath()
                ctx.arc(n.x, n.y, radius(n) + 3, 0, Math.PI * 2)
                ctx.fill()
              }}
              nodeLabel={(n) => (n.group ? `${n.label}: ${(n.meta.values ?? []).slice(0, 6).join(', ')}` : '')}
              linkColor={(l) => rgba(EDGE_STYLE[l.type]?.color ?? '#b3a999', linkLit(l) ? 0.9 : 0.08)}
              linkWidth={(l) => (EDGE_STYLE[l.type]?.width ?? 0.7) * (PAIR_EDGES.has(l.type) && l.weight ? 0.6 + l.weight * 0.6 : 1)}
              linkLineDash={(l) => EDGE_STYLE[l.type]?.dash ?? null}
              linkCurvature={(l) => (PAIR_EDGES.has(l.type) ? 0.2 : 0)}
              linkDirectionalParticles={(l) => (l.type === 'ten_sam_operator' ? 3 : l.type === 'ta_sama_kampania' ? 2 : 0)}
              linkDirectionalParticleWidth={(l) => (linkLit(l) ? 3 : 0)}
              linkDirectionalParticleSpeed={0.005}
              linkDirectionalParticleColor={(l) => EDGE_STYLE[l.type]?.color ?? INK}
              linkCanvasObjectMode={() => 'after'}
              linkCanvasObject={drawLinkLabel}
              linkHoverPrecision={6}
              onNodeHover={(n) => setHover(n?.id ?? null)}
              onNodeClick={(n) => { setActive({ node: n }); onSelect?.(asSelection(n, false)) }}
              onLinkClick={(l) => { setActive({ link: l }); onSelect?.(asSelection(l, true)) }}
              onBackgroundClick={() => { setActive(null); onSelect?.(null) }}
              cooldownTicks={220}
              d3VelocityDecay={0.32}
              onEngineStop={() => { if (!fitted.current) { fitted.current = true; fg.current?.zoomToFit(500, 46) } }}
            />
          )}
        </div>
        <button className="graph-fit" type="button" onClick={() => fg.current?.zoomToFit(500, 46)}>Dopasuj</button>
        {!graph.nodes.length && <div className="graph-empty">Brak powiązań do pokazania.</div>}
      </Box>
      <Legend nodes={graph.nodes} links={graph.links} />
    </div>
  )
}
