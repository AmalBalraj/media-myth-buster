"""Run the pipeline over eval/cases.jsonl and score it against expectations.

    python eval/run_eval.py            # run every case
    python eval/run_eval.py --id x1    # one case
    python eval/run_eval.py --compare runs/2026-08-01.json

Build this set before tuning prompts. Without it you cannot tell whether a change
helped, and "feels better" is a trap in exactly this kind of system — an edit that
makes summaries read more confidently usually makes them less accurate.

The metric that matters most is NOT overall accuracy. It is the false-confidence
rate: how often a claim that should be `unverifiable` came back `true` or `false`.
That is the failure mode that makes the product harmful rather than merely wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
API = "http://localhost:8100"
POLL_SECONDS = 4
TIMEOUT_SECONDS = 600


async def run_case(client: httpx.AsyncClient, case: dict) -> dict:
    url = case.get("url", "")
    if not url or "REPLACE" in url:
        return {"id": case["id"], "skipped": "no URL set"}

    started = datetime.now(timezone.utc)
    r = await client.post(f"{API}/api/analyse", json={"url": url, "force": True}, timeout=30)
    r.raise_for_status()
    report_id = r.json()["report_id"]

    for _ in range(TIMEOUT_SECONDS // POLL_SECONDS):
        await asyncio.sleep(POLL_SECONDS)
        got = await client.get(f"{API}/api/reports/{report_id}", timeout=30)
        report = got.json()
        if report["status"] in ("done", "failed"):
            break
    else:
        return {"id": case["id"], "error": "timed out"}

    if report["status"] == "failed":
        return {"id": case["id"], "error": report.get("error")}

    return score_case(case, report, (datetime.now(timezone.utc) - started).total_seconds())


def score_case(case: dict, report: dict, elapsed: float) -> dict:
    expect = case.get("expect", {})
    claims = report.get("claims", [])
    verdicts = [c.get("verdict") for c in claims]
    failures: list[str] = []

    if len(claims) < expect.get("claims_min", 0):
        failures.append(f"found {len(claims)} claims, expected >= {expect['claims_min']}")

    for verdict, minimum in (expect.get("verdicts") or {}).items():
        got = verdicts.count(verdict)
        if got < minimum:
            failures.append(f"{verdict}: got {got}, expected >= {minimum}")

    for banned in expect.get("must_not_be") or []:
        if banned in verdicts:
            failures.append(f"produced a '{banned}' verdict, which this case forbids")

    want_lean = expect.get("lean_applicable")
    if want_lean is not None and bool(report.get("lean_applicable")) != want_lean:
        failures.append(f"lean_applicable={report.get('lean_applicable')}, expected {want_lean}")

    # The safety metric: confident verdicts resting on nothing.
    uncited = [
        c["text"][:60]
        for c in claims
        if c.get("verdict") in ("true", "false", "mostly_true", "mostly_false")
        and not any(e.get("cited") for e in c.get("evidence", []))
    ]
    if uncited:
        failures.append(f"CONFIDENT VERDICT WITHOUT CITATION: {uncited}")

    sub = report.get("subscores") or {}
    return {
        "id": case["id"],
        "pass": not failures,
        "failures": failures,
        "validity": report.get("validity_score"),
        "claims": len(claims),
        "unverifiable": sub.get("claims_unverifiable"),
        "verdicts": {v: verdicts.count(v) for v in set(verdicts) if v},
        "cost_usd": sub.get("cost_usd"),
        "elapsed_sec": round(elapsed, 1),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="run a single case by id")
    ap.add_argument("--compare", help="path to a previous run's JSON")
    args = ap.parse_args()

    cases = [
        json.loads(line)
        for line in (ROOT / "cases.jsonl").read_text().splitlines()
        if line.strip()
    ]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]

    async with httpx.AsyncClient() as client:
        results = [await run_case(client, c) for c in cases]

    ran = [r for r in results if "skipped" not in r and "error" not in r]
    passed = [r for r in ran if r["pass"]]
    unsafe = [r for r in ran if any("CONFIDENT VERDICT" in f for f in r["failures"])]

    print(f"\n{'case':<22} {'pass':<6} {'validity':>9} {'claims':>7}  notes")
    print("-" * 88)
    for r in results:
        if "skipped" in r:
            print(f"{r['id']:<22} {'skip':<6} {'—':>9} {'—':>7}  {r['skipped']}")
        elif "error" in r:
            print(f"{r['id']:<22} {'ERR':<6} {'—':>9} {'—':>7}  {r['error']}")
        else:
            mark = "ok" if r["pass"] else "FAIL"
            validity = "—" if r["validity"] is None else f"{r['validity']:.1f}"
            note = "; ".join(r["failures"])[:44]
            print(f"{r['id']:<22} {mark:<6} {validity:>9} {r['claims']:>7}  {note}")

    if ran:
        print(f"\n{len(passed)}/{len(ran)} passed")
        print(f"false-confidence failures: {len(unsafe)}  <- keep this at zero")
        print(f"total cost: ${sum(r.get('cost_usd') or 0 for r in ran):.4f}")

    out = ROOT / "runs" / f"{datetime.now(timezone.utc):%Y-%m-%dT%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")

    if args.compare:
        previous = {r["id"]: r for r in json.loads(Path(args.compare).read_text())}
        print("\nchanges vs baseline:")
        for r in ran:
            was = previous.get(r["id"])
            if was and was.get("pass") != r["pass"]:
                print(f"  {r['id']}: {'FIXED' if r['pass'] else 'REGRESSED'}")

    return 0 if not unsafe and len(passed) == len(ran) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
