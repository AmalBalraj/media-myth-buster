"use client";

import { ChartFrame, useTooltip } from "@/components/Tooltip";
import type { Claim, ForensicSignal } from "@/lib/types";
import { fmt, styleFor } from "@/lib/verdicts";

/**
 * Claims plotted where they occur in the video. This is the one view that shows
 * *when* a video goes wrong — a reel whose false claims cluster in the last ten
 * seconds reads very differently from one that opens with them.
 */
export function ClaimTimeline({
  claims,
  duration,
  onSelect,
}: {
  claims: Claim[];
  duration: number;
  onSelect?: (id: string) => void;
}) {
  const { show, hide, node } = useTooltip();
  const placed = claims.filter((c) => c.t_start !== null);
  if (!placed.length || duration <= 0) return null;

  const h = 78;
  const lane = (i: number) => 18 + (i % 3) * 15;

  return (
    <ChartFrame label="Claims over the duration of the video">
      <svg width="100%" height={h} style={{ display: "block" }}>
        <line x1="0" y1={h - 16} x2="100%" y2={h - 16} stroke="var(--baseline)" strokeWidth="1" />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line
              x1={`${f * 100}%`}
              y1="8"
              x2={`${f * 100}%`}
              y2={h - 16}
              stroke="var(--gridline)"
              strokeWidth="1"
            />
            <text
              x={`${f * 100}%`}
              y={h - 3}
              fontSize="10.5"
              fill="var(--text-muted)"
              textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}
            >
              {(duration * f).toFixed(0)}s
            </text>
          </g>
        ))}

        {placed.map((c, i) => {
          const s = styleFor(c.verdict);
          const left = Math.min((c.t_start! / duration) * 100, 99);
          const width = Math.max(
            (((c.t_end ?? c.t_start! + 2) - c.t_start!) / duration) * 100,
            1.2,
          );
          const y = lane(i);
          return (
            <g
              key={c.id}
              style={{ cursor: onSelect ? "pointer" : "default" }}
              onClick={() => onSelect?.(c.id)}
              onMouseMove={(e) =>
                show(
                  e,
                  <>
                    <strong>{s.label}</strong> · {c.t_start!.toFixed(1)}s
                    <br />
                    {c.text}
                  </>,
                )
              }
              onMouseLeave={hide}
            >
              {/* invisible fat hit target — the visible mark is deliberately thin */}
              <rect
                x={`${left}%`}
                y={y - 7}
                width={`max(${width}%, 14px)`}
                height="18"
                fill="transparent"
              />
              <rect
                x={`${left}%`}
                y={y}
                width={`calc(max(${width}%, 10px) - 2px)`}
                height="9"
                rx="4"
                fill={s.color}
                opacity={s.emphasis}
                stroke="var(--surface-1)"
                strokeWidth="2"
              />
            </g>
          );
        })}
      </svg>
      {node}
    </ChartFrame>
  );
}

/**
 * Political lean on two axes with a confidence box. One axis would be US-centric
 * and would lose information on non-US content, so the plot is 2-D and the box
 * shows how little we are claiming.
 */
export function LeanPlot({
  economic,
  social,
  confidence,
  sourceMixLean,
}: {
  economic: number;
  social: number;
  confidence: number;
  sourceMixLean: number | null;
}) {
  const { show, hide, node } = useTooltip();
  const size = 250;
  const pad = 26;
  const span = size - pad * 2;
  const at = (v: number) => pad + ((v + 1) / 2) * span;
  const spread = (1 - Math.max(confidence, 0.05)) * span * 0.42;

  return (
    <ChartFrame label="Political framing on economic and social axes">
      <svg width="100%" viewBox={`0 0 ${size} ${size}`} style={{ maxWidth: size }}>
        <rect
          x={pad}
          y={pad}
          width={span}
          height={span}
          fill="none"
          stroke="var(--gridline)"
          strokeWidth="1"
        />
        <line x1={at(0)} y1={pad} x2={at(0)} y2={size - pad} stroke="var(--baseline)" strokeWidth="1" />
        <line x1={pad} y1={at(0)} x2={size - pad} y2={at(0)} stroke="var(--baseline)" strokeWidth="1" />

        <text x={pad} y={pad - 9} fontSize="10.5" fill="var(--text-muted)">
          ← economic left
        </text>
        <text x={size - pad} y={pad - 9} fontSize="10.5" fill="var(--text-muted)" textAnchor="end">
          economic right →
        </text>
        <text x={pad - 6} y={pad + 4} fontSize="10.5" fill="var(--text-muted)" textAnchor="end" transform={`rotate(-90 ${pad - 6} ${pad + 4})`}>
          socially conservative →
        </text>

        {/* confidence region: wide box = we are not claiming much */}
        <rect
          x={at(economic) - spread / 2}
          y={at(-social) - spread / 2}
          width={spread}
          height={spread}
          rx="6"
          fill="var(--pole-support)"
          opacity="0.16"
        />
        <circle
          cx={at(economic)}
          cy={at(-social)}
          r="7"
          fill="var(--pole-support)"
          stroke="var(--surface-1)"
          strokeWidth="2"
          onMouseMove={(e) =>
            show(
              e,
              <>
                Framing: economic <strong>{economic.toFixed(2)}</strong>, social{" "}
                <strong>{social.toFixed(2)}</strong>
                <br />
                confidence {confidence.toFixed(2)}
              </>,
            )
          }
          onMouseLeave={hide}
        />

        {sourceMixLean !== null && (
          <g
            onMouseMove={(e) =>
              show(e, <>Lean of the sources cited: <strong>{sourceMixLean.toFixed(2)}</strong></>)
            }
            onMouseLeave={hide}
          >
            <line
              x1={at(sourceMixLean)}
              y1={size - pad}
              x2={at(sourceMixLean)}
              y2={size - pad - 12}
              stroke="var(--pole-refute)"
              strokeWidth="2"
            />
            <text
              x={at(sourceMixLean)}
              y={size - pad + 12}
              fontSize="10"
              fill="var(--text-secondary)"
              textAnchor="middle"
            >
              sources
            </text>
          </g>
        )}
      </svg>
      {node}
      <p className="sub" style={{ marginTop: 6 }}>
        Two estimators, shown separately and never averaged: the dot is how the video
        <em> frames</em> its subject; the tick is the lean of the sources it relies on.
      </p>
    </ChartFrame>
  );
}

const SIGNAL_LABELS: Record<string, string> = {
  c2pa: "Content Credentials (C2PA)",
  recycled_footage: "Recycled footage",
  container_forensics: "Container / encoder history",
  audio_spoof: "Synthetic voice",
  face_manipulation: "Face manipulation",
  ai_generated_frames: "AI-generated frames",
  splice_recompression: "Splice / recompression",
};

/**
 * Forensic signals as calibrated probabilities, each shown with the weight it
 * actually carries. Bar length is magnitude, so this is a sequential encoding —
 * one hue, light to dark, never the verdict palette.
 */
export function ForensicsPanel({
  signals,
  overall,
  confidence,
}: {
  signals: ForensicSignal[];
  overall: number | null;
  confidence: number | null;
}) {
  const { show, hide, node } = useTooltip();

  if (!signals.length) {
    return (
      <p className="sub">
        No forensic analysis was run for this video. Manipulation signals did not
        contribute to the score.
      </p>
    );
  }

  const conf = confidence ?? 0;
  const ramp = (p: number) =>
    p < 0.25 ? "var(--seq-1)" : p < 0.5 ? "var(--seq-2)" : p < 0.75 ? "var(--seq-3)" : "var(--seq-4)";

  return (
    <ChartFrame label="Forensic signals">
      <p className="sub" style={{ marginTop: 0 }}>
        {conf < 0.15 ? (
          <>
            Overall confidence is <strong>too low</strong> for these signals to affect the
            score. They are reported for information only.
          </>
        ) : (
          <>
            Combined manipulation probability <strong>{overall?.toFixed(2) ?? "—"}</strong> at
            confidence <strong>{conf.toFixed(2)}</strong>. Detectors generalise poorly to
            unseen generators, so treat this as one signal among several.
          </>
        )}
      </p>

      <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
        {signals.map((s) => {
          const p = s.calibrated_prob ?? 0;
          const label = SIGNAL_LABELS[s.signal] ?? s.signal.replace(/_/g, " ");
          return (
            <div
              key={s.signal}
              style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: 10, alignItems: "center" }}
              onMouseMove={(e) =>
                show(
                  e,
                  <>
                    <strong>{label}</strong>
                    <br />
                    calibrated {p.toFixed(2)} · raw {s.raw_score?.toFixed(2) ?? "—"}
                    <br />
                    weight {s.confidence?.toFixed(2) ?? "—"}
                  </>,
                )
              }
              onMouseLeave={hide}
            >
              <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{label}</span>
              <svg width="100%" height="12" style={{ display: "block" }}>
                <rect x="0" y="3" width="100%" height="6" rx="3" fill="var(--gridline)" />
                <rect
                  x="0"
                  y="1"
                  width={`${Math.max(p * 100, 1)}%`}
                  height="10"
                  rx="4"
                  fill={ramp(p)}
                  opacity={0.35 + (s.confidence ?? 0.3) * 0.65}
                />
              </svg>
              <span className="cred">{p.toFixed(2)}</span>
            </div>
          );
        })}
      </div>
      {node}
    </ChartFrame>
  );
}

/** Table view of every claim — the non-visual path through the same data. */
export function ClaimTable({ claims }: { claims: Claim[] }) {
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Claim</th>
            <th>Verdict</th>
            <th style={{ textAlign: "right" }}>Confidence</th>
            <th style={{ textAlign: "right" }}>Sources</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c) => (
            <tr key={c.id}>
              <td>{c.text}</td>
              <td>{styleFor(c.verdict).label}</td>
              <td className="num">{fmt((c.confidence ?? 0) * 100)}%</td>
              <td className="num">{c.evidence.filter((e) => e.cited).length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
