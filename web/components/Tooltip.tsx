"use client";

import { useCallback, useState, type ReactNode } from "react";

export interface TipState {
  x: number;
  y: number;
  content: ReactNode;
}

/** Shared hover layer. Every mark-based chart here ships one — a chart in HTML is
 *  interactive by default, and the tooltip is where the numbers live so the plot
 *  itself can stay free of labels on every point. */
export function useTooltip() {
  const [tip, setTip] = useState<TipState | null>(null);

  const show = useCallback((e: React.MouseEvent, content: ReactNode) => {
    const host = e.currentTarget.closest("[data-chart]") as HTMLElement | null;
    const box = host?.getBoundingClientRect();
    setTip({
      x: e.clientX - (box?.left ?? 0),
      y: e.clientY - (box?.top ?? 0),
      content,
    });
  }, []);

  const hide = useCallback(() => setTip(null), []);

  const node = tip ? (
    <div
      className="tooltip"
      style={{
        left: Math.max(tip.x + 12, 4),
        top: Math.max(tip.y - 8, 4),
      }}
      role="status"
    >
      {tip.content}
    </div>
  ) : null;

  return { show, hide, node };
}

export function ChartFrame({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div data-chart style={{ position: "relative" }} role="img" aria-label={label}>
      {children}
    </div>
  );
}
