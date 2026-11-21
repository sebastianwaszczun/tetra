const IMAGE_EXT = /\.(png|jpe?g|webp|gif|bmp|heic|heif|avif|tiff?)$/i

const URL_ONLY = /^(?:https?:\/\/)?(?:www\.)?(?:[a-z0-9\u00a1-\uffff](?:[a-z0-9\u00a1-\uffff-]*[a-z0-9\u00a1-\uffff])?\.)+[a-z\u00a1-\uffff]{2,}(?::\d{2,5})?(?:[/?#]\S*)?$/i
const URL_INSIDE = /(?:https?:\/\/|www\.)\S+|\b[a-z0-9-]+\.(?:pl|com|net|org|eu|info|shop|online|xyz|top|site|store|app|io|me|ly)(?:\/\S*)?\b/i

const PRODUCT_MAX_CHARS = 60
const PRODUCT_MAX_WORDS = 8

export function resolveInput(value) {
  if (value == null) return null

  if (typeof File !== 'undefined' && value instanceof File) {
    const isImage = value.type.startsWith('image/') || IMAGE_EXT.test(value.name)
    return { input: value, type: isImage ? 'image' : 'file' }
  }

  const input = String(value).trim()
  if (!input) return null

  if (!/\s/.test(input) && URL_ONLY.test(input)) return { input, type: 'link' }

  return { input, type: looksLikeProduct(input) ? 'product' : 'text' }
}

function looksLikeProduct(s) {
  if (s.length > PRODUCT_MAX_CHARS) return false
  if (/[\r\n]/.test(s)) return false
  if (s.split(/\s+/).length > PRODUCT_MAX_WORDS) return false
  if (URL_INSIDE.test(s)) return false
  if (/[.!?…](\s|$)/.test(s)) return false
  if (/\d{6,}|\d{3}[\s-]\d{3}[\s-]\d{3}/.test(s)) return false
  if (/[:;]\s/.test(s)) return false
  return true
}

export const TYPE_LABELS = {
  image: 'obraz',
  file: 'plik',
  link: 'link',
  text: 'tekst',
  product: 'nazwa produktu',
}
