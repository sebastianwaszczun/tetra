export function SvgDefs() {
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

export function NavLink({ children, href = '#', active = false }) {
  return (
    <a className={'nl' + (active ? ' active' : '')} href={href} aria-current={active ? 'page' : undefined}>
      <span className="nh" aria-hidden="true" />
      {children}
      <span className="ink" aria-hidden="true" />
    </a>
  )
}

export function RoughLine({ d }) {
  return (
    <svg className="rough-line" aria-hidden="true" viewBox="0 0 1000 8" preserveAspectRatio="none">
      <path d={d} />
    </svg>
  )
}

export function DashedLine() {
  return (
    <svg className="dashed-line" aria-hidden="true" viewBox="0 0 600 8" preserveAspectRatio="none">
      <path d="M0 4 C150 1 300 7 600 3" />
    </svg>
  )
}
