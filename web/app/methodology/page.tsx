async function getMethodology() {
  const base = process.env.API_BASE_URL ?? "http://localhost:8100";
  try {
    const res = await fetch(`${base}/api/methodology`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export default async function MethodologyPage() {
  const m = await getMethodology();

  return (
    <>
      <section className="card">
        <h2>How a score is produced</h2>
        <p className="sub">
          Every judgement in a report is tied to sources you can open. Where retrieval
          finds nothing, the claim is marked unverifiable — the system never treats
          silence as evidence of falsehood.
        </p>
        {m && (
          <p style={{ fontFamily: "ui-monospace, monospace", fontSize: 12.5 }}>
            {m.validity_formula}
          </p>
        )}
      </section>

      {m && (
        <>
          <section className="card">
            <h2>Verdicts and what they contribute</h2>
            <div className="scroll-x">
              <table>
                <thead>
                  <tr>
                    <th>Verdict</th>
                    <th style={{ textAlign: "right" }}>Contribution</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(m.verdict_values as Record<string, number>).map(([k, v]) => (
                    <tr key={k}>
                      <td>{k.replace(/_/g, " ")}</td>
                      <td className="num">{v.toFixed(2)}</td>
                    </tr>
                  ))}
                  {(m.excluded_verdicts as string[]).map((k) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td className="num">excluded from the average</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>Weight given to each forensic signal</h2>
            <p className="sub">
              Cryptographic provenance is trusted; statistical detectors are not trusted
              far, because they generalise poorly to generators they were not trained on.
            </p>
            <div className="scroll-x">
              <table>
                <tbody>
                  {Object.entries(m.forensic_signal_confidence as Record<string, number>)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => (
                      <tr key={k}>
                        <td>{k.replace(/_/g, " ")}</td>
                        <td className="num">{v.toFixed(2)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>Political lean rubric</h2>
            <p className="sub">
              This is an editorial instrument, so it is published in full rather than
              described. It is applied mechanically.
            </p>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                fontSize: 12.5,
                color: "var(--text-secondary)",
                margin: 0,
              }}
            >
              {m.political_lean_rubric}
            </pre>
          </section>

          <section className="card">
            <h2>Limitations</h2>
            <ul className="sub" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.85 }}>
              {(m.limitations as string[]).map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </section>
        </>
      )}

      {!m && (
        <section className="card">
          <p className="sub">The methodology endpoint is unavailable — is the API running?</p>
        </section>
      )}
    </>
  );
}
