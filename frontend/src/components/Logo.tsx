// The GTMFlow mark (same artwork as src/app/icon.svg): lead lines converging.
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden>
      <defs>
        <linearGradient id="gtmflow-logo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3b82f6" />
          <stop offset="1" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#gtmflow-logo)" />
      <g fill="none" stroke="#fff" strokeLinecap="round" strokeWidth="5">
        <path d="M14 20h22" opacity=".55" />
        <path d="M14 32h26" />
        <path d="M14 44h22" opacity=".55" />
        <path d="M36 20c6 0 6 12 12 12M36 44c6 0 6-12 12-12" opacity=".8" strokeWidth="3.5" />
      </g>
      <circle cx="50" cy="32" r="5" fill="#fff" />
    </svg>
  );
}
