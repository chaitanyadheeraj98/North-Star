# Adaptive LLM Router

**Use the right amount of AI for the task.**

A local-first web application that answers one question:

> Given this coding task, which model and reasoning effort should I use
> within Codex, and which should I use within Claude?

It then learns from what actually happened, so the next answer is better.

Not the cheapest model. Not the strongest model. The **minimum sufficient
configuration**: the least quota-intensive option that is still reliable enough
to finish the task correctly.

---

## Contents

1. [Why this exists](#1-why-this-exists)
2. [Architecture](#2-architecture)
3. [Why Pi, and why Pi does not decide](#3-why-pi-and-why-pi-does-not-decide)
4. [Requirements](#4-requirements)
5. [Setup on Windows](#5-setup-on-windows)
6. [Setup on macOS and Linux](#6-setup-on-macos-and-linux)
7. [Running your first task](#7-running-your-first-task)
8. [Importing your first receipt](#8-importing-your-first-receipt)
9. [How the routing works](#9-how-the-routing-works)
10. [How the learning works](#10-how-the-learning-works)
11. [Configuration](#11-configuration)
12. [Data, reset and backup](#12-data-reset-and-backup)
13. [Tests](#13-tests)
14. [Troubleshooting](#14-troubleshooting)
15. [Security](#15-security)
16. [Privacy](#16-privacy)
17. [Known V1 limitations](#17-known-v1-limitations)
18. [Repository layout](#18-repository-layout)

---

## 1. Why this exists

Both failure modes cost you quota.

**Underpowering.** A cheap model on a hard task makes a mistake, you debug it,
it retries, you escalate to a stronger model anyway. Total burn: far more than
starting stronger would have cost.

**Overpowering.** A premium model at maximum reasoning on a one-line copy
change. Total burn: dozens of times what was needed.

The objective is neither "always cheap" nor "always strong". It is:

```
lowest expected effective burn
subject to an acceptable reliability threshold
```

where *effective burn* includes the expected cost of retries, debugging,
escalation and outright failure, not just the first attempt.

### The loop

```
TASK
 ↓
Pi classifies it into a structured TaskFingerprint
 ↓
Deterministic backend picks model + effort independently for each provider
 ↓
You copy the handoff into Claude Code or the Codex CLI
 ↓
You do the work
 ↓
The router-outcome skill writes an execution receipt
 ↓
You paste the receipt back
 ↓
Statistics update; the next similar task routes better
```

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       YOUR COMPUTER                         │
│                                                             │
│   Browser  http://localhost:3000                            │
│        │                                                    │
│        ▼                                                    │
│   ┌──────────────────┐                                      │
│   │ React frontend   │  Docker (nginx serves + proxies)     │
│   └────────┬─────────┘                                      │
│            │ /api                                           │
│            ▼                                                │
│   ┌──────────────────────────┐                              │
│   │ FastAPI backend          │  Docker                      │
│   │                          │                              │
│   │  Routing engine          │  deterministic               │
│   │  Learning engine         │  deterministic               │
│   │  Model registry          │  YAML                        │
│   │  Receipt validator       │                              │
│   └──────┬───────────┬───────┘                              │
│          │           │                                      │
│          ▼           ▼                                      │
│      SQLite      host.docker.internal:31415                 │
│      ./data          │                                      │
│                      ▼                                      │
│               ┌──────────────┐                              │
│               │  Pi Bridge   │  host process, loopback only │
│               └──────┬───────┘                              │
│                      ▼                                      │
│                  Pi Agent                                   │
│                      │                                      │
│            your existing subscription                       │
│              ┌───────┴───────┐                              │
│              ▼               ▼                              │
│         ChatGPT/Codex     Claude                            │
└─────────────────────────────────────────────────────────────┘
```

Three processes: two containers and one host process. No cloud, no API keys of
ours, no per-task API bill.

### Why the Pi bridge lives outside Docker

Pi authenticates against your existing Claude Pro/Max or ChatGPT Plus/Pro
subscription through its own credential store, using an OAuth flow that needs a
browser. A container has neither those credentials nor a browser. Putting the
bridge on the host is not a compromise; it is the only arrangement where "use
the subscription you already pay for" actually works.

The backend reaches it at `host.docker.internal:31415`. On Linux, the
`host-gateway` entry in `docker-compose.yml` makes that name resolve too.

---

## 3. Why Pi, and why Pi does not decide

Pi has exactly one job: turn an unstructured task into a structured
`TaskFingerprint`.

You write:

> Re-key premium lead versions based only on recruiter identity and preserve
> source-email provenance across the persistence layer.

Pi returns:

```json
{
  "task_family": "backend_data_integrity",
  "complexity": 0.84,
  "regression_risk": 0.88,
  "database_reasoning": 0.91,
  "requirements_clarity": 0.93,
  "scope": "multi_file",
  "estimated_files": 5,
  "confidence": 0.91
}
```

Pi does **not** return "use Codex Sol at high effort". It never sees a provider
name, a model name, or an effort level. The analyzer prompt forbids it, and the
bridge validates the output against a schema that has no field for it.

### Why the decision is deterministic code

Three reasons, in order of importance:

1. **Consistency.** The same task must produce the same recommendation. An LLM
   asked "which model should I use?" will not do that, and a router that
   answers differently on Tuesday is worse than no router.
2. **Auditability.** Every recommendation comes with the arithmetic: the
   reliability bar, every configuration considered, why the cheaper one was
   rejected, why the stronger one was not worth it. That is only possible when
   a deterministic function produced it.
3. **Stability of the learning.** Pi produces the fingerprint. The outcome
   skill produces evidence. The backend calculates and updates the statistics.
   **Pi never writes routing weights.** An LLM allowed to adjust its own
   decision policy from single observations will drift, and nobody will be able
   to tell drift from improvement after the fact.

Learned statistics are **data**, not source-code mutations. Nothing in this
application rewrites its own config files.

---

## 4. Requirements

| Component | Needed for | Minimum |
| --- | --- | --- |
| Docker Desktop | frontend + backend | any current version |
| Node.js | Pi bridge | **22.6+** (it runs TypeScript directly) |
| Pi | task classification | `npm install -g @mariozechner/pi-coding-agent` |
| A subscription | Pi's model calls | Claude Pro/Max or ChatGPT Plus/Pro |

You do **not** need an Anthropic or OpenAI API key. Pi uses the subscription you
already have.

---

## 5. Setup on Windows

```powershell
.\scripts\setup.ps1
```

This checks prerequisites, creates `data\`, copies `.env.example` to `.env`,
installs the bridge dependencies, and reports which providers Pi holds
credentials for. It changes nothing outside the repository.

Then:

**Step 1 - authenticate Pi (once).**

```powershell
pi
```

Type `/login` and pick your provider. This is the only authentication step in
the whole system.

**Step 2 - start the web stack.**

```powershell
docker compose up -d
```

**Step 3 - start the Pi bridge.**

```powershell
.\scripts\start-pi.ps1
```

Leave it running. It prints which model it will classify with and whether the
credentials actually resolve.

**Step 4 - install the outcome skill.**

```powershell
.\scripts\install-skills.ps1
```

Preview what it would do first with `-WhatIfOnly`. It only ever touches a
directory named `router-outcome`.

**Step 5 - open the app.**

```
http://localhost:3000
```

---

## 6. Setup on macOS and Linux

Same sequence, shell scripts instead:

```bash
./scripts/setup.sh
pi                          # then /login
docker compose up -d
./scripts/start-pi.sh       # leave running
./scripts/install-skills.sh
```

---

## 7. Running your first task

1. Open <http://localhost:3000>. The dot in the top right shows the Pi bridge
   status.
2. Paste a real coding task into the box. Write it exactly as you would give it
   to Claude or Codex; the analyzer reads the same words the executing agent
   will.
3. Press **Analyze task** (or Ctrl+Enter).

You get (this is the real output for the data-integrity task below):

```
RECOMMENDED

CLAUDE
Opus 5
Low reasoning

Confidence 65%        Task family  Backend Data Integrity
Reliability 96.1%     Complexity   High
needs 95.7%           Regression   Very high
Expected burn 2.86    Fallback     Codex GPT-5.6 Sol (high)
```

A strong model at low effort is a perfectly normal recommendation and not a
bug: the task is precisely specified, so it needs capability rather than
searching. The router buys the capability and declines to pay for the
searching.

plus three plain-English blocks: **Why**, **Why not lighter?**, **Why not
stronger?**, and a **Show the arithmetic** toggle that reveals the fingerprint,
the reason codes, and every configuration that was considered with its
reliability and burn.

4. Choose a provider and press **Copy Codex handoff** or **Copy Claude handoff**. You get:

```
[LLM-ROUTER]
task_id=RT-000001
recommended_provider=claude
recommended_model=opus_5
recommended_effort=low
recommended_model_id=claude-opus-5
router_version=1.0.0
[/LLM-ROUTER]

TASK

<your task>

---
When the work is finished, run the router-outcome skill (or ask: "generate the
router result receipt") and paste the [LLM-ROUTER-RESULT] block back into the
router's Result page.
```

5. Select that model and effort in Claude Code or the Codex CLI, paste, and do
   the work.

The `task_id` in the metadata block is the only thing connecting the work back
to the routing decision. Keep it in the conversation.

---

## 8. Importing your first receipt

When the work is done, in the same conversation:

```
generate the router result receipt
```

The skill inspects what happened and emits:

```
[LLM-ROUTER-RESULT]
{ ... }
[/LLM-ROUTER-RESULT]
```

Copy the whole block, open the **Result** tab, paste, press **Import result**.

You will see what was recorded and what it changed:

> **Result learned**
> Outcome Success · Followed recommendation Yes · First pass Yes · Escalated No
> First recorded execution for claude/opus_5/low on backend_data_integrity. One
> data point does not move routing on its own; the Bayesian prior still
> dominates until several more arrive.

**Import the receipt even if you ignored the recommendation.** A deviation is
recorded as `recommendation_followed: false` and is some of the most valuable
evidence the system can get.

---

## 9. How the routing works

Eight steps, all deterministic, run independently within each provider.
There is no cross-provider winner, quota lookup, or subscription-credit check:

1. **Enumerate** every enabled provider × model × effort combination (37 by
   default).
2. **Score capability** for each, weighting the capability dimensions by what
   the task actually demands. A database-heavy task makes the database
   dimension dominate; a cosmetic task makes it almost irrelevant.
3. **Apply effort** as `capability + gain × (1 − capability)`. Sub-linear on
   purpose: a model with capability 0.70 reaches only 0.82 at max effort, well
   below a model whose base capability is 0.92. **Effort cannot substitute for
   capability.**
4. **Predict reliability** as `1 − scale × deficit² × difficulty^1.2`. Squaring
   the deficit is what separates a competent model from a very competent one as
   tasks get harder.
5. **Derive the required reliability** from the fingerprint's risk dimensions,
   not from the task family alone. Roughly 0.78 for cosmetic work through 0.98
   for concurrency and security.
6. **Discard** everything below the bar.
7. **Estimate effective burn** for what remains:

   ```
   base      = provider weight × model burn × effort prior
   + retry   = p(fail) × base × 0.9
   + debug   = expected debug cycles × base × 0.35
   + escalate= p(fail) × 0.25 × cost of redoing it properly
   + penalty = p(fail) × 1.5 × cost of redoing it properly
   ```

   Pricing failure against *what redoing the work costs* is what keeps the
   router honest at both ends. Being wrong about a cosmetic tweak is genuinely
   cheap, so cheap configurations are not punished into oblivion; being wrong
   about a migration is expensive, so the router will pay up front to avoid it.

8. **Take the lowest total.** If nothing clears the bar, take the most reliable
   option and say so plainly on screen.

Both provider results include a recommendation, reasoning effort, fallback, and
explanation. The same fingerprint, reliability threshold, and historical evidence
are used, but escalation costs, candidate rankings, and fallbacks stay within the
provider. The user chooses which handoff to copy. A fallback below the required
reliability is labelled; a provider with no routable configuration is unavailable.

The API returns `recommendation.codex` and `recommendation.claude`, with separate
`handoffs.codex` and `handoffs.claude`. `GET /api/tasks/{id}/handoff` requires
`?provider=codex` or `?provider=claude` for new decisions. Both decisions are stored
in the existing JSON column; legacy summary columns use `provider=independent`
and empty/zero placeholders, not an overall winner. Older decisions remain
readable and are marked `legacy`; missing historical recommendations are not
invented. Receipts compare against the recommendation for the executed provider.

### About the numbers

There is **no published, fixed token multiplier for effort levels** on either
provider. Both use adaptive reasoning: the same effort setting spends wildly
different amounts depending on the task. Anywhere this application needs a
number for effort intensity, it uses a clearly labelled **routing prior**, never
a claim about provider behaviour.

`backend/config/models.yaml` marks every field `[SOURCE]` (derived from the
published pricing and effort support in `reference/`) or `[PRIOR]` (our own
assumption). The `/api/models` endpoint and the Settings page repeat the
distinction.

---

## 10. How the learning works

No training, no fine-tuning, no LLM adjusting weights. Accumulated statistics
per `task family × provider × model × effort`.

**Bayesian smoothing.** The task-specific prior is combined with observed
outcomes through a Beta posterior:

```
alpha = prior_strength × prior + weighted successes
beta  = prior_strength × (1 − prior) + weighted failures
```

`prior_strength = 20` gives the evidence bands the design calls for:

| Executions | Influence |
| --- | --- |
| 1 | ~5% — prior dominates |
| 5 | 20% — weak |
| 20 | 50% — moderate |
| 50 | 71% — strong |

One success out of one attempt is never a 100% success rate, and no single
import can shift a recommendation by more than 0.12 reliability points.

**Recency weighting.** Models change, so old evidence counts for less:

| Age | Weight |
| --- | --- |
| 0–30 days | 1.00 |
| 31–90 | 0.85 |
| 91–180 | 0.65 |
| 181–365 | 0.40 |
| 365+ | 0.20 |

This is why routing recomputes from the raw execution rows rather than reading
a rollup: a rollup cannot be re-weighted as evidence ages.

**It learns in both directions.**

*Underpowering:* a configuration that keeps escalating or needing three rounds
of debugging gets a worse reliability estimate and a higher expected burn, so
the router starts stronger next time.

*Overpowering:* if a cheaper configuration matches an expensive one's measured
success rate at a fraction of the burn, the Analytics page says so under
"Patterns worth acting on", and the posterior moves the cheaper option up on
its own.

**Measured versus estimated.** Token counts are stored only when a provider
actually reported them (`usage_source: provider_reported`). Effective burn is
an estimate reconstructed from work signals and is labelled as such everywhere
it appears. Nothing in the system invents a token count.

---

## 11. Configuration

Everything routing-related is YAML, with comments explaining which numbers are
sourced facts and which are assumptions. There is no settings form for policy,
deliberately: a UI would strip that context and create a second source of
truth.

| File | Holds |
| --- | --- |
| `backend/config/models.yaml` | providers, models, supported efforts, burn, capability priors |
| `backend/config/routing.yaml` | effort priors, difficulty weights, reliability model, thresholds, learning, recency |
| `backend/config/task_families.yaml` | the 22 canonical task families and their definitions |

The directory is bind-mounted, so edit on the host and apply without a rebuild:

```bash
curl -X POST http://localhost:8000/api/models/reload
```

or press **Reload YAML** on the Settings page. Invalid config is rejected with a
specific message and the previous config keeps running.

### Update Models

Settings **Update Models** calls `POST /api/models/update`. The separate updater
reads only [OpenAI's Codex model catalog](https://github.com/openai/codex/blob/main/codex-rs/models-manager/models.json),
[Anthropic's model overview](https://platform.claude.com/docs/en/models/overview), and
[Anthropic's effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort).
It validates source formats, model identities, effort values, and the candidate
against the routing policy before writing. Redirects, malformed data, empty
catalogs, and updates that remove a provider's last routable configuration fail.

A changed registry receives a patch-version increment and an exact backup beside
`models.yaml`. Replacement is atomic; a reload failure restores the original file
and retains the loaded configuration. An unchanged registry is not rewritten.
Updates and manual reloads are serialized in the single backend worker used by
Docker Compose. Multiple backend workers require shared locking and cache reloads.

Existing keys, enabled states, capability priors, and burn priors are preserved.
Missing models are reported as unconfirmed and retained. New entries are disabled
with `review_required: true`, zero capability placeholders, and conservative burn
placeholders. Review their priors and effort support in YAML, set
`review_required: false` and `enabled: true`, then use **Reload YAML**. The updater
never infers capability from marketing text and does not call Pi or another LLM.

### Things worth tuning

**`burn_weight`** scales burn within a provider. Providers are evaluated
independently; changing Claude's weight cannot affect the Codex recommendation.

**`effort_policy.disabled_efforts`.** Defaults to `[none, ultra]`. `none`
disables deliberate reasoning entirely, which is unsuitable for agentic coding.
`ultra` is gated to eligible Codex users and can invoke additional agents, so
its consumption is unbounded from this application's point of view. Remove an
entry to let the router consider it.

**`required_reliability.base`** and the risk coefficients. Raise the base if the
router feels reckless; lower it if it feels paranoid.

**`family_affinity`.** Empty by default. The mechanism exists so that measured
evidence can be promoted into a per-model, per-family nudge later. It is empty
because inventing affinities would be indistinguishable from making up facts.

### Analyzer model

The model that **classifies** your tasks is chosen explicitly. It is not the
model that does the work; the router recommends that separately.

Two places set it, and the second wins:

**1. `.env` — the boot default.**

```
PI_PROVIDER=openai-codex
PI_MODEL=gpt-5.5
PI_THINKING_LEVEL=low
```

Run `pi --list-models` for the exact ids. `PI_THINKING_LEVEL` is Pi's
vocabulary for the classifier: `minimal`, `low`, `medium`, `high`, `xhigh`. It
is deliberately a different set from the router's effort levels (`none`
through `ultra`), which describe what the *executing* agent should use.

**2. Settings → Analyzer model — a per-install override.**

Pick a provider, model and thinking level from the dropdowns. The choice is
stored in the local database and sent to the bridge on every analysis, so it
takes effect on the next task with no restart. Leave a field on "Use bridge
default" to inherit it from `.env`.

Classification is a small structured judgement. It does not need a premium
model, and spending premium quota to decide how to spend quota would defeat the
purpose of the tool.

### The analyzer is never chosen for you

If the configured provider or model is unavailable or unauthenticated, the
bridge **fails and says so**. It does not substitute a different model, even
when other providers are authenticated and ready.

That restraint is deliberate and was added after a real defect. An earlier
version picked "the first authenticated provider whose credentials resolved",
and on a machine with three Pi logins it classified tasks with `kimi-coding`
while interactive Pi was signed in to `openai-codex`. Nothing was visibly
broken; the analyzer had simply changed underneath the user. Since the
fingerprint drives every routing decision downstream, a silent change of
analyzer is a silent change of routing behaviour, which is exactly the kind of
thing this application exists to make visible.

So a failure now looks like this, and lists what else is available without
taking any of it:

```
  analyzer : openai-codex / gpt-5.5 / low
  auth     : NOT READY (not_authenticated)

  Pi is not authenticated with "openai-codex".
  Run "pi" in a terminal and use /login to authenticate openai-codex.

  Authenticated alternatives (the bridge will NOT pick one for you):
    github-copilot: claude-haiku-4.5, claude-sonnet-4.5
    kimi-coding: kimi-for-coding
```

---

## 12. Data, reset and backup

Everything lives in `./data/router.db` (SQLite, bind-mounted into the backend).

**Back up:**

```powershell
Copy-Item .\data\router.db ".\data\router-$(Get-Date -f yyyyMMdd).db"
```

**Reset everything:**

```powershell
docker compose down
Remove-Item .\data\router.db*
docker compose up -d
```

**Rebuild the analytics rollup** without losing outcomes (after restoring a
partial backup or editing the database by hand):

```bash
curl -X POST http://localhost:8000/api/results/rebuild-statistics
```

Tables: `tasks`, `task_fingerprints`, `routing_decisions`, `executions`,
`execution_metrics`, `escalations`, `routing_statistics`, `app_settings`,
`model_registry_snapshots`.

Every fingerprint, decision and receipt is also stored as raw JSON alongside the
extracted columns, so nothing an analyzer or agent reported is lost because this
schema version had no field for it.

---

## 13. Tests

**Backend** (197 tests):

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

Covers contracts, config validation, the reliability and burn models, effort
filtering, deterministic selection, fallback behaviour, the no-eligible-route
path, Bayesian smoothing, recency weighting, receipt validation, deviation
handling, escalation, the learning update, analyzer selection reaching the
bridge, thirty reference-task routing bands, and the full end-to-end loop with
a mocked analyzer.

**Pi bridge** (35 tests):

```bash
cd pi-bridge
npm test          # analyzer selection, JSON extraction, schema validation
npm run typecheck
```

**Frontend** (15 tests):

```bash
cd frontend
npm test
npm run typecheck
```

**Everything, in one go** (PowerShell):

```powershell
cd backend;    python -m pytest;  cd ..
cd pi-bridge;  npm test;          cd ..
cd frontend;   npm test;          cd ..
```

---

## 14. Troubleshooting

**"Pi bridge unavailable" in the UI.**
The bridge is not running. Start it with `.\scripts\start-pi.ps1`. Check with
`curl http://127.0.0.1:31415/health`.

**The bridge starts but says `auth: NOT READY`.**
The configured provider cannot produce a token. `getAvailable()` only checks
that a credential exists; the bridge additionally probes whether it can
actually be used, which is why it can tell you at startup rather than failing
mid-analysis. Fix it with:

```
pi
/login
```

then restart the bridge, or `curl -X POST http://127.0.0.1:31415/reload`.

**The bridge is classifying with the wrong model.**
Check what it thinks it was told:

```bash
curl -s http://127.0.0.1:31415/health
```

`configured_provider` / `configured_model` / `configured_effort` are what it
was asked for, `configured_from` says where each value came from (`default`,
`env:PI_MODEL`, or `request`), and `actual_provider` / `actual_model` are what
a request would use right now. If configured and actual disagree, the note
explains why. If `configured_from` says `request`, a Settings override is
winning over `.env` — clear it on the Settings page.

**"Pi could not authenticate with the analyzer provider."**
Same cause, surfaced during an analysis. The detail line contains the provider's
own error message.

**`Bind for 0.0.0.0:8000 failed: port is already allocated`.**
Something else owns port 8000. Set `BACKEND_PORT=8010` in `.env` and run
`docker compose up -d` again. The frontend reaches the backend over the internal
Docker network, so only direct API access moves.

**Analysis is slow.**
It is a real model call. A long task on a reasoning model can take 30–90
seconds. Lower `PI_THINKING_LEVEL` or pick a faster `PI_MODEL`.

**"No task named RT-000123 exists in this router."**
The receipt's `task_id` does not match anything here. Usually the agent invented
one because the `[LLM-ROUTER]` block was not in its context. Re-paste the
handoff and regenerate the receipt.

**"The result block is not valid JSON."**
Copy the whole block including both `[LLM-ROUTER-RESULT]` markers.

**I want to work offline.**
Settings → "Use the offline keyword analyzer instead of Pi". It is much cruder
than Pi and says so, but it keeps the routing engine usable. There is also
"Fall back when Pi is unreachable", off by default so you always know which
analyzer classified a task.

**Docker on Linux cannot reach the bridge.**
`docker-compose.yml` already maps `host.docker.internal` to `host-gateway`. If
your Docker predates that, set `PI_BRIDGE_URL=http://172.17.0.1:31415` in
`.env`.

---

## 15. Security

- **The Pi bridge binds to `127.0.0.1` only.** Anything that can reach that port
  can spend your subscription quota. The bridge refuses to bind to any other
  address unless you explicitly set `PI_BRIDGE_ALLOW_REMOTE=1`.
- **Both containers bind to loopback.** The database holds your task text, which
  may be proprietary.
- **The analyzer runs with no tools at all.** No filesystem, no shell, no
  repository access. Classifying pasted text needs none of that, and the right
  amount of capability to hand a classifier is none. Pi runs with the
  permissions of the user who launched it, and project trust is not a sandbox.
- **No credentials pass through this application.** The backend knows one thing
  about Pi: a URL. Credentials live in Pi's own store and are never read,
  logged, proxied or stored in SQLite. The Settings page reports provider
  *names* only.
- **Pi output and receipt text are untrusted input** and are schema-validated
  before they reach the router or the statistics.

---

## 16. Privacy

Everything stays on this machine.

- No cloud database. One SQLite file in `./data`.
- No telemetry. No analytics. Nothing is reported anywhere.
- Pi calls your subscription provider to classify task text.
- Update Models fetches public official model information without sending task
  text, history, credentials, or files. Pi retains its no-tools security model.
- The reference documents in `reference/` seeded the registry once. They are not
  re-read on every task and are not sent anywhere.

Task text may contain proprietary code details. It is stored locally and sent
only to the analyzer, through your own subscription.

---

## 17. Known V1 limitations

Deliberately not built in V1:

- **No similar-task retrieval.** Learning is per task family. Two very different
  `backend_data_integrity` tasks share a statistics bucket. Embedding-based
  retrieval of the 30–50 most similar historical tasks is the V1.5 feature that
  would most improve precision.
- **No automatic receipt ingestion.** You copy a handoff out and paste a receipt
  back. Automating that needs CLI or IDE integration.
- **No automatic execution.** The router recommends; you run it.
- **Model updates require review for new models.** Discovery cannot establish
  capability priors. New entries remain disabled until reviewed.
- **Effort priors are priors.** Neither provider publishes fixed multipliers.
  Historical data corrects them over time; the priors themselves never
  self-update.
- **Capability priors are assumptions**, seeded from qualitative guidance, not
  from benchmarks.
- **Both hardest categories clamp to the same bar.** Concurrency and security
  both hit `required_reliability.max = 0.98`, so the router cannot currently
  distinguish "very hard" from "extremely hard". Raise the ceiling if you need
  it to.
- **`create_all`, not Alembic.** The schema is additive so far. A destructive
  migration would need Alembic wired in first.
- **Single user, no auth.** Everything is bound to loopback.
- **The offline analyzer is keyword-based.** It exists so the app still works
  when Pi is down, not as a replacement for semantic understanding.
- **`@mariozechner/pi-coding-agent` is being renamed** to
  `@earendil-works/pi-coding-agent`. The bridge pins the current package; the Pi
  adapter is one file (`pi-bridge/src/pi.ts`) when that move needs making.

---

## 18. Repository layout

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                    FastAPI app
│   │   ├── config.py                  YAML loading + cross-file validation
│   │   ├── api/                       tasks, results, history, analytics,
│   │   │                              models, pi, settings
│   │   ├── core/
│   │   │   ├── router/                THE DECISION
│   │   │   │   ├── engine.py          enumerate, filter, select
│   │   │   │   ├── scoring.py         difficulty and capability
│   │   │   │   ├── thresholds.py      required reliability
│   │   │   │   ├── reliability.py     prior + Bayesian posterior
│   │   │   │   ├── burn.py            effective burn
│   │   │   │   └── explain.py         deterministic prose
│   │   │   └── learning/              THE MEMORY
│   │   │       ├── statistics.py      recency-weighted aggregation
│   │   │       ├── bayesian.py        Beta-Binomial smoothing
│   │   │       ├── recency.py         age weighting
│   │   │       └── updater.py         rollup maintenance
│   │   ├── db/                        SQLAlchemy models + engine
│   │   ├── schemas/                   the contracts
│   │   └── services/                  analyzer, pi, task, routing,
│   │                                  receipt, analytics
│   ├── config/                        models.yaml, routing.yaml,
│   │                                  task_families.yaml
│   ├── tests/                         182 tests
│   └── Dockerfile
│
├── frontend/                          React + TypeScript + Vite + Tailwind
│   ├── src/pages/                     Router, Result, History, Analytics,
│   │                                  Settings
│   ├── nginx.conf                     serves the SPA, proxies /api
│   └── Dockerfile
│
├── pi-bridge/                         host process, loopback only
│   ├── src/
│   │   ├── server.ts                  /health /analyze /providers /models
│   │   ├── config.ts                  explicit analyzer configuration
│   │   ├── analyzer.ts                prompt, parse, one repair retry
│   │   ├── pi.ts                      the only Pi-specific file
│   │   ├── fingerprint.ts             schema validation
│   │   └── families.ts                reads the backend's canonical families
│   └── prompts/task-analyzer.md       the analyzer prompt
│
├── skills/router-outcome/             the canonical outcome skill
│   ├── SKILL.md
│   └── schemas/receipt.schema.json
│
├── reference/                         ClaudeLLM.md, CodexLLM.md
├── data/                              router.db lives here
├── scripts/                           setup, start-pi, install-skills
│                                      (.ps1 and .sh)
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## The philosophy, once more

> Not the cheapest AI. Not the strongest AI. The minimum sufficient
> configuration that maximises the chance of finishing the task correctly while
> minimising total effective quota burn.

Pi interprets. The backend decides. The backend learns.
