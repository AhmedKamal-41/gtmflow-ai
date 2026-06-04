export function LoadingState({ text = "Loading…" }: { text?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600 shadow-card">
      <svg
        className="h-4 w-4 animate-spin text-brand-600"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeOpacity="0.2"
          strokeWidth="4"
        />
        <path d="M4 12a8 8 0 0 1 8-8" stroke="currentColor" strokeWidth="4" />
      </svg>
      {text}
    </div>
  );
}

// Shimmer block for content-shaped loading placeholders.
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-slate-200/70 ${className}`}
      aria-hidden="true"
    />
  );
}
