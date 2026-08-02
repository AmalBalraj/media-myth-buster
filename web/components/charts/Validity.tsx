"use client";

import { ChartFrame, useTooltip } from "@/components/Tooltip";
import type { Claim, Verdict } from "@/lib/types";
import { VERDICT_ORDER, fmt, styleFor } from "@/lib/verdicts";

/**
 * The headline is a single number, so it is a stat tile with an interval — not a
 * gauge dial. The interval is the point: a score from two scored claims must not
 * look as settled as one from ten.
 */
export function ValidityHero({
  score,
  ciLow,
  ciHigh,
  scored,
  total,
  unverifiable,
}: {
  score: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  scored: number;
  total: number;
  unverifiable: number;
}) {
  if (score === null) {
    return (
      <div>
        <div style={{ fontSize: 40, fontWeight: 600, color: "var(--text-muted)" }}>
          No score
        </div>
        <p className="sub" style={{ marginTop: 4 }}>
          {total === 0
            ? "No check-worthy factual claims were found in this video."
            : `None of the ${total} claims could be verified against evidence.`}
        </p>
      </div>
    );
  }

  const lo = ciLow ?? score;
  const hi = ciHigh ?? score;
  const pct = (v: number) => `${Math.max(0, Math.min(100, v))}%`;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontSize: 48, fontWeight: 600, lineHeight: 1.05 }}>
          {fmt(score)}
        </span>
        <span style={{ fontSize: 18, color: "var(--text-secondary)" }}>/ 100</span>
      </div>

      <svg width="100%" height="34" style={{ display: "block", marginTop: 10 }}>
        {/* track */}
        <rect x="0" y="13" width="100%" height="6" rx="3" fill="var(--gridline)" />
        {/* confidence interval */}
        <rect
          x={pct(lo)}
          y="11"
          width={pct(hi - lo)}
          height="10"
          rx="4"
          fill="var(--pole-support)"
          opacity="0.28"
        />
        {/* point estimate — 2px surface ring so it reads over the band */}
        <circle
          cx={pct(score)}
          cy="16"
          r="6"
          fill="var(--pole-support)"
          stroke="var(--surface-1)"
          strokeWidth="2"
        />
        <text x="0" y="32" fontSize="11" fill="var(--text-muted)">
          0
        </text>
        <text x="100%" y="32" fontSize="11" fill="var(--text-muted)" textAnchor="end">
          100
        </text>
      </svg>

      <p className="sub" style={{ marginTop: 2 }}>
        95% interval {fmt(lo)}–{fmt(hi)} · {scored} of {total} claims scored
        {unverifiable > 0 ? `, ${unverifiable} unverifiable` : ""}
      </p>
    </div>
  );
}

/**
 * Verdict distribution. Segments carry a 2px surface gap so adjacent fills never
 * merge, and every segment above ~7% is directly labelled — colour alone never
 * has to carry which verdict a band is.
 */
export function VerdictBar({ claims }: { claims: Claim[] }) {
  const { show, hide, node } = useTooltip();
  if (!claims.length) return null;

  const counts = VERDICT_ORDER.map((v) => ({
    verdict: v,
    n: claims.filter((c) => c.verdict === v).length,
  })).filter((d) => d.n > 0);

  const total = claims.length;
  let offset = 0;

  return (
    <ChartFrame label={`Verdict distribution across ${total} claims`}>
      <svg width="100%" height="46" style={{ display: "block" }}>
        {counts.map((d) => {
          const w = (d.n / total) * 100;
          const s = styleFor(d.verdict);
          const x = offset;
          offset += w;
          return (
            <g key={d.verdict}>
              <rect
                x={`${x}%`}
                y="0"
                width={`calc(${w}% - 2px)`}
                height="22"
                rx="4"
                fill={s.color}
                opacity={s.emphasis}
                onMouseMove={(e) =>
                  show(
                    e,
                    <>
                      <strong>{s.label}</strong> — {d.n} of {total} claims
                    </>,
                  )
                }
                onMouseLeave={hide}
              />
              {w > 7 && (
                <text
                  x={`${x + w / 2}%`}
                  y="38"
                  fontSize="11"
                  fill="var(--text-secondary)"
                  textAnchor="middle"
                >
                  {s.label} {d.n}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {node}
      {/* Legend for the segments too narrow to label in place. */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 6 }}>
        {counts
          .filter((d) => (d.n / total) * 100 <= 7)
          .map((d) => {
            const s = styleFor(d.verdict);
            return (
              <span key={d.verdict} className="chip">
                <span
                  className="dot"
                  style={{ background: s.color, opacity: s.emphasis }}
                />
                {s.label} {d.n}
              </span>
            );
          })}
      </div>
    </ChartFrame>
  );
}

/** Sub-scores. One series, so no legend box — the title names it. */
export function SubscoreRadar({
  data,
}: {
  data: { label: string; value: number | null }[];
}) {
  const { show, hide, node } = useTooltip();
  const points = data.filter((d) => d.value !== null) as {
    label: string;
    value: number;
  }[];
  if (points.length < 3) {
    return (
      <table>
        <tbody>
          {data.map((d) => (
            <tr key={d.label}>
              <td>{d.label}</td>
              <td className="num">{d.value === null ? "not measured" : fmt(d.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }

  // The viewBox is wider than the plot: labels sit outside the outermost ring and
  // would otherwise be clipped at the left and right vertices.
  const size = 300;
  const padX = 74;
  const cx = size / 2;
  const cy = size / 2 + 6;
  const r = 74;
  const angle = (i: number) => (Math.PI * 2 * i) / points.length - Math.PI / 2;
  const at = (i: number, frac: number) => [
    cx + Math.cos(angle(i)) * r * frac,
    cy + Math.sin(angle(i)) * r * frac,
  ];

  const path =
    points
      .map((d, i) => {
        const [x, y] = at(i, d.value / 100);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ") + " Z";

  return (
    <ChartFrame label="Sub-scores">
      <svg
        width="100%"
        viewBox={`${-padX} 0 ${size + padX * 2} ${size + 12}`}
        style={{ maxWidth: size + padX * 2 }}
      >
        {[0.25, 0.5, 0.75, 1].map((frac) => (
          <polygon
            key={frac}
            points={points
              .map((_, i) => at(i, frac).map((n) => n.toFixed(1)).join(","))
              .join(" ")}
            fill="none"
            stroke="var(--gridline)"
            strokeWidth="1"
          />
        ))}
        {points.map((_, i) => {
          const [x, y] = at(i, 1);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={x}
              y2={y}
              stroke="var(--gridline)"
              strokeWidth="1"
            />
          );
        })}

        <path d={path} fill="var(--pole-support)" fillOpacity="0.18" stroke="var(--pole-support)" strokeWidth="2" />

        {points.map((d, i) => {
          const [x, y] = at(i, d.value / 100);
          const [lx, ly] = at(i, 1.24);
          return (
            <g key={d.label}>
              <circle
                cx={x}
                cy={y}
                r="5"
                fill="var(--pole-support)"
                stroke="var(--surface-1)"
                strokeWidth="2"
                onMouseMove={(e) =>
                  show(e, <>{d.label}: <strong>{fmt(d.value)}</strong></>)
                }
                onMouseLeave={hide}
              />
              <text
                x={lx}
                y={ly}
                fontSize="10.5"
                fill="var(--text-secondary)"
                textAnchor={lx < cx - 6 ? "end" : lx > cx + 6 ? "start" : "middle"}
                dominantBaseline="middle"
              >
                {d.label}
              </text>
            </g>
          );
        })}
      </svg>
      {node}
    </ChartFrame>
  );
}

/** Creator track record over time. One series; the last point is direct-labelled. */
export function Sparkline({ history }: { history: { validity: number }[] }) {
  if (history.length < 2) return null;
  const pts = [...history].reverse();
  const w = 220;
  const h = 44;
  const x = (i: number) => (i / (pts.length - 1)) * (w - 8) + 4;
  const y = (v: number) => h - 6 - (v / 100) * (h - 14);

  return (
    <svg width={w} height={h} role="img" aria-label="Validity of recent reels by this creator">
      <line x1="4" y1={y(50)} x2={w - 4} y2={y(50)} stroke="var(--gridline)" strokeWidth="1" />
      <path
        d={pts.map((p, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(p.validity)}`).join(" ")}
        fill="none"
        stroke="var(--pole-support)"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <circle
        cx={x(pts.length - 1)}
        cy={y(pts[pts.length - 1].validity)}
        r="4"
        fill="var(--pole-support)"
        stroke="var(--surface-1)"
        strokeWidth="2"
      />
    </svg>
  );
}
