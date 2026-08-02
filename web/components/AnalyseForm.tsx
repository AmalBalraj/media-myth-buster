"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AnalyseForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/analyse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail?.[0]?.msg ?? data.detail ?? "Could not queue that URL");
      }
      router.push(`/report/${data.report_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="field">
        <input
          type="url"
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.instagram.com/reel/..."
          aria-label="Instagram reel URL"
        />
        <button type="submit" disabled={busy || !url}>
          {busy ? "Queuing…" : "Analyse"}
        </button>
      </div>
      {error && (
        <p style={{ color: "var(--pole-refute)", fontSize: 13, marginBottom: 0 }}>{error}</p>
      )}
    </form>
  );
}
