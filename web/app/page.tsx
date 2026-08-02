import { AnalyseForm } from "@/components/AnalyseForm";

export default function Home() {
  return (
    <>
      <section className="card">
        <h2>Check a reel</h2>
        <p className="sub">
          Paste an Instagram reel link. Every claim is checked against retrieved
          evidence and cited — where the evidence does not settle a claim, it is
          reported as unverifiable rather than guessed at.
        </p>
        <AnalyseForm />
      </section>

      <section className="card">
        <h2>What you get</h2>
        <ul className="sub" style={{ margin: 0, paddingLeft: 20, lineHeight: 1.9 }}>
          <li>A transcript plus on-screen text, since reels put many claims in captions.</li>
          <li>Each factual claim adjudicated separately, with sources you can open.</li>
          <li>An overall validity score with a confidence interval and a published formula.</li>
          <li>Political framing on two axes, scored against a rubric you can read.</li>
          <li>Manipulation and provenance signals, calibrated and confidence-gated.</li>
        </ul>
      </section>
    </>
  );
}
