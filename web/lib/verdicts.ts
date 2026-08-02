import type { Verdict } from "./types";

/**
 * Verdicts render on a diverging scale: a supported pole, a neutral midpoint, and a
 * refuted pole. Within an arm, `emphasis` (fill opacity) separates the two steps —
 * hue does not, because a second step per arm cannot clear the dark-mode lightness
 * band. Every mark therefore also carries `label`, and nothing in the UI shows a
 * verdict colour without it.
 */
export interface VerdictStyle {
  label: string;
  color: string;
  emphasis: number;
  /** Shape, so colour is never the only channel (print, forced-colors, CVD). */
  glyph: string;
  scored: boolean;
}

export const VERDICTS: Record<Verdict, VerdictStyle> = {
  true: { label: "True", color: "var(--pole-support)", emphasis: 1, glyph: "✓", scored: true },
  mostly_true: {
    label: "Mostly true",
    color: "var(--pole-support)",
    emphasis: 0.55,
    glyph: "✓",
    scored: true,
  },
  mixed: { label: "Mixed", color: "var(--neutral-mark)", emphasis: 1, glyph: "~", scored: true },
  mostly_false: {
    label: "Mostly false",
    color: "var(--pole-refute)",
    emphasis: 0.55,
    glyph: "✕",
    scored: true,
  },
  false: { label: "False", color: "var(--pole-refute)", emphasis: 1, glyph: "✕", scored: true },
  unverifiable: {
    label: "Unverifiable",
    color: "var(--neutral-mark)",
    emphasis: 0.35,
    glyph: "?",
    scored: false,
  },
  opinion: {
    label: "Opinion",
    color: "var(--neutral-mark)",
    emphasis: 0.35,
    glyph: "◦",
    scored: false,
  },
};

export const VERDICT_ORDER: Verdict[] = [
  "true",
  "mostly_true",
  "mixed",
  "mostly_false",
  "false",
  "unverifiable",
  "opinion",
];

export function styleFor(v: Verdict | null | undefined): VerdictStyle {
  return v && VERDICTS[v] ? VERDICTS[v] : VERDICTS.unverifiable;
}

export function fmt(n: number | null | undefined, digits = 0): string {
  return n === null || n === undefined ? "—" : n.toFixed(digits);
}
