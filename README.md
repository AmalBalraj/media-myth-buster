# Media Myth Buster

Rate the validity of a reel or other social media content, using transcription plus
video analysis. Paste an Instagram reel link — or @mention the bot on any public reel —
and get a claim-by-claim credibility report: transcript and on-screen text, each
factual claim adjudicated against cited evidence, political framing on two axes,
manipulation and provenance signals, and the creator's track record.

**Where verdicts come from:** every judgement is tied to sources you can open. When
retrieval finds nothing, the claim is reported as `unverifiable` — never guessed at,
and never scored as false. That rule is enforced in code, not left to the prompt: a
citation the model invents is dropped, and a verdict left with no valid citation is
downgraded automatically ([runner.py](backend/app/pipeline/runner.py)).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and the reasoning behind each
tool choice.

---

## 1. Quick start (local)

```bash
cp .env.example .env          # then fill in the keys below
docker compose up -d postgres redis searxng
cd backend && uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 8100 &
.venv/bin/arq app.worker.WorkerSettings &
cd ../web && npm install && npm run dev      # http://localhost:3001
```

Want to see the UI before wiring up any provider?

```bash
cd backend && .venv/bin/python -m scripts.seed_demo
```

That inserts a complete report — including the awkward cases (an unverifiable claim,
an opinion, low-confidence forensics) — and prints its URL.

### Keys you need

| Key | Cost | Needed for |
|---|---|---|
| `DEEPSEEK_API_KEY` | ~$0.012/reel | Claim extraction, adjudication, lean, summary |
| `GROQ_API_KEY` | free, 2k req/day | Transcription (Whisper v3 Turbo) |
| `GEMINI_API_KEY` | free, 250 RPD | Video understanding + on-screen text OCR |
| `GOOGLE_FACTCHECK_API_KEY` | free | The ClaimReview corpus — highest-value evidence |
| `IG_ACCESS_TOKEN`, `IG_USER_ID` | free | Instagram ingest (see §2) |
| `FORENSICS_URL` | free | Optional off-box deepfake/manipulation service |

Everything degrades gracefully: with no `GEMINI_API_KEY` you lose on-screen text but
still get transcript-based claims; with no `FORENSICS_URL` the manipulation panel says
so rather than fabricating a score. `GET /api/health` reports which providers are live.

---

## 2. Getting reels in

Two independent paths. **Pick one — you do not need both.**

### Path A — yt-dlp (personal use; nothing to set up)

```bash
ENABLE_YTDLP_FALLBACK=true          # already the default in .env.example
YTDLP_COOKIES_FILE=/path/to/cookies.txt
```

No Meta account, no app, no review. Most reels need a session, so export cookies in
Netscape format from a **burner** Instagram account (never your own) — a browser
extension like "Get cookies.txt" does this, or `yt-dlp --cookies-from-browser chrome`.
Without cookies Instagram returns an empty media response for most posts.

This violates Instagram's ToS and the extractor breaks periodically, so it is fine for
a private tool and unsuitable for anything public-facing. When Graph credentials are
absent the ladder skips those rungs entirely rather than failing through them.

### Path B — Meta Graph API (only for the public @mention bot)

Needed only if you want the bot to answer @mentions on other people's reels, or to run
this as a public product. Requires a **Professional (Business or Creator) account linked
to a Facebook Page** — a personal account will not work, since no sanctioned API exposes
one. All of it is free.

> Note: this is **`developers.facebook.com`**, which is unrelated to *Meta Model API*
> (`llama.developer.meta.com`, Meta's LLM product — region-restricted, and not used
> anywhere in this project).

1. **Create the Instagram account** for the bot (e.g. `@yourmythbuster`).
2. **Switch it to Professional**: Settings → Account type and tools → Switch to
   professional account → pick *Creator* or *Business*.
3. **Create a Facebook Page** at [facebook.com/pages/create](https://facebook.com/pages/create).
   It can be empty; it exists to hold the API permissions.
4. **Link them**: Instagram → Settings → Account type and tools → Sharing to other
   apps → Facebook → connect the Page. (Or from the Page: Settings → Linked accounts.)
5. **Create a Meta app** at [developers.facebook.com/apps](https://developers.facebook.com/apps)
   → type *Business* → add the **Instagram** product.
6. **Generate a token** in Graph API Explorer with `instagram_business_basic` and
   `instagram_business_manage_comments`, then exchange it for a long-lived token.
   Put it in `IG_ACCESS_TOKEN`; put the IG user id in `IG_USER_ID`.
7. **Verify**:
   `curl "https://graph.facebook.com/v23.0/$IG_USER_ID?fields=username&access_token=$IG_ACCESS_TOKEN"`

At this point you have **Standard Access** — you can read *your own* account. That is
enough to build, and to record the demo videos App Review requires.

### App Review (needed to read other people's reels)

Submit for **Advanced Access** on the two permissions above. Meta requires business
verification, a live app with a privacy policy and a data-deletion endpoint, a working
demo, and a screen recording proving each permission's API call. Budget **2–6 weeks**.

Nothing else in the roadmap blocks on this, so submit early and keep building.

### Webhook (the @mention flow)

Point the Instagram product's webhook at `https://<your-host>/api/webhook/instagram`,
subscribe to the `mentions` field, and set `META_WEBHOOK_VERIFY_TOKEN` to match. Every
callback's `X-Hub-Signature-256` is verified before anything is queued — an unverified
webhook endpoint is an open injection point into the job queue.

---

## 3. How a reel becomes a report

```
resolve ──▶ download ──▶ ┌ Groq Whisper      (transcript)
                         ├ Gemini File API   (on-screen text, visual consistency)
                         └ HF Space          (forensic signals)   [optional]
                                  │
                    DeepSeek claim extraction
                                  │
              evidence retrieval per claim, tiered:
              fact-checks ▸ structured sources ▸ open web
                                  │
                    DeepSeek adjudication  ──▶  citations enforced in code
                                  │
                  validity · lean · creator scoring
```

The server never transcodes, extracts frames, or runs a model. Groq's transcription
endpoint takes mp4 directly and Gemini samples frames server-side, so there is no
ffmpeg step anywhere in the happy path.

**Ingest ladder** — each rung more legitimate than the one below:

1. `mentioned_media` via the mentions webhook — works on media you don't own
2. `business_discovery` — any Professional account's public media, by username
3. oEmbed — thumbnail and author handle, app-token only
4. yt-dlp — off by default; the only route to personal-account reels, and against ToS

---

## 4. Layout

```
backend/app/
  ingest/       the ladder above, all emitting one MediaBundle contract
  providers/    deepseek · groq_asr · gemini_video · forensics (+ calibration)
  evidence/     sources (tiered) · credibility · retrieve (cache + ranking)
  pipeline/     prompts · runner (stages, citation enforcement)
  scoring/      validity (published formula) · creator (track record)
  api/          routes_reports (REST + SSE) · routes_webhook (signed)
web/
  components/charts/  Validity.tsx · Signals.tsx
  lib/verdicts.ts     the diverging verdict scale
eval/           cases.jsonl + run_eval.py  ← build this before tuning prompts
```

## 5. Tests

```bash
cd backend && .venv/bin/python -m pytest tests -q     # 48 tests
cd web && npx tsc --noEmit && npx next build
```

The suite covers what must not silently break: unverifiable claims are excluded rather
than scored as half-true, low-confidence forensics cannot move the score, hallucinated
citations are dropped, the SSRF guard rejects cloud-metadata and suffix-confusion
hosts, and webhook signatures are verified properly.

## 6. Known limitations

Stated in the product too, at `/methodology`:

- Deepfake detection is unreliable on unseen generators and degraded by Instagram's
  re-encoding. It is one signal, never the verdict.
- Political lean is subjective; the rubric is Western-leaning and is published in full.
- Non-English and code-mixed audio gets lower ASR accuracy and thinner evidence.
- Claims under 24–48 hours old are often genuinely unverifiable.
- Creator credibility is hidden below 5 analysed reels.
- Personal Instagram accounts are unreachable via any sanctioned API.

## 7. Before making this public

- MBFC/AllSides bias data needs a licence for public use — the shipped table is a small
  hand-curated placeholder ([credibility.py](backend/app/evidence/credibility.py)).
- Creator scores are a defamation surface: keep the sample-size floor, the published
  methodology, and a correction route.
- The bot replies with a neutral link, never a verdict in the comment text. A bare
  "FALSE" in someone's comment section strips away every caveat and citation.
