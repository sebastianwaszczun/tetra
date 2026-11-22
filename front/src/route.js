import { useEffect, useState } from 'react'

// #/            – sprawdzanie materiału
// #/kampanie    – przegląd kampanii
// #/kampanie/7  – kampania
// #/operator/3  – operator (kampanie z tą samą infrastrukturą)
// #/material/ab – otoczenie materiału
function parse(hash) {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent)
  const [view = '', id] = parts
  if (view === 'kampanie') return id ? { view: 'campaign', id } : { view: 'campaigns' }
  if (view === 'operator' && id) return { view: 'operator', id }
  if (view === 'material' && id) return { view: 'material', id }
  return { view: 'home' }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const onHash = () => {
      setRoute(parse(window.location.hash))
      window.scrollTo(0, 0)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  return route
}

export const href = {
  home: '#/',
  campaigns: '#/kampanie',
  campaign: (id) => `#/kampanie/${id}`,
  operator: (id) => `#/operator/${id}`,
  material: (id) => `#/material/${encodeURIComponent(id)}`,
}
