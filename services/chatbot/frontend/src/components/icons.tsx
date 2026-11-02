/** Small inline stroke icons (no icon library). Decorative: buttons carry their own aria-label. */
type P = { size?: number }

function Svg({ size = 18, children }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {children}
    </svg>
  )
}

export const ArrowUp = (p: P) => <Svg {...p}><path d="M12 19V5M5 12l7-7 7 7" /></Svg>
export const Stop = (p: P) => <Svg {...p}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" /></Svg>
export const Copy = (p: P) => <Svg {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h8" /></Svg>
export const Check = (p: P) => <Svg {...p}><path d="M5 12.5l4.5 4.5L19 7.5" /></Svg>
export const ThumbUp = (p: P) => <Svg {...p}><path d="M7 11v9H4v-9h3zm0 0l4-7a2 2 0 0 1 3 2l-1 4h5a2 2 0 0 1 2 2.3l-1.2 6A2 2 0 0 1 16.8 20H7" /></Svg>
export const Flag = (p: P) => <Svg {...p}><path d="M5 21V4m0 0h11l-2 4 2 4H5" /></Svg>
export const Retry = (p: P) => <Svg {...p}><path d="M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4" /></Svg>
export const Book = (p: P) => <Svg {...p}><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5zm0 14a2 2 0 0 1 2-2h13" /></Svg>
export const Close = (p: P) => <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>
export const Logout = (p: P) => <Svg {...p}><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10" /></Svg>
