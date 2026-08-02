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
ENABLE_YTDLP_FALLBACK=true                            # already the default
YTDLP_COOKIES_FILE=/data/instagram-cookies.txt        # container path, see below
```

No Meta account, no app, no review. Most reels need a session, so you need a cookie
export from a **burner** Instagram account (never your own).

**Generate the cookies on your own machine, never on the server.** Instagram scores
datacenter IPs as high-risk; logging in from the Oracle box is a fast route to a
checkpointed or banned account. The session then travels to the server as a file.

1. Create the burner account on your phone or laptop. Let it sit a day and follow a
   few accounts — a brand-new account that immediately starts fetching looks exactly
   like what it is.
2. Log into it in a browser, ideally a separate profile so it doesn't collide with
   your own session. **Do not log out afterwards** — that invalidates the `sessionid`
   you are about to export.
3. Export cookies for `instagram.com` in **Netscape format** using a "cookies.txt"
   browser extension. A JSON export will not work. Locally you can skip the extension
   with `yt-dlp --cookies-from-browser chrome --cookies cookies.txt`.
4. Ship it:

```bash
./deploy/push-cookies.sh ~/Downloads/instagram-cookies.txt
```

That validates the file (Netscape format, has instagram.com cookies, has a live
`sessionid`, warns if it expires within a week), copies it to the server's `data/`
directory as mode 600, points `YTDLP_COOKIES_FILE` at the **container** path
`/data/instagram-cookies.txt`, and restarts the workers. `data/` is excluded from
rsync, so deploys never clobber it.

> The path must be a container path. `data/` on the host is mounted at `/data`, so a
> host path like `/home/amal/...` in `.env` will not resolve inside the worker. The
> ingest adapter checks this at call time and says so rather than surfacing a generic
> extraction failure.

Sessions last weeks, not months, and can be cut short by a checkpoint. When reels
start failing with "empty media response", open the burner in a browser, clear any
prompt, and re-run `push-cookies.sh`.

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
4. yt-dlp — the only route to personal-account reels, and against ToS. Default for
   personal use (§2 Path A); the rungs above are skipped entirely when unconfigured

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

## 5. Deployment

Live at **https://myth-buster.devmindset.in** on `oracle-dev`, behind the same
nginx + certbot setup as the other sites on that box.

```bash
./deploy/deploy.sh            # sync, rebuild, restart, health-check
./deploy/deploy.sh --api      # backend only (skips the slow web image build)
./deploy/deploy.sh --web      # frontend only
./deploy/deploy.sh --logs     # tail logs afterwards
```

`deploy.sh` runs the test suite before it ships anything, rsyncs the tree, rebuilds,
and polls `/api/health` until the site answers. **The server's `.env` is excluded from
the sync** — it holds the API keys and is the one file a deploy must never clobber.

First-time setup on a new host (idempotent):

```bash
./deploy/bootstrap.sh         # remote dirs, .env from example, nginx site
ssh oracle-dev 'nano /home/amal/myth-buster/.env'    # add API keys
./deploy/deploy.sh
./deploy/tls.sh               # certbot --nginx, after the site answers on :80
```

Override the target with `MYTH_HOST`, `MYTH_REMOTE_DIR`, `MYTH_DOMAIN`.

**Topology.** Everything binds to `127.0.0.1`; nginx is the only public entrance.
`/api/*` proxies to FastAPI on `:8100`, everything else to Next.js on `:3005`. The SSE
endpoint gets its own regex location with `proxy_buffering off` and a 900s read timeout
— a prefix location there would both break streaming and make nginx 301 the
`/api/reports` collection route.

The deploy account is not in the `docker` group but has passwordless sudo, so the
scripts detect which is available rather than assuming.

```bash
ssh oracle-dev 'cd myth-buster && sudo docker compose ps'
ssh oracle-dev 'cd myth-buster && sudo docker compose logs -f worker'
ssh oracle-dev 'cd myth-buster && sudo docker compose exec -T api python -m scripts.seed_demo'
```

## 6. Tests

```bash
cd backend && .venv/bin/python -m pytest tests -q     # 48 tests
cd web && npx tsc --noEmit && npx next build
```

The suite covers what must not silently break: unverifiable claims are excluded rather
than scored as half-true, low-confidence forensics cannot move the score, hallucinated
citations are dropped, the SSRF guard rejects cloud-metadata and suffix-confusion
hosts, and webhook signatures are verified properly.

## 7. Known limitations

Stated in the product too, at `/methodology`:

- Deepfake detection is unreliable on unseen generators and degraded by Instagram's
  re-encoding. It is one signal, never the verdict.
- Political lean is subjective; the rubric is Western-leaning and is published in full.
- Non-English and code-mixed audio gets lower ASR accuracy and thinner evidence.
- Claims under 24–48 hours old are often genuinely unverifiable.
- Creator credibility is hidden below 5 analysed reels.
- Personal Instagram accounts are unreachable via any sanctioned API.

## 8. Before making this public

- MBFC/AllSides bias data needs a licence for public use — the shipped table is a small
  hand-curated placeholder ([credibility.py](backend/app/evidence/credibility.py)).
- Creator scores are a defamation surface: keep the sample-size floor, the published
  methodology, and a correction route.
- The bot replies with a neutral link, never a verdict in the comment text. A bare
  "FALSE" in someone's comment section strips away every caveat and citation.
