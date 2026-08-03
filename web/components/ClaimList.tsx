"use client";

import type { Claim } from "@/lib/types";
import { fmt, styleFor } from "@/lib/verdicts";

function VerdictChip({ claim }: { claim: Claim }) {
  const s = styleFor(claim.verdict);
  return (
    <span className="chip">
      <span className="dot" style={{ background: s.color, opacity: s.emphasis }} />
      <span aria-hidden>{s.glyph}</span>
      {s.label}
    </span>
  );
}

export function ClaimList({
  claims,
  permalink,
  highlight,
}: {
  claims: Claim[];
  permalink: string | null;
  highlight?: string | null;
}) {
  if (!claims.length) {
    return (
      <p className="sub">
        No check-worthy factual claims were found. That is a normal outcome for
        opinion, entertainment, or purely personal content.
      </p>
    );
  }

  return (
    <div>
      {claims.map((c) => {
        const cited = c.evidence.filter((e) => e.cited);
        const other = c.evidence.filter((e) => !e.cited);
        return (
          <article
            key={c.id}
            id={`claim-${c.id}`}
            className="claim-row"
            style={
              highlight === c.id
                ? { background: "var(--neutral-fill)", borderRadius: 8, padding: 16 }
                : undefined
            }
          >
            <div className="claim-head">
              <div style={{ minWidth: 0 }}>
                <p className="claim-text">{c.text}</p>
                {/* The verdict rests on this translation, so a reader who speaks
                    the language must be able to check it against the original. */}
                {c.lang && c.lang !== "en" && c.verbatim && (
                  <p className="verbatim" lang={c.lang}>
                    {c.verbatim}
                  </p>
                )}
                <p className="muted" style={{ margin: 0 }}>
                  {c.t_start !== null && permalink ? `${c.t_start.toFixed(1)}s · ` : ""}
                  {c.claim_type} · from {c.source === "ocr" ? "on-screen text" : c.source}
                  {c.lang && c.lang !== "en" ? ` · translated from ${c.lang}` : ""}
                  {c.confidence !== null ? ` · confidence ${fmt(c.confidence * 100)}%` : ""}
                </p>
              </div>
              <VerdictChip claim={c} />
            </div>

            {c.rationale && (
              <p className="sub" style={{ marginTop: 10, marginBottom: 0 }}>
                {c.rationale}
              </p>
            )}

            {cited.length > 0 && (
              <ul className="evidence">
                {cited.map((e) => (
                  <li key={e.url}>
                    <span className="cred">
                      {e.publisher_credibility !== null
                        ? e.publisher_credibility.toFixed(2)
                        : "—"}
                    </span>
                    <a href={e.url} target="_blank" rel="noopener noreferrer">
                      {e.title ?? e.url}
                    </a>
                    <span className="cred">{e.publisher}</span>
                  </li>
                ))}
              </ul>
            )}

            {other.length > 0 && (
              <details style={{ marginTop: 10 }}>
                <summary>{other.length} more sources retrieved but not cited</summary>
                <ul className="evidence">
                  {other.map((e) => (
                    <li key={e.url}>
                      <span className="cred">
                        {e.publisher_credibility !== null
                          ? e.publisher_credibility.toFixed(2)
                          : "—"}
                      </span>
                      <a href={e.url} target="_blank" rel="noopener noreferrer">
                        {e.title ?? e.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        );
      })}
    </div>
  );
}
