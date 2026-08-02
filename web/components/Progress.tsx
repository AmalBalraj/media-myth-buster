"use client";

import { useEffect, useState } from "react";
import type { StageEvent } from "@/lib/types";

const STAGES: [string, string][] = [
  ["ingest", "Fetching reel"],
  ["transcribe", "Transcribing"],
  ["video", "Reading video"],
  ["forensics", "Forensics"],
  ["claims", "Extracting claims"],
  ["evidence", "Gathering evidence"],
  ["adjudicate", "Checking claims"],
  ["score", "Scoring"],
];

/** Live stage progress. The pipeline takes 30-90s, so this is functional, not decoration. */
export function Progress({
  reportId,
  onDone,
}: {
  reportId: string;
  onDone: () => void;
}) {
  const [seen, setSeen] = useState<Record<string, StageEvent>>({});
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    const es = new EventSource(`/api/reports/${reportId}/events`);
    es.addEventListener("stage", (e) => {
      const ev: StageEvent = JSON.parse((e as MessageEvent).data);
      setSeen((prev) => ({ ...prev, [ev.stage]: ev }));
      if (ev.stage === "done") {
        es.close();
        if (ev.status === "error") setFailed(String(ev.message ?? "Analysis failed"));
        else onDone();
      }
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [reportId, onDone]);

  const evidence = seen.evidence;
  const activeIdx = STAGES.findIndex(([k]) => seen[k] && seen[k].status !== "done");

  return (
    <div className="card">
      <h2>Analysing</h2>
      {/* Reasoning-model calls dominate: claim extraction and each adjudication
          run ~50s, three at a time. */}
      <p className="sub">This usually takes two to four minutes.</p>

      <ul className="stages">
        {STAGES.map(([key, label], i) => {
          const ev = seen[key];
          const state = !ev
            ? "pending"
            : ev.status === "error"
              ? "error"
              : ev.status === "done"
                ? "done"
                : "active";
          return (
            <li key={key} data-state={state}>
              <span aria-hidden>
                {state === "done" ? "✓" : state === "error" ? "!" : state === "active" ? "◐" : "○"}
              </span>
              {label}
              {key === "evidence" && state === "active" && evidence?.done !== undefined
                ? ` ${evidence.done}/${evidence.total}`
                : ""}
            </li>
          );
        })}
      </ul>

      {activeIdx === -1 && !failed && Object.keys(seen).length === 0 && (
        <p className="sub" style={{ marginTop: 14 }}>Waiting for a worker to pick this up…</p>
      )}

      {failed && (
        <p style={{ color: "var(--pole-refute)", fontSize: 14, marginTop: 16 }}>{failed}</p>
      )}
    </div>
  );
}
