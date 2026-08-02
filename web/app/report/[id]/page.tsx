"use client";

import { use, useCallback, useEffect, useState } from "react";
import { Progress } from "@/components/Progress";
import { ReportView } from "@/components/ReportView";
import type { Report } from "@/lib/types";

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/api/reports/${id}`, { cache: "no-store" });
    if (!res.ok) {
      setError(res.status === 404 ? "Report not found." : "Could not load that report.");
      return;
    }
    setReport(await res.json());
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <section className="card"><p>{error}</p></section>;
  if (!report) return <section className="card"><p className="sub">Loading…</p></section>;

  if (report.status === "failed") {
    return (
      <section className="card">
        <h2>Analysis failed</h2>
        <p className="sub">{report.error}</p>
        <p className="sub">
          The most common cause is a reel posted by a personal (non-Professional)
          account, which no sanctioned Instagram API can reach.
        </p>
      </section>
    );
  }

  if (report.status !== "done") {
    return <Progress reportId={id} onDone={load} />;
  }

  return <ReportView report={report} />;
}
