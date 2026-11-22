import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph3D from 'react-force-graph-3d'
import * as THREE from 'three'
import SpriteText from 'three-spritetext'

import { canvasImageUrl, getJSON } from './api.js'

import { Legend, radius } from './Graph.jsx'
import { PAIR_EDGES, asSelection, gravity, prepareGraph } from './graphData.js'
import { Box } from './ui.jsx'

// Widok 3D mapy kartoteki: „nocne biurko detektywa” – ciemne tło, ciepłe kolory, płynące powiązania.
const COLOR = {
  material: '#F3EBDC', campaign: '#D9C3A0', operator: '#E04848', domain: '#C8B48C',
  person: '#B08A55', account: '#E07A7A', kit: '#6FB08A', infra: '#7F9BD8',
}
const LINK = {
  ta_sama_kampania: 'rgba(243,235,220,0.85)', do_weryfikacji: 'rgba(224,170,80,0.85)',
  ten_sam_operator: 'rgba(224,72,72,0.9)', operator: 'rgba(224,72,72,0.6)',
}
const idOf = (x) => (typeof x === 'object' ? x.id : x)
const shorten = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

// Miniatura zrzutu jako krążek na kuli materiału. crossOrigin: WebGL przyjmie obraz tylko za zgodą CORS API.
const thumbs = new Map()
function loadThumb(id) {
  if (!thumbs.has(id)) {
    thumbs.set(id, getJSON(`/materials/${encodeURIComponent(id)}/images`).then((d) => {
      const items = d?.items ?? []
      const pick = items.find((i) => i.kind === 'screenshot') ?? items.find((i) => i.kind === 'image') ?? items[0]
      if (!pick) return null
      return new Promise((resolve) => {
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.onload = () => resolve(img)
        img.onerror = () => resolve(null)
        img.src = canvasImageUrl(id, pick.n)
      })
    }).catch(() => null))
  }
  return thumbs.get(id)
}

function discTexture(img) {
  const c = document.createElement('canvas')
  c.width = c.height = 128
  const x = c.getContext('2d')
  x.save()
  x.beginPath()
  x.arc(64, 64, 60, 0, Math.PI * 2)
  x.clip()
  x.fillStyle = '#F3EBDC'
  x.fillRect(0, 0, 128, 128)
  if (img) {
    const side = Math.min(img.width, img.height)
    x.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 128, 128)
  }
  x.restore()
  x.lineWidth = 7
  x.strokeStyle = '#F3EBDC'
  x.beginPath()
  x.arc(64, 64, 60, 0, Math.PI * 2)
  x.stroke()
  const t = new THREE.CanvasTexture(c)
  t.colorSpace = THREE.SRGBColorSpace
  return t
}
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

export default function Graph3D({ data, onSelect, height = 640 }) {
  const wrap = useRef(null)
  const fg = useRef(null)
  const [width, setWidth] = useState(800)
  const [hover, setHover] = useState(null)
  const graph = useMemo(() => prepareGraph(data), [data])

  const neighbors = useMemo(() => {
    const m = new Map(graph.nodes.map((n) => [n.id, new Set([n.id])]))
    for (const l of graph.links) {
      m.get(idOf(l.source))?.add(idOf(l.target))
      m.get(idOf(l.target))?.add(idOf(l.source))
    }
    return m
  }, [graph])
  const lit = hover ? neighbors.get(hover) : null

  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.floor(e.contentRect.width))))
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const g = fg.current
    if (!g) return
    g.d3Force('charge').strength((n) => (n.entity ? -60 : -260))
    g.d3Force('link').distance((l) => (PAIR_EDGES.has(l.type) ? 90 : 45))
    g.d3Force('gravity', gravity(0.08))
    const controls = g.controls()
    if (controls) { controls.autoRotate = true; controls.autoRotateSpeed = 0.5 }
  }, [graph])

  useEffect(() => {
    const t = setTimeout(() => fg.current?.zoomToFit(900, 30), 2500)
    return () => clearTimeout(t)
  }, [graph])

  const nodeObject = useCallback((n) => {
    const group = new THREE.Group()
    const r = radius(n)
    if (n.type === 'material') {
      // materiał to płaski krążek zwrócony do kamery: najpierw papier, po załadowaniu – miniatura zrzutu
      const disc = new THREE.Sprite(new THREE.SpriteMaterial({ map: discTexture(null), toneMapped: false }))
      disc.scale.set(r * 2.4, r * 2.4, 1)
      group.add(disc)
      loadThumb(n.meta.material_id ?? n.id.replace(/^material:/, '')).then((img) => {
        if (!img) return
        disc.material.map.dispose()
        disc.material.map = discTexture(img)
        disc.material.needsUpdate = true
      })
    }
    if (n.type === 'campaign' || n.type === 'operator') {
      const label = new SpriteText(shorten(n.label, 34), 8, n.type === 'operator' ? '#ff9a9a' : '#F3EBDC')
      label.fontFace = 'Patrick Hand, cursive'
      label.position.y = -(r + 11)
      label.material.toneMapped = false
      group.add(label)
    }
    return group
  }, [])

  const flyTo = (n) => {
    const g = fg.current
    if (!g) return
    const c = g.controls()
    if (c) c.autoRotate = false
    const dist = 140
    const ratio = 1 + dist / Math.max(1, Math.hypot(n.x, n.y, n.z))
    g.cameraPosition({ x: n.x * ratio, y: n.y * ratio, z: n.z * ratio }, n, 1200)
  }

  return (
    <div className="graph">
      <Box className="graph-frame dark" style={{ height }}>
        <div ref={wrap} className="graph-canvas">
          <ForceGraph3D
            ref={fg}
            graphData={graph}
            width={width}
            height={height - 12}
            controlType="orbit"
            backgroundColor="#1d1a17"
            showNavInfo={false}
            nodeRelSize={4}
            nodeVal={(n) => (radius(n) / 4) ** 3}
            nodeColor={(n) => (lit && !lit.has(n.id) ? 'rgba(120,110,100,0.25)' : COLOR[n.type] ?? '#aaa')}
            nodeOpacity={0.95}
            nodeResolution={16}
            nodeThreeObject={nodeObject}
            nodeThreeObjectExtend={(n) => n.type !== 'material'}
            nodeLabel={(n) => `<div class="tip3d"><b>${esc(n.label)}</b>${n.group ? `<br>${esc((n.meta.values ?? []).slice(0, 5).join(', '))}` : ''}</div>`}
            linkColor={(l) => (lit && !(lit.has(idOf(l.source)) && lit.has(idOf(l.target)))
              ? 'rgba(120,110,100,0.08)' : LINK[l.type] ?? 'rgba(225,215,195,0.42)')}
            linkWidth={(l) => (PAIR_EDGES.has(l.type) ? 2 : 0.5)}
            linkOpacity={0.7}
            linkDirectionalParticles={(l) => (l.type === 'ten_sam_operator' ? 4 : l.type === 'ta_sama_kampania' ? 2 : 0)}
            linkDirectionalParticleWidth={2.2}
            linkDirectionalParticleSpeed={0.006}
            linkDirectionalParticleColor={(l) => (l.type === 'ten_sam_operator' ? '#ff6b6b' : '#fff3dc')}
            onNodeHover={(n) => setHover(n?.id ?? null)}
            onNodeClick={(n) => { flyTo(n); onSelect?.(asSelection(n, false)) }}
            onLinkClick={(l) => onSelect?.(asSelection(l, true))}
            onBackgroundClick={() => onSelect?.(null)}
            cooldownTicks={160}
          />
        </div>
        <button className="graph-fit light" type="button" onClick={() => fg.current?.zoomToFit(800, 40)}>Dopasuj</button>
      </Box>
      <Legend nodes={graph.nodes} links={graph.links} />
    </div>
  )
}
