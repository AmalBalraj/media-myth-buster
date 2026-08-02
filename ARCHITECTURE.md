# Media Myth Buster — Architecture

> Paste an Instagram Reel link (or @mention the bot on any public reel) → get a detailed,
> cited, chart-driven credibility report: transcript, claim-by-claim fact check,
> manipulation/deepfake forensics, political-lean scoring, and creator track record.

This document is the technical plan for [REQUIRMENTS.md](REQUIRMENTS.md), written against two
hard constraints: **`oracle-dev` is an orchestrator, not a compute node** — it fetches,
queues, calls APIs, stores, and serves; it does not transcode, transcribe, or run models.
And **everything must run on free tiers plus a DeepSeek V4 Flash key** (~1¢ per reel).

---

## 0. Design principles

1. **Never output an unsourced verdict.** Every claim-level judgement carries citations. If
   retrieval found nothing, the verdict is `unverifiable`, not `false`. An authoritative-looking
   wrong verdict is worse than no product.
2. **Scores are decomposed, never a single black box.** The overall validity score is a
   published weighted formula over sub-scores the user can expand.
3. **Calibrated uncertainty over binary labels** — especially for deepfake detection, where
   2026 detectors generalise badly to unseen generators.
4. **The server never touches pixels or audio samples.** It moves bytes and JSON. All
   inference is a hosted API call or an off-box microservice. This is the constraint that
   shapes §3–§5.
5. **Provider-swappable model layer.** DeepSeek V4 Flash for text, Gemini for video, Groq for
   audio — one interface, config-driven, no rewrites.
6. **Cache aggressively; everything is content-addressed.** Free tiers are rate-limited, and
   DeepSeek's cache-hit pricing is a 98% discount. The cache *is* the cost model.
7. **Prefer sanctioned Instagram APIs over scraping.** See §2 — with a dedicated Professional
   account, most of this is legitimately available.

---

## 1. System overview

```
   @mention on any reel ─┐
   IG DM to bot ─────────┤        ┌──────────────────────────────────────┐
   Pasted URL ───────────┴───────▶│  Next.js web app (Vercel free tier)  │
   PWA share target               │  SSE progress · report UI · charts   │
                                  └───────────────┬──────────────────────┘
                                                  │ REST + SSE
   ╔══════════════════════════════════════════════▼═══════════════════════════╗
   ║  oracle-dev — ORCHESTRATION ONLY (no media processing)                    ║
   ║                                                                          ║
   ║   FastAPI :8100   ──▶  Redis queue  ──▶  ARQ workers                     ║
   ║   IG webhooks     ──▶  (fetch bytes, call APIs, persist, assemble)        ║
   ║                                                                          ║
   ║   Postgres 17 + pgvector  ·  media blobs on disk/R2  ·  nginx TLS         ║
   ╚═══╤═══════════╤═══════════╤═══════════╤═══════════╤══════════════════════╝
       │           │           │           │           │
       ▼           ▼           ▼           ▼           ▼
  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐
  │ Meta    │ │ Groq    │ │ Gemini  │ │DeepSeek │ │ HF Space     │
  │ Graph   │ │ Whisper │ │ File API│ │V4 Flash │ │ "forensics"  │
  │ API     │ │ (ASR)   │ │ (video) │ │ (text)  │ │ (pixels)     │
  │ ingest  │ │ free    │ │ free    │ │ ~1¢/reel│ │ free CPU/Zero│
  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────────┘
                                          │
                                          ▼
                          ┌──────────────────────────────┐
                          │ Evidence: Google Fact Check, │
                          │ SearXNG, Wikipedia, PubMed,  │
                          │ OpenAlex, GDELT, World Bank  │
                          └──────────────────────────────┘
```

**The whole pipeline for one reel on the box:** download ~5 MB, POST it to three APIs, run
~15 HTTP fetches for evidence, write rows to Postgres. Peak CPU is JSON parsing. That is the
entire budget.

---

## 2. Ingestion — solving the Instagram problem

### 2.1 What a dedicated account actually unlocks

**Yes, creating an account solves most of this — but it must be a Professional (Business or
Creator) account linked to a Facebook Page, not a personal one.** That unlocks three
sanctioned Graph API surfaces, and together they cover the majority of real usage:

| Surface | What you get | Constraints |
|---|---|---|
| **`GET /{ig-user-id}/mentioned_media`** + **mentions webhook** | Media that @mentioned your account — **including media you do not own**. Fields: `caption`, `media_url`, `media_type`, `permalink`, `timestamp`, `username`. | Webhook fires only for **public** accounts. Stories not supported. Needs `instagram_business_manage_comments`. |
| **`business_discovery`** | Any **Professional** account's public media by username: `media_url`, `caption`, `permalink`, `media_type`, `like_count`, `comments_count`, `view_count`, `timestamp`. Reels included. | Target must be Professional (not personal). Facebook Login only. ~200 calls/hour × app users. |
| **oEmbed** | `thumbnail_url` + embed HTML for any public post/reel. **App-token only — no user token, no per-user auth.** | Public posts/carousels/reels only. No stories, no private accounts. |

`media_url` being present on both `mentioned_media` and `business_discovery` is the key fact:
it is a direct CDN link to the actual video file. You can fetch and analyse the real bytes
through a sanctioned path.

### 2.2 The resulting ingestion ladder

Try in order; each rung is more legitimate than the one below it:

1. **@mention → webhook → `mentioned_media`.** A user comments `@mythbuster check this` on
   *any* public reel. Meta pushes you a webhook, you read the media, you reply with the report
   link. This is the primary product experience *and* the cleanest legal footing — the content
   is delivered to you by Meta, on a user's explicit invitation. Build this first.
2. **User pastes a URL → `business_discovery`** by the creator's handle. Works for essentially
   every influencer/news/commentary account worth fact-checking, because monetising creators
   are almost universally Professional accounts.
3. **oEmbed probe** for thumbnail + author attribution when you only need light metadata, or
   to validate a URL before spending quota.
4. **yt-dlp fallback**, explicitly last, for personal-account reels the API cannot reach.
   Behind a feature flag, clearly labelled, with a burner session — never your bot account.

The critical design consequence: `ingest/adapters/*.py` all emit the same `MediaBundle`
contract, so which rung fired never leaks into the analysis pipeline, and adding
TikTok/Shorts/X later is one adapter each.

### 2.3 App Review — plan for it now

Third-party data access (i.e. anything about media you don't own) needs **Advanced Access via
App Review**: business verification, a live app with a privacy policy and data-deletion
endpoint, a working demo, and screen recordings proving each permission's API call. Standard
Access (your own account only) needs no review — so **build and demo against your own account
first**, then submit. Permissions to request:
`instagram_business_basic`, `instagram_business_manage_comments`
(or the Facebook-Login trio `instagram_basic` + `instagram_manage_comments` + `pages_read_engagement`).

Budget 2–6 weeks of review latency. Nothing else in the roadmap blocks on it, so submit early
and keep building.

---

## 3. Media handling — what the server does and doesn't do

**Does:** downloads the file from `media_url` (a few MB), computes a content hash, stores it,
hands out short-lived signed URLs, deletes it after N days.

**Does not:** transcode, extract frames, or resample audio in the happy path. Two facts make
this possible:

- **Groq's transcription endpoint is OpenAI-compatible and accepts `mp4` directly.** No
  ffmpeg audio extraction needed — POST the reel as-is.
- **Gemini's File API accepts video directly**, up to 2 hours. No frame extraction needed for
  semantic analysis — it does its own sampling server-side.

The only pixel-level work is the forensics ensemble, and that lives in an **off-box
microservice** (§5.4) that pulls the signed URL and runs its own ffmpeg. The box stays idle.

> Guardrails anyway: reels ≤ 10 min and ≤ 100 MB (Groq free tier caps uploads ~25 MB, so
> chunk or reject above that), download timeout 30 s, worker concurrency 2.

---

## 4. Understanding the content

### 4.1 Transcription — Groq Whisper

**`whisper-large-v3-turbo` on Groq.** Free tier: **2,000 requests/day, 7,200 audio-seconds
per hour** (~2 h of audio per clock hour) — that is roughly 120 reels/hour and 2,000/day, far
beyond what you'll need. OpenAI-compatible API, ~228× real-time, word-level timestamps,
multilingual (matters for Hindi/Malayalam/code-mixed reels).

Rate limits are **org-level**, so extra API keys don't multiply quota. Fallback chain:
Groq → Gemini (audio input) → local whisper.cpp *only* as an emergency escape hatch.

### 4.2 Video understanding — Gemini File API

This is the piece that replaces all the local vision work, and it does more than frame
extraction ever would. Upload the mp4, ask for structured JSON:

- **On-screen text (OCR)** — burned-in captions, chyrons, screenshot overlays, with
  timestamps. *Reels put half their claims here; a transcript-only system misses them.*
- **Visual-audio consistency** — does the footage actually show what the narration claims?
- **Scene provenance cues** — staged/reenacted framing, stock footage, screen recordings of
  other media, obvious recycled B-roll.
- **Visible source attribution** — watermarks, handles, cited outlet logos.

Free tier (per model, shared 250k TPM, full 1M context):

| Model | RPM | RPD | Use |
|---|---|---|---|
| 2.5 Flash-Lite | 15 | 1,000 | Volume workhorse — OCR, frame QA |
| 2.5 Flash | 10 | 250 | Main video pass |
| 2.5 Pro | 5 | 100 | Hard cases, ambiguous footage |

Route by difficulty; 250 full video analyses/day is plenty for a personal product, and the
report cache means repeat reels cost zero.

### 4.3 Forensics — off-box microservice

Deepfake/manipulation detection is the one job with no good free hosted API at volume, so it
runs as a **Hugging Face Space** (`myth-buster-forensics`) exposing a small FastAPI/Gradio
endpoint. It pulls a signed media URL and returns a signal vector. Free CPU Spaces handle the
lightweight ONNX models fine; ZeroGPU (free, a few GPU-minutes/day) covers the heavier ones.

| Signal | Method | Catches |
|---|---|---|
| AI-generated frames | ONNX CLIP linear-probe / EfficientNetV2 detectors | Sora/Veo/CogVideoX-class synthetic footage |
| Face-swap / reenactment | YuNet face crops → FF++/Celeb-DF/DFDC-family detector | Classic deepfakes |
| Splice / recompression | ELA, noise-residual variance, blockiness discontinuity | Pasted overlays, cut-and-paste edits |
| Container forensics | `ffprobe` encoder tags, atom order, GOP structure | Editor fingerprints, re-encode chains |
| Provenance | **c2pa-python** (C2PA v2.3, Jan 2026) | Signed Content Credentials — rare, near-conclusive |
| Audio spoof | AASIST / RawNet2 ONNX | Voice cloning, dubbed audio |
| Recycled footage | pHash/dHash keyframes vs your corpus | Old clip recirculated as new event ← *highest-value cheap signal* |

**Optional deep scan:** [Reality Defender's free tier](https://www.realitydefender.com/insights/reality-defender-launches-free-access-to-deepfake-detection-api)
gives 50 detections/month. Too small for every reel — wire it as a user-triggered
"deep scan" button on suspicious content, not an automatic stage.

**Calibration is mandatory.** 2026 benchmarks (AIGVDBench: 31 generators, 440k videos) show
detectors collapse on unseen generators, and Instagram's re-encoding destroys forensic
traces. Every raw score passes through a fitted calibration curve to a probability with an
interval; the UI shows *"3 of 7 signals flag anomalies — moderate confidence"* with a
per-signal breakdown, never a bare "FAKE". A `forensics_confidence` field gates how much this
contributes to the overall score.

---

## 5. Reasoning — DeepSeek V4 Flash as the workhorse

DeepSeek V4 Flash: **$0.14/M input, $0.28/M output, $0.0028/M on cache hits (98% off),
1M-token context, 384K max output — text-only.** That pricing plus context window makes it the
right primary brain for everything textual, and the cache discount is why long stable system
prompts are an architectural choice, not a detail.

**Text-only is the key routing constraint:** DeepSeek never sees the video. It reasons over
the artefacts Gemini and Groq produce.

| Task | Model | Why |
|---|---|---|
| Claim extraction | DeepSeek V4 Flash | Long context swallows transcript + OCR + caption + comments in one call |
| Stance/NLI over evidence | DeepSeek V4 Flash (batched) | Cheap enough to replace a local DeBERTa deployment |
| Claim adjudication | DeepSeek V4 Flash | Structured JSON output, citation-constrained |
| Report synthesis | DeepSeek V4 Flash | 384K output ceiling means the long-form report is one call |
| Video/visual analysis | **Gemini 2.5 Flash** | DeepSeek is text-only |
| Transcription | **Groq Whisper v3 Turbo** | Free, fast, OpenAI-compatible |
| Overflow / burst | Groq Llama, Cerebras, OpenRouter `:free` | Free failover when DeepSeek is slow |
| Hermes (GPT-5.6) on `oracle-dev` | Dev-time only | It's an agent CLI, not a production API — use it for building the eval set, prompt iteration, and ops. Keep it out of the request path. |

Routing lives in `config/models.yaml`. Every call is logged with model, prompt hash, and token
counts so past reports stay reproducible.

### 5.1 Cost per reel

| Stage | Input tok | Output tok | Cost |
|---|---|---|---|
| Claim extraction | ~6k | ~1.5k | $0.0013 |
| Evidence adjudication (×6 claims) | ~48k | ~4k | $0.0079 |
| Report synthesis | ~12k | ~3k | $0.0025 |
| **Total** | ~66k | ~8.5k | **≈ $0.012** |

**~1.2¢ per uncached reel**; with cached system prompts and a warm evidence corpus, closer to
0.5¢. **1,000 reels ≈ $8.** Groq, Gemini, SearXNG, HF Spaces, and all evidence APIs are $0.
Vercel and the Oracle box are already paid for.

### 5.2 Claim extraction

Input: transcript + OCR timeline + caption + top comments. Output: atomic, self-contained,
timestamped claims, each tagged with `type` (factual / statistical / causal / prediction /
opinion), `checkworthiness` 0–1 (ClaimBuster-style — jokes and opinions filtered out),
`entities`, `time_scope`, `geo_scope`, and a `verbatim_span` + `t_start`/`t_end` so the UI can
jump to that moment in the video.

JSON-schema-constrained output, few-shot, measured against a hand-built eval set.

---

## 6. Evidence retrieval

Tiered, most authoritative first. All free, all called from the box as plain HTTP.

**Tier 1 — existing fact checks.** **Google Fact Check Tools API** (`claims:search`), free with
an API key, queries the global ClaimReview corpus. If a professional fact-checker already ruled
on this claim, that's the strongest and cheapest evidence available.

**Tier 2 — structured sources, routed by claim type.**
Science/health → Europe PMC, PubMed E-utilities, Semantic Scholar, OpenAlex, Crossref, arXiv.
Stats/economics → World Bank, Our World in Data, IMF, Eurostat.
General facts → Wikipedia REST + Wikidata SPARQL (excellent for entity grounding).
News/events → GDELT 2.0.

**Tier 3 — open web.** **SearXNG self-hosted** (Docker, unlimited, $0) as primary — it's a
metasearch proxy, negligible CPU, so it doesn't violate the no-processing rule. **Tavily free**
(1,000/mo, LLM-optimised) as quality fallback; **Brave free credit** for news fan-out. Page
extraction via **trafilatura** (pure-Python, milliseconds, fine on the box).

**Ranking:** BM25 (Postgres full-text) + dense vectors in **pgvector**. Embeddings come from a
hosted endpoint (Gemini `text-embedding` free tier, or Cloudflare Workers AI free daily
allocation) — not computed locally. Every fetched document is cached by URL hash and embedded
once, so the corpus compounds: later reels get faster *and* better.

---

## 7. Verification & scoring

### 7.1 Per-claim verdict

Two-pass, so the model can't hallucinate agreement:

1. **Stance pass** — each retrieved passage labelled supports / refutes / neutral w.r.t. the
   claim. Batched, cheap, auditable.
2. **Adjudication pass** — given the claim plus stance-labelled passages *with source
   credibility attached*, emit: verdict ∈ {true, mostly_true, mixed, mostly_false, false,
   unverifiable, opinion}, confidence 0–1, a 2-sentence rationale, and **citations that must be
   a subset of the supplied passage IDs** — enforced in code; a citation outside the input set
   fails validation and triggers a retry. This is what makes principle #1 mechanical rather
   than aspirational.

### 7.2 Overall validity score

```
validity = 100 × Σ(wᵢ · verdict_valueᵢ · checkworthinessᵢ) / Σ(wᵢ · checkworthinessᵢ)
           adjusted by manipulation_penalty(forensics_confidence)
```

Shown in the UI as an expandable formula, always alongside: claims checked, claims
unverifiable, and the confidence interval. A reel with 2 checked and 8 unverifiable claims
must never display a confident 85%.

### 7.3 Political lean

The most subjective output — be transparent about method.

- **Two independent estimators, reported separately:** *framing lean* (scored against a
  published written rubric, validated on a hand-labelled calibration set) and *source-mix lean*
  (from the bias ratings of outlets cited or corroborating).
- **Two axes** (economic, social/cultural), not one — a single left-right line is US-centric
  and loses information on non-US content.
- Bias data: **MBFC Data API** (10k+ ratings) or **AllSides** (1,400+ outlets). ⚠️ Both need a
  licence for commercial/public redistribution — fine personally, must be resolved before
  launch. Free alternative: build your own outlet-lean table from academic datasets.
- Always render with a confidence band and a "how we compute this" link. Emit
  `not_applicable` on non-political reels — no score is better than a meaningless one.

### 7.4 Creator credibility

- **Track record** — rolling accuracy across every reel of theirs you've analysed. Requires ≥5
  analysed reels before display; below that, "insufficient history".
- **Account signals** — from `business_discovery`: follower count, media count,
  engagement-ratio anomalies, posting cadence, verification.
- **Recycled-content signal** — keyframe pHash matched against your corpus: *"this footage
  previously appeared in a 2023 reel about a different event."*
- Public data only.

---

## 8. Report & visualisation

**Stack:** Next.js (App Router) + TypeScript on Vercel free tier, with design tokens in
plain CSS custom properties.

**No chart library.** Recharts and visx were the plan, but five of the six forms here are
bespoke (an interval-bearing hero figure, a video-timeline scrubber, a 2-axis lean plot
with a confidence region, confidence-weighted signal bars) and the sixth is a radar —
none is what a chart library makes easy. They are ~400 lines of direct SVG in
[`components/charts/`](web/components/charts/), which is SSR-safe by construction, ships
zero extra bytes, and gives exact control over the mark specs. Revisit if the chart set
grows past a dozen.

**Verdict colour is a diverging scale, and the palette was validated rather than chosen.**
Green↔red is the obvious mapping and it fails: the validator measures deutan ΔE 5.2
between the poles — invisible to red-green colourblind readers, who are the single
largest accessibility group. Blue↔red measures 18.3 protan / 29.9 normal in light and
19.2 / 29.0 in dark, passing every gate in both modes. Two steps per arm cannot clear the
dark-mode lightness band, so `true` vs `mostly_true` is carried by fill opacity plus an
always-present text label — colour never travels alone.

1. **Verdict header** — overall validity gauge, one-sentence summary, confidence band.
2. **Sub-score radar** — factual accuracy / source quality / manipulation integrity / creator
   track record / transparency.
3. **Claim timeline** *(visx)* — video scrubber with claims plotted at their timestamps,
   colour-coded by verdict; click jumps to that moment and opens the evidence cards.
4. **Claim table** — verdict, confidence, rationale, citations with source-credibility chips.
5. **Political lean** — 2-axis scatter with confidence ellipse + source-mix bar.
6. **Forensics panel** *(visx)* — keyframe strip with per-frame anomaly heat, per-signal
   breakdown, C2PA badge, audio-spoof meter.
7. **Creator card** — historical accuracy sparkline, reels analysed, notable past verdicts.
8. **Methodology & limitations** — permanently visible, not buried.

Export: shareable permalink, PDF, and an OG image card (the share card is the growth loop —
and the thing the mention-bot replies with).

> Invoke the `dataviz` skill before writing chart code — colour, form, and dark-mode rules for
> the whole set live there.

---

## 9. Deployment

### 9.1 The box, as measured

```
Ubuntu 22.04.5 · aarch64 (Ampere A1) · 4 cores · 23 GB RAM · 165 GB free · no GPU
Docker 29.6.2 · Python 3.10 · Node 24
In use: :80/:443 nginx · :3000 node · :6379 redis · :8081 :8082 :8090 :8095 :8787
Free range for this project: 8100–8110
```

Under the orchestrator-only design, none of this is a constraint any more — the workload is
I/O-bound. ARM64 image availability stops mattering too, since no ML images get deployed.

### 9.2 Compose stack

```yaml
api:       FastAPI + uvicorn        :8100   # REST, SSE, IG webhooks
worker:    ARQ workers (conc. 2)            # HTTP orchestration only
postgres:  postgres:17 + pgvector   :8101   # localhost only
searxng:   searxng/searxng          :8102   # localhost only
redis:     reuse existing :6379, separate DB index
```

nginx (already on :443) proxies `myth-buster.<domain>` → `:8100` with Let's Encrypt. The
webhook endpoint must be public HTTPS with signature verification — Meta requires it.

- **Queue:** ARQ (async-native, Redis-backed, far lighter than Celery for this shape).
- **Migrations:** Alembic. **ORM:** SQLAlchemy 2.x async.
- **Progress:** SSE driven by per-stage Redis pubsub. The pipeline takes 30–90 s, so live
  stage-by-stage progress is functional, not polish.
- **Media storage:** local disk to start; **Cloudflare R2 free tier** (10 GB, zero egress fees)
  once you want signed URLs the HF Space can pull without hitting your bandwidth.
- **CI:** GitHub Actions → GHCR → SSH deploy. **Observability:** structured JSON logs +
  Sentry free tier. **Backups:** nightly `pg_dump` into `~/backups` (existing convention).

### 9.3 Caching

Report cache keyed by `(canonical shortcode, pipeline_version)`. Evidence documents cached by
URL hash with TTL. LLM responses cached by prompt hash — which makes prompt iteration nearly
free and directly feeds DeepSeek's 98% cache-hit discount.

---

## 10. Security & abuse

- **SSRF guard** on ingestion: allowlist Instagram/Meta CDN hosts, block private CIDRs, cap
  redirects. A URL-fetching backend is the classic SSRF target.
- **Webhook signature verification** (`X-Hub-Signature-256`) on every Meta callback, plus
  replay protection. An unverified webhook endpoint is an open injection point into your queue.
- **Untrusted media never executes on the box** — it's forwarded to APIs. The HF Space that
  does parse it runs sandboxed, off your infrastructure, on someone else's blast radius.
- Size/duration caps enforced *before* download; per-IP and per-account rate limits at the
  gateway (free-tier quotas are a shared resource, one abuser drains the day).
- Media retention: delete source video after N days, keep derived artefacts only (transcript,
  hashes, frames) — smaller footprint, better copyright posture.
- **Bot-account hygiene:** the Professional account holds tokens that can read and reply on
  Instagram. Long-lived tokens in `.env` (not git), rotated, with a documented revoke path.

---

## 11. Legal & ethical posture

- **Defamation exposure is real.** Verdicts must read as *automated analysis of specific claims
  with cited evidence*, never as statements about a person's character. Creator scores need
  visible methodology, a sample-size floor, and a correction/appeal route.
- **The mention-driven flow is your best legal position:** content arrives via Meta's own API
  at a user's explicit invitation. Prefer it, and keep the yt-dlp fallback flagged and minimal.
- **Reply etiquette:** when the bot replies publicly on someone's reel, it is publishing an
  accusation. Reply with a neutral link ("analysis: <url>"), never a verdict in the comment
  text. Rate-limit per creator. Honour opt-outs.
- **Copyright:** short retention + transformative analysis + no redistribution of the original.
- **Bias-rating licences:** MBFC/AllSides need a licence for public use.
- **Your own bias:** the lean rubric is an editorial artefact. Publish it, version it, let
  users see how their reel scored against it.

---

## 12. Data model (core tables)

```
creators   id, platform, handle, display_name, ig_user_id, followers, is_professional,
           verified, first_seen, accuracy_score, reels_analysed
media      id, platform, shortcode, url_hash, ingest_path(mention|discovery|oembed|ytdlp),
           duration, posted_at, creator_id, caption, storage_key, phashes[]
reports    id, media_id, pipeline_version, status, validity_score,
           lean_economic, lean_social, lean_confidence,
           forensics_score, forensics_confidence, created_at
claims     id, report_id, text, type, checkworthiness, t_start, t_end,
           source(asr|ocr|caption), verdict, confidence, rationale
evidence   id, claim_id, url, title, publisher, publisher_credibility, snippet,
           stance, retrieved_at, embedding vector(1024)
forensics  id, report_id, signal, raw_score, calibrated_prob, detail jsonb
llm_calls  id, report_id, stage, model, prompt_hash, tokens_in, tokens_out, cached, latency_ms
mentions   id, ig_comment_id, ig_media_id, requester, received_at, report_id, replied_at
```

---

## 13. Roadmap

**Phase 0 — Meta setup (start immediately, runs in parallel).** Create the Professional
account + Facebook Page + Meta app. Build against Standard Access (your own account) to
produce the demo and screen recordings App Review requires, then submit. 2–6 weeks of latency
you want to be spending in the background.

**Phase 1 — the spine (1–2 weeks).** Pasted URL → `business_discovery` (yt-dlp fallback) →
Groq transcript → DeepSeek claim extraction → Google Fact Check + SearXNG → DeepSeek
adjudication → JSON report. Plain page, no charts. *Goal: prove verdict quality on ~20 real
reels.*

**Phase 2 — the report.** Full chart UI, SSE progress, claim timeline, evidence cards,
Postgres persistence, permalinks, OG cards.

**Phase 3 — video + forensics.** Gemini video pass (OCR, visual-audio consistency), HF Space
forensics service, C2PA, pHash recycled-footage detection, calibration curves.

**Phase 4 — scoring depth.** Political lean (both axes), creator track record, source
credibility.

**Phase 5 — distribution.** Mention bot goes live (post-review) → DM bot → PWA share target →
browser extension.

**Build the eval set in Phase 1.** ~50 reels with hand-written ground truth, re-scored on every
pipeline change. Use Hermes/GPT-5.6 on the box to help label it — that's the right job for a
dev-time agent. Without it you cannot tell whether a prompt tweak helped, and "feels better"
is a trap in exactly this kind of system.

---

## 14. Tooling summary

| Layer | Choice | Cost | Where it runs |
|---|---|---|---|
| Ingestion | Meta Graph API (mentions, business_discovery, oEmbed); yt-dlp fallback | free | oracle-dev |
| Transcription | Groq `whisper-large-v3-turbo` | free (2k req/day) | Groq |
| Video/OCR/visual | Gemini 2.5 Flash / Flash-Lite File API | free (250–1000 RPD) | Google |
| Text reasoning | **DeepSeek V4 Flash** (1M ctx, cache-hit 98% off) | ~$0.012/reel | DeepSeek |
| Overflow LLM | Groq, Cerebras, OpenRouter `:free` | free | hosted |
| Forensics | ONNX detectors, c2pa-python, AASIST, pHash | free | HF Space |
| Deep scan (opt-in) | Reality Defender free tier | free (50/mo) | hosted |
| Embeddings | Gemini embeddings / Cloudflare Workers AI | free | hosted |
| Search | SearXNG self-hosted, Tavily free, Brave free | free | oracle-dev |
| Evidence | Google Fact Check Tools, Wikipedia/Wikidata, Europe PMC, Semantic Scholar, OpenAlex, Crossref, GDELT, World Bank, OWID | free | hosted |
| Bias data | MBFC / AllSides (licence caveat) | free-ish | hosted |
| Backend | FastAPI, ARQ, SQLAlchemy 2, Alembic, trafilatura | free | oracle-dev |
| Data | Postgres 17 + pgvector, Redis, Cloudflare R2 | free | oracle-dev / CF |
| Frontend | Next.js + TS + CSS custom properties + hand-rolled SVG charts | free | Vercel |
| Dev-time agent | Hermes (GPT-5.6) — eval labelling, prompt iteration, ops | existing | oracle-dev |

---

## 15. Known limitations to state in the product

1. Deepfake detection is unreliable on unseen generators and degraded by Instagram's
   re-encoding. It is *one signal*, never the verdict.
2. Political lean is inherently subjective; the rubric is Western-leaning by default.
3. Non-English and code-mixed reels (Hinglish, Malayalam-English) get lower ASR accuracy and
   much thinner evidence coverage.
4. Claims under 24 hours old often have no fact-check or literature coverage → `unverifiable`,
   which is the correct and honest answer.
5. Creator credibility is noise below ~5 analysed reels.
6. **Personal (non-Professional) accounts are not reachable via any sanctioned API.** Those
   reels either fall back to yt-dlp or are declined — a real, permanent coverage gap.
7. Free-tier quotas cap throughput at roughly 250 full video analyses/day. The report cache is
   what keeps that from being felt.

---

### Sources consulted (Aug 2026)

[DeepSeek V4 Flash pricing](https://deepseek.ai/pricing) ·
[DeepSeek V4 guide](https://codersera.com/blog/deepseek-v4-complete-guide-2026/) ·
[Instagram Official APIs reference (Apr 2026)](https://gist.github.com/jameschapman2c/65eff9f54a2d350b17a6ce5127b9fe42) ·
[Meta: Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/) ·
[Meta: Instagram webhooks](https://developers.facebook.com/docs/instagram-platform/webhooks) ·
[Instagram Graph API 2026 guide](https://zernio.com/blog/instagram-graph-api) ·
[Groq free tier limits 2026](https://www.grizzlypeaksoftware.com/articles/p/groq-api-free-tier-limits-in-2026-what-you-actually-get-uwysd6mb) ·
[Groq pricing 2026](https://tokenmix.ai/blog/groq-api-pricing) ·
[Gemini API free tier limits 2026](https://harboratory.com/gemini-api-free-tier-limits-in-2026-explained/) ·
[HF Inference API free tier](https://klymentiev.com/blog/huggingface-inference-api) ·
[Reality Defender free API tier](https://www.realitydefender.com/insights/reality-defender-launches-free-access-to-deepfake-detection-api) ·
[Deepfake detection APIs 2026](https://www.edenai.co/post/best-deepfake-detection-apis-image-and-video-verification) ·
[AI-generated video detection survey](https://arxiv.org/abs/2601.11035) ·
[Google Fact Check Tools API](https://developers.google.com/fact-check/tools/api) ·
[C2PA Python library](https://opensource.contentauthenticity.org/docs/c2pa-python/) ·
[Free web search APIs for agents 2026](https://parallel.ai/articles/best-free-web-search-api) ·
[MBFC Data API](https://mediabiasfactcheck.com/mbfcs-data-api/) ·
[AllSides Bias Ratings API](https://www.allsides.com/tools-services/bias-ratings-license-api) ·
[Best React chart libraries 2026](https://blog.logrocket.com/best-react-chart-libraries-2026/)
