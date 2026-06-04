// Lightweight, dependency-free SVG charts tuned for this dashboard.
// Each chart pairs the visual with a text/legend value so meaning never
// depends on color alone (WCAG), and carries an aria-label summary.

type Segment = { label: string; value: number; color: string };

// ---------------------------------------------------------------------------
// Donut: proportional split (e.g. Hot / Warm / Cold)
// ---------------------------------------------------------------------------

export function Donut({
  segments,
  centerValue,
  centerLabel,
  size = 168,
}: {
  segments: Segment[];
  centerValue: number | string;
  centerLabel: string;
  size?: number;
}) {
  const stroke = 18;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const total = segments.reduce((sum, s) => sum + s.value, 0);

  const center = size / 2;
  let offset = 0;
  const arcs = segments.map((s) => {
    const frac = total > 0 ? s.value / total : 0;
    const len = frac * c;
    const arc = (
      <circle
        key={s.label}
        cx={center}
        cy={center}
        r={r}
        fill="none"
        stroke={s.color}
        strokeWidth={stroke}
        strokeDasharray={`${len} ${c - len}`}
        strokeDashoffset={-offset}
        strokeLinecap={frac > 0 && frac < 1 ? "butt" : "round"}
      />
    );
    offset += len;
    return arc;
  });

  const summary = segments.map((s) => `${s.label} ${s.value}`).join(", ");

  return (
    <div className="flex items-center gap-6">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`${centerLabel}: ${summary}`}
        className="flex-none"
      >
        {/* Rotate only the ring so segments start at 12 o'clock. Text stays upright. */}
        <g transform={`rotate(-90 ${center} ${center})`}>
          <circle
            cx={center}
            cy={center}
            r={r}
            fill="none"
            stroke="#f1f5f9"
            strokeWidth={stroke}
          />
          {total > 0 && arcs}
        </g>
        <text
          x={center}
          y={center - 2}
          textAnchor="middle"
          className="fill-slate-900 text-2xl font-bold"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {centerValue}
        </text>
        <text
          x={center}
          y={center + 18}
          textAnchor="middle"
          className="fill-slate-400 text-[11px] font-medium uppercase tracking-wide"
        >
          {centerLabel}
        </text>
      </svg>
      <ul className="space-y-2 text-sm">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 flex-none rounded-sm"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-slate-600">{s.label}</span>
            <span className="tabular ml-auto font-semibold text-slate-900">
              {s.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Funnel: pipeline stages with conversion vs the first stage
// ---------------------------------------------------------------------------

export function Funnel({ stages }: { stages: Segment[] }) {
  const top = stages.length > 0 ? Math.max(stages[0].value, 1) : 1;

  return (
    <div
      className="space-y-2.5"
      role="img"
      aria-label={`Funnel: ${stages.map((s) => `${s.label} ${s.value}`).join(", ")}`}
    >
      {stages.map((s, i) => {
        const widthPct = Math.max((s.value / top) * 100, s.value > 0 ? 4 : 0);
        const convPct = top > 0 ? Math.round((s.value / top) * 100) : 0;
        return (
          <div key={s.label} className="flex items-center gap-3">
            <div className="w-40 flex-none text-sm text-slate-600">
              {s.label}
            </div>
            <div className="relative h-9 flex-1 overflow-hidden rounded-lg bg-slate-100">
              <div
                className="flex h-full items-center rounded-lg px-3 motion-safe:origin-left motion-safe:animate-bar-grow"
                style={{
                  width: `${widthPct}%`,
                  backgroundColor: s.color,
                  minWidth: s.value > 0 ? "2.5rem" : 0,
                }}
              >
                <span className="tabular text-sm font-semibold text-white">
                  {s.value}
                </span>
              </div>
            </div>
            <div className="tabular w-12 flex-none text-right text-xs font-medium text-slate-400">
              {i === 0 ? "100%" : `${convPct}%`}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gauge: single percentage as a semicircle
// ---------------------------------------------------------------------------

export function Gauge({
  percent,
  label,
  color = "#2563eb",
}: {
  percent: number;
  label: string;
  color?: string;
}) {
  const pct = Math.max(0, Math.min(100, percent));
  // Semicircle arc from left to right, drawn with pathLength=100.
  const d = "M 12 60 A 48 48 0 0 1 108 60";

  return (
    <div className="flex flex-col items-center">
      <svg
        width="120"
        height="68"
        viewBox="0 0 120 68"
        role="img"
        aria-label={`${label}: ${pct}%`}
      >
        <path
          d={d}
          fill="none"
          stroke="#f1f5f9"
          strokeWidth={12}
          strokeLinecap="round"
        />
        <path
          d={d}
          fill="none"
          stroke={color}
          strokeWidth={12}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${pct} 100`}
        />
        <text
          x="60"
          y="56"
          textAnchor="middle"
          className="fill-slate-900 text-xl font-bold"
          style={{ fontVariantNumeric: "tabular-nums" }}
        >
          {pct}%
        </text>
      </svg>
      <div className="mt-1 text-center text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bar: a single labeled progress bar (used in score breakdown)
// ---------------------------------------------------------------------------

export function Bar({
  percent,
  color = "bg-brand-500",
  track = "bg-slate-100",
  height = "h-2",
}: {
  percent: number;
  color?: string;
  track?: string;
  height?: string;
}) {
  const pct = Math.max(0, Math.min(100, percent));
  return (
    <div className={`overflow-hidden rounded-full ${track} ${height}`}>
      <div
        className={`h-full rounded-full ${color} motion-safe:origin-left motion-safe:animate-bar-grow`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
