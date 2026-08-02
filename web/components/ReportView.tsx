"use client";

import { useState } from "react";
import { ClaimList } from "@/components/ClaimList";
import {
  Sparkline,
  SubscoreRadar,
  ValidityHero,
  VerdictBar,
} from "@/components/charts/Validity";
import {
  ClaimTable,
  ClaimTimeline,
  ForensicsPanel,
  LeanPlot,
} from "@/components/charts/Signals";
import type { Report } from "@/lib/types";
import { fmt } from "@/lib/verdicts";

export function ReportView({ report }: { report: Report }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [showTable, setShowTable] = useState(false);
  const sub = report.subscores ?? {};
  const media = report.media;
  const duration =
    report.transcript?.segments?.at(-1)?.end ?? (report.transcript as any)?.duration ?? 0;
  const creator = sub.creator as
    | { displayable: boolean; score: number | null; reels_analysed: number; note: string; history: { validity: number }[] }
    | undefined;

  return (
    <>
      <section className="card">
        <h2>{sub.headline ?? "Analysis"}</h2>
        {media?.creator && (
          <p className="sub">
            @{media.creator.handle}
            {media.creator.followers
              ? ` · ${media.creator.followers.toLocaleString()} followers`
              : ""}
            {media.permalink && (
              <>
                {" · "}
                <a href={media.permalink} target="_blank" rel="noopener noreferrer">
                  view on Instagram
                </a>
              </>
            )}
          </p>
        )}

        <div className="grid-2" style={{ marginTop: 18 }}>
          <ValidityHero
            score={report.validity_score}
            ciLow={report.validity_ci_low}
            ciHigh={report.validity_ci_high}
            scored={sub.claims_scored ?? 0}
            total={sub.claims_total ?? report.claims.length}
            unverifiable={sub.claims_unverifiable ?? 0}
          />
          <div>
            {report.summary && <p style={{ margin: 0, fontSize: 15 }}>{report.summary}</p>}
          </div>
        </div>

        {Array.isArray(sub.notes) && sub.notes.length > 0 && (
          <ul className="sub" style={{ marginTop: 16, marginBottom: 0, paddingLeft: 18 }}>
            {sub.notes.map((n: string) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </section>

      {report.claims.length > 0 && (
        <section className="card">
          <h2>Claims across the video</h2>
          <p className="sub">
            Each mark is one claim, placed where it occurs. Click to jump to it.
          </p>
          <ClaimTimeline claims={report.claims} duration={duration} onSelect={setSelected} />
          <div style={{ marginTop: 18 }}>
            <VerdictBar claims={report.claims} />
          </div>
        </section>
      )}

      <div className="grid-2">
        <section className="card">
          <h2>Sub-scores</h2>
          <p className="sub">Components of the overall figure, each on 0–100.</p>
          <SubscoreRadar
            data={[
              { label: "Factual accuracy", value: sub.factual_accuracy ?? null },
              { label: "Source quality", value: sub.source_quality ?? null },
              { label: "Media integrity", value: sub.manipulation_integrity ?? null },
              {
                label: "Creator record",
                value: creator?.displayable ? (creator.score ?? null) : null,
              },
              {
                label: "Evidence coverage",
                value:
                  sub.claims_total > 0
                    ? Math.round((sub.claims_scored / sub.claims_total) * 100)
                    : null,
              },
            ]}
          />
        </section>

        <section className="card">
          <h2>Political framing</h2>
          {report.lean_applicable &&
          report.lean_economic !== null &&
          report.lean_social !== null ? (
            <>
              <p className="sub">Scored against a published rubric, not a model's opinion.</p>
              <LeanPlot
                economic={report.lean_economic}
                social={report.lean_social}
                confidence={report.lean_confidence ?? 0.5}
                sourceMixLean={sub.source_mix_lean ?? null}
              />
              {report.lean_rationale && (
                <p className="sub" style={{ marginTop: 10 }}>{report.lean_rationale}</p>
              )}
            </>
          ) : (
            <p className="sub">
              This video carries no political content, so no lean is scored. A
              meaningless number here would be worse than none.
            </p>
          )}
        </section>
      </div>

      <section className="card">
        <h2>Media integrity</h2>
        <p className="sub">
          Manipulation and provenance signals. Never a verdict on its own.
        </p>
        <ForensicsPanel
          signals={report.forensics}
          overall={report.forensics_score}
          confidence={report.forensics_confidence}
        />
      </section>

      <section className="card">
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            gap: 12,
          }}
        >
          <div>
            <h2>Claims and evidence</h2>
            <p className="sub" style={{ marginBottom: 0 }}>
              {report.claims.length} claim{report.claims.length === 1 ? "" : "s"} checked
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowTable((v) => !v)}
            style={{
              background: "transparent",
              color: "var(--text-secondary)",
              border: "1px solid var(--border)",
              padding: "6px 12px",
              fontSize: 13,
            }}
          >
            {showTable ? "Detail view" : "Table view"}
          </button>
        </div>

        <div style={{ marginTop: 16 }}>
          {showTable ? (
            <ClaimTable claims={report.claims} />
          ) : (
            <ClaimList
              claims={report.claims}
              permalink={media?.permalink ?? null}
              highlight={selected}
            />
          )}
        </div>
      </section>

      {creator && (
        <section className="card">
          <h2>Creator track record</h2>
          <p className="sub">{creator.note}</p>
          {creator.displayable && (
            <div style={{ display: "flex", alignItems: "center", gap: 18, marginTop: 10 }}>
              <span style={{ fontSize: 30, fontWeight: 600 }}>{fmt(creator.score)}</span>
              <Sparkline history={creator.history} />
            </div>
          )}
        </section>
      )}

      <section className="card">
        <h2>How this was calculated</h2>
        <p className="sub" style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>
          {sub.formula}
        </p>
        <table style={{ marginTop: 10 }}>
          <tbody>
            <tr>
              <td>Claims found</td>
              <td className="num">{sub.claims_total ?? 0}</td>
            </tr>
            <tr>
              <td>Scored against evidence</td>
              <td className="num">{sub.claims_scored ?? 0}</td>
            </tr>
            <tr>
              <td>Unverifiable</td>
              <td className="num">{sub.claims_unverifiable ?? 0}</td>
            </tr>
            <tr>
              <td>Opinions (excluded)</td>
              <td className="num">{sub.claims_opinion ?? 0}</td>
            </tr>
            <tr>
              <td>Manipulation penalty applied</td>
              <td className="num">{fmt(sub.manipulation_penalty ?? 0, 1)}</td>
            </tr>
            <tr>
              <td>Analysis time</td>
              <td className="num">{fmt(sub.elapsed_sec ?? 0, 1)}s</td>
            </tr>
            <tr>
              <td>Model cost</td>
              <td className="num">${(sub.cost_usd ?? 0).toFixed(4)}</td>
            </tr>
          </tbody>
        </table>
        <p className="sub" style={{ marginTop: 12, marginBottom: 0 }}>
          <a href="/methodology">Full methodology and limitations →</a>
        </p>
      </section>
    </>
  );
}
