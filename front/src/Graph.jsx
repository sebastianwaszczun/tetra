import { useEffect, useMemo, useRef } from 'react'
import cytoscape from 'cytoscape'

import { Box } from './ui.jsx'

const INK = '#161412'
const PAPER = '#FFFBF3'
const RED = '#C62828'
const OCHRE = '#B7791F'

export const NODE_STYLE = {
  material: { shape: 'ellipse', size: 30, bg: PAPER, name: 'materiał' },
  campaign: { shape: 'round-rectangle', size: 40, bg: INK, name: 'kampania' },
  operator: { shape: 'diamond', size: 46, bg: RED, name: 'operator' },
  domain: { shape: 'rectangle', size: 22, bg: '#E6D9C1', name: 'domena' },
  person: { shape: 'ellipse', size: 24, bg: '#D9C3A0', name: 'osoba' },
  account: { shape: 'tag', size: 24, bg: '#F0C2BC', name: 'konto (piksel, analityka, kontakt)' },
  kit: { shape: 'hexagon', size: 24, bg: '#C9DDD0', name: 'element kitu' },
  infra: { shape: 'barrel', size: 24, bg: '#CCD6EA', name: 'infrastruktura' },
}

export const EDGE_STYLE = {
  ta_sama_kampania: { color: INK, style: 'solid', width: 4, name: 'ta sama kampania (≥ 0,75)' },
  do_weryfikacji: { color: OCHRE, style: 'dashed', width: 3, name: 'do weryfikacji (0,5–0,75)' },
  ten_sam_operator: { color: RED, style: 'dotted', width: 3, name: 'ten sam operator' },
  w_kampanii: { color: '#a89f92', style: 'solid', width: 1, name: 'materiał w kampanii' },
  operator: { color: RED, style: 'dotted', width: 1.5, name: 'kampania operatora' },
  uzywa: { color: '#b3a999', style: 'solid', width: 1, name: 'używa śladu' },
  wymienia: { color: '#b3a999', style: 'dashed', width: 1, name: 'wymienia osobę' },
  pokazuje: { color: '#b3a999', style: 'dashed', width: 1, name: 'pokazuje osobę' },
}

const STRONG = new Set(['material', 'campaign', 'operator'])
const LEVEL = { material: 3, campaign: 2, operator: 2 }

const LAYOUTS = {
  cose: { name: 'cose', animate: false, nodeRepulsion: () => 14000, idealEdgeLength: () => 100, padding: 30,
          randomize: false, nodeOverlap: 20, componentSpacing: 80 },
  // materiały w środku, wspólne ślady na okręgu wokół – czytelne dla jednej kampanii albo materiału
  concentric: { name: 'concentric', animate: false, padding: 30, minNodeSpacing: 56, startAngle: 3 / 2 * Math.PI,
                concentric: (n) => LEVEL[n.data('type')] ?? 1, levelWidth: () => 1 },
}

function stylesheet() {
  const s = [
    {
      selector: 'node',
      style: {
        label: 'data(label)', 'font-family': 'Patrick Hand, cursive', 'font-size': 13, color: INK,
        'text-wrap': 'ellipsis', 'text-max-width': 150, 'text-valign': 'bottom', 'text-margin-y': 5,
        'border-width': 2, 'border-color': INK, 'background-color': PAPER, width: 26, height: 26,
        'text-background-color': '#F3EBDC', 'text-background-opacity': 0.75, 'text-background-padding': 1,
      },
    },
    {
      selector: 'edge',
      style: {
        'curve-style': 'bezier', width: 1, 'line-color': '#b3a999', opacity: 0.9,
        'font-family': 'Patrick Hand, cursive', 'font-size': 12, color: INK,
      },
    },
  ]
  for (const [type, st] of Object.entries(NODE_STYLE)) {
    s.push({
      selector: `node[type = "${type}"]`,
      style: {
        shape: st.shape, width: st.size, height: st.size, 'background-color': st.bg,
        ...(STRONG.has(type) ? {} : { label: '', 'font-size': 12, color: '#4a443d', 'text-max-width': 160 }),
      },
    })
  }
  for (const [type, st] of Object.entries(EDGE_STYLE)) {
    s.push({
      selector: `edge[type = "${type}"]`,
      style: { 'line-color': st.color, 'line-style': st.style, width: st.width },
    })
  }
  s.push(
    { selector: 'edge[type = "ta_sama_kampania"], edge[type = "do_weryfikacji"], edge[type = "ten_sam_operator"]',
      style: { label: 'data(label)', 'text-background-color': '#F3EBDC', 'text-background-opacity': 1,
               'text-background-padding': 2 } },
    { selector: 'node[assignment = "do_weryfikacji"]', style: { 'border-color': OCHRE, 'border-style': 'dashed', 'border-width': 3 } },
    { selector: 'node[assignment = "odrzucony"]', style: { 'background-color': '#E2DDD4', 'border-style': 'dotted' } },
    { selector: 'node[type = "material"]', style: { width: 38, height: 38, 'font-size': 14, 'text-max-width': 170 } },
    { selector: 'node.lbl', style: { label: 'data(label)', 'z-index': 20 } },
    { selector: 'node[?focus]', style: { 'border-width': 4, 'border-color': RED } },
    { selector: ':selected', style: { 'border-color': RED, 'border-width': 4, 'line-color': RED, 'z-index': 10 } },
    { selector: '.faded', style: { opacity: 0.18 } },
  )
  return s
}

export default function Graph({ data, onSelect, hideTypes = [], layout = 'cose', focusId = null, height = 560 }) {
  const box = useRef(null)
  const cy = useRef(null)
  const select = useRef(onSelect)
  select.current = onSelect

  const elements = useMemo(() => {
    if (!data) return []
    const nodes = data.nodes.filter((n) => !hideTypes.includes(n.type))
    const ids = new Set(nodes.map((n) => n.id))
    return [
      ...nodes.map((n) => ({
        data: { ...n.data, id: n.id, type: n.type, label: n.label, focus: n.id === focusId || undefined },
      })),
      ...data.edges
        .filter((e) => ids.has(e.source) && ids.has(e.target))
        .map((e) => ({ data: { ...e.data, id: e.id, source: e.source, target: e.target, type: e.type,
                               label: e.label, weight: e.weight } })),
    ]
  }, [data, hideTypes.join(), focusId])

  useEffect(() => {
    if (!box.current) return
    const g = cytoscape({
      container: box.current,
      elements,
      style: stylesheet(),
      layout: LAYOUTS[layout] ?? LAYOUTS.cose,
      minZoom: 0.2, maxZoom: 3,
    })
    g.on('mouseover', 'node', (ev) => ev.target.addClass('lbl'))
    g.on('mouseout', 'node', (ev) => { if (!ev.target.selected() && !ev.target.data('keep')) ev.target.removeClass('lbl') })
    g.on('tap', 'node, edge', (ev) => {
      const el = ev.target
      g.elements().addClass('faded')
      g.nodes().removeClass('lbl').data('keep', false)
      const near = el.isEdge() ? el.connectedNodes() : el.closedNeighborhood().nodes()
      el.closedNeighborhood().removeClass('faded')
      near.removeClass('faded').addClass('lbl').data('keep', true)
      select.current?.({ ...el.data(), entityKind: el.data('kind'), kind: el.isEdge() ? 'edge' : 'node' })
    })
    g.on('tap', (ev) => {
      if (ev.target === g) {
        g.elements().removeClass('faded')
        g.nodes().removeClass('lbl').data('keep', false)
        select.current?.(null)
      }
    })
    document.fonts?.ready.then(() => g.style().update())
    cy.current = g
    box.current.cy = g        // dostęp z testów i konsoli przeglądarki
    return () => { g.destroy(); cy.current = null }
  }, [elements, layout])

  const present = useMemo(() => ({
    nodes: [...new Set(elements.filter((e) => !e.data.source).map((e) => e.data.type))],
    edges: [...new Set(elements.filter((e) => e.data.source).map((e) => e.data.type))],
  }), [elements])

  return (
    <div className="graph">
      <Box className="graph-frame" style={{ height }}>
        <div ref={box} className="graph-canvas" aria-label="Graf powiązań" role="img" />
        <button className="graph-fit" type="button" onClick={() => cy.current?.fit(undefined, 30)}>Dopasuj</button>
        {!elements.length && <div className="graph-empty">Brak powiązań do pokazania.</div>}
      </Box>
      <div className="legend">
        {present.nodes.map((t) => NODE_STYLE[t] && (
          <span key={t} className="legend-item" title={data?.legend?.nodes?.[t]}>
            <i className={'sw-node ' + t} style={{ background: NODE_STYLE[t].bg }} />{NODE_STYLE[t].name}
          </span>
        ))}
        {present.edges.map((t) => EDGE_STYLE[t] && (
          <span key={t} className="legend-item" title={data?.legend?.edges?.[t]}>
            <i className="sw-edge" style={{ borderTop: `${Math.max(2, EDGE_STYLE[t].width)}px ${EDGE_STYLE[t].style} ${EDGE_STYLE[t].color}` }} />
            {EDGE_STYLE[t].name}
          </span>
        ))}
      </div>
    </div>
  )
}
