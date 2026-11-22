export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function parse(r) {
  const body = await r.json().catch(() => null)
  if (!r.ok) throw new Error(body?.detail ? String(body.detail) : `Błąd ${r.status}`)
  return body
}

export const getJSON = (path, signal) => fetch(`${API_URL}${path}`, { signal }).then(parse)

export const postJSON = (path, data) =>
  fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data ?? {}),
  }).then(parse)

export const imageUrl = (materialId, n) => `${API_URL}/materials/${materialId}/images/${n}`

export const canvasImageUrl = (materialId, n) => `${imageUrl(materialId, n)}?canvas=1`
