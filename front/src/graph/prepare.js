// Dane z GET /graph -> węzły i krawędzie dla react-force-graph.
// Ślady tego samego rodzaju łączące ten sam zestaw materiałów (np. 7 plików obrazu z dwóch stron)
// stają się jednym węzłem z licznikiem – graf pokazuje powiązania, a nie listę hashy.

const ENTITY = new Set(['domain', 'person', 'account', 'kit', 'infra'])
const GROUPABLE = new Set(['account', 'kit', 'infra'])
const ENTITY_EDGES = new Set(['uzywa', 'wymienia', 'pokazuje'])
export const idOf = (x) => (typeof x === 'object' ? x.id : x)
export const shorten = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
export const materialIdOf = (node) => node.meta?.material_id ?? node.id.replace(/^material:/, '')

export const PAIR_EDGES = new Set(['ta_sama_kampania', 'do_weryfikacji', 'ten_sam_operator'])

export function prepareGraph(data, { hideTypes = [] } = {}) {
  if (!data) return { nodes: [], links: [] }
  const hidden = new Set(hideTypes)
  const raw = data.nodes.filter((n) => !hidden.has(n.type))
  const byId = new Map(raw.map((n) => [n.id, n]))

  // materiały, które wskazują na dany ślad
  const users = new Map()
  for (const e of data.edges) {
    if (!ENTITY_EDGES.has(e.type) || !byId.has(e.source) || !byId.has(e.target)) continue
    if (!users.has(e.target)) users.set(e.target, new Set())
    users.get(e.target).add(e.source)
  }

  const groupOf = new Map()       // id śladu -> id węzła grupy
  const groups = new Map()
  for (const n of raw) {
    if (!GROUPABLE.has(n.type)) continue
    const mats = [...(users.get(n.id) ?? [])].sort()
    const key = `${n.type}|${n.data?.kind ?? ''}|${mats.join(',')}`
    if (!groups.has(key)) groups.set(key, { members: [], mats, type: n.type, kind: n.data?.kind ?? n.type })
    groups.get(key).members.push(n)
  }
  let gi = 0
  for (const g of groups.values()) {
    if (g.members.length < 2) continue
    const id = `grp:${gi++}`
    for (const m of g.members) groupOf.set(m.id, id)
    g.node = {
      id, type: g.type, group: true, count: g.members.length,
      label: `${g.members.length}× ${g.kind}`,
      meta: { kind: g.kind, values: g.members.map((m) => m.data?.value ?? m.label), materials: g.mats.length },
    }
  }

  const nodes = []
  for (const n of raw) {
    if (groupOf.has(n.id)) continue
    nodes.push({ id: n.id, type: n.type, label: n.label, count: 1, meta: n.data ?? {}, entity: ENTITY.has(n.type) })
  }
  for (const g of groups.values()) if (g.node) nodes.push({ ...g.node, entity: true })

  const ids = new Set(nodes.map((n) => n.id))
  const seen = new Set()
  const links = []
  for (const e of data.edges) {
    const source = groupOf.get(e.source) ?? e.source
    const target = groupOf.get(e.target) ?? e.target
    if (!ids.has(source) || !ids.has(target) || source === target) continue
    const key = `${e.type}|${source}|${target}`
    if (seen.has(key)) continue
    seen.add(key)
    links.push({ id: e.id, source, target, type: e.type, label: e.label, weight: e.weight, meta: e.data ?? {} })
  }
  return { nodes, links }
}

// Węzeł / krawędź w formacie panelu „dlaczego” (ten sam co wcześniej).
export function asSelection(obj, isLink) {
  if (isLink) {
    return {
      kind: 'edge', type: obj.type, label: obj.label, weight: obj.weight, ...obj.meta,
      source: typeof obj.source === 'object' ? obj.source.id : obj.source,
      target: typeof obj.target === 'object' ? obj.target.id : obj.target,
    }
  }
  return {
    ...obj.meta, kind: 'node', id: obj.id, type: obj.type, label: obj.label,
    entityKind: obj.meta?.kind, group: obj.group ?? false,
  }
}

// Łagodne przyciąganie do środka: rozłączne kampanie (pojedyncze materiały) nie odlatują poza kadr.
export function gravity(k = 0.06) {
  let nodes = []
  const force = (alpha) => {
    for (const n of nodes) {
      n.vx -= n.x * k * alpha
      n.vy -= n.y * k * alpha
      if (n.z !== undefined && n.vz !== undefined) n.vz -= n.z * k * alpha
    }
  }
  force.initialize = (ns) => { nodes = ns }
  return force
}
