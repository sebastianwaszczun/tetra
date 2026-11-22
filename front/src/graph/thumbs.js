// Miniatury zrzutów materiałów dla grafów 2D i 3D (pobierane raz na materiał).
// crossOrigin: canvas i WebGL przyjmą obraz z API tylko za zgodą CORS.
import { canvasImageUrl, getJSON } from '../lib/api.js'

const cache = new Map()

function pick(items) {
  return items.find((i) => i.kind === 'screenshot') ?? items.find((i) => i.kind === 'image') ?? items[0]
}

export function loadThumb(materialId) {
  if (!cache.has(materialId)) {
    cache.set(materialId, getJSON(`/materials/${encodeURIComponent(materialId)}/images`).then((d) => {
      const item = pick(d?.items ?? [])
      if (!item) return null
      return new Promise((resolve) => {
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.onload = () => resolve(img)
        img.onerror = () => resolve(null)
        img.src = canvasImageUrl(materialId, item.n)
      })
    }).catch(() => {
      cache.delete(materialId)
      return null
    }))
  }
  return cache.get(materialId)
}
