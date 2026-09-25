Yes. Based on everything we decided, I would now lock the architecture and build against this specification.

# Adaptive LLM Router — Final Personal-Use Product Specification

## 1. What we are building

A **local web application** that answers one question:

> Given this coding task, should I use Claude or Codex, which model should I choose, and how much reasoning effort should I give it?

It then learns from what actually happened after the task was executed.

The complete loop is:

```text
TASK
 ↓
Pi Agent understands the task
 ↓
Our backend calculates the best route
 ↓
Codex / Claude → Model → Effort
 ↓
User executes the task
 ↓
Claude/Codex Outcome Skill generates receipt
 ↓
User pastes receipt back
 ↓
Backend learns from outcome
 ↓
Future routing improves
```

The application is **personal-use, local-first, Dockerized, and does not require us to buy OpenAI/Anthropic API credits**.

Pi can authenticate to supported subscription providers through `/login`, including ChatGPT Plus/Pro (Codex) and Claude Pro/Max. ([Pi.dev][1])

---

# 2. Final architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     USER'S COMPUTER                     │
│                                                         │
│   Browser                                               │
│   http://localhost:3000                                 │
│        │                                                │
│        ▼                                                │
│  ┌────────────────┐                                     │
│  │ React Web App  │  Docker                             │
│  └───────┬────────┘                                     │
│          │                                              │
│          ▼                                              │
│  ┌────────────────────────┐                             │
│  │ FastAPI Backend        │  Docker                     │
│  │                        │                             │
│  │ Task management        │                             │
│  │ Routing engine         │                             │
│  │ Learning engine        │                             │
│  │ Receipt parser         │                             │
│  │ Model registry         │                             │
│  └──────────┬─────────────┘                             │
│             │                                           │
│       ┌─────┴─────┐                                     │
│       ▼           ▼                                     │
│   SQLite       Pi Bridge                                │
│   Docker       Host process                             │
│   volume           │                                    │
│                    ▼                                    │
│                Pi Agent                                 │
│                    │                                    │
│           user's subscription                           │
│             ┌──────┴──────┐                             │
│             ▼             ▼                             │
│        ChatGPT/Codex    Claude                          │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

The critical separation is:

**Pi understands.**

**Our backend decides.**

**Our backend learns.**

Pi should **not directly modify routing weights or decide final routing policy**.

That protects the system from an LLM arbitrarily changing its own decision algorithm.

---

# 3. Why Pi exists

Pi has only one essential V1 responsibility:

### Convert an unstructured task into a structured Task Fingerprint

User gives:

```text
Re-key premium lead versions based only on recruiter
identity and preserve source-email provenance...
```

Pi returns something like:

```json
{
  "task_family": "backend_data_integrity",
  "task_type": "bug_fix",
  "complexity": 0.84,
  "regression_risk": 0.88,
  "ambiguity": 0.16,
  "requirements_clarity": 0.93,
  "architecture_reasoning": 0.78,
  "database_reasoning": 0.91,
  "concurrency_risk": 0.10,
  "security_risk": 0.05,
  "repository_understanding": 0.72,
  "scope": "multi_file",
  "estimated_files": 5,
  "confidence": 0.91
}
```

Pi does **not** return:

```text
Use Codex Sol High
```

Our backend determines that.

---

# 4. Pi integration

I recommend a small **Pi Bridge** written in TypeScript/Node.

Use Pi's SDK rather than trying to control its terminal UI.

Pi officially exposes an SDK with `createAgentSession`, model/auth facilities, resource loading and coding/read-only tools. It also offers RPC mode if we later prefer process isolation or integration from another language. ([Pi.dev][2])

The bridge runs directly on your host PC:

```text
127.0.0.1:31415
```

and exposes only a few endpoints.

```text
GET  /health
POST /analyze
GET  /providers
GET  /models
```

Example:

```json
POST /analyze

{
  "task": "...",
  "analysis_profile": "router-v1"
}
```

Response:

```json
{
  "fingerprint": {
    "task_family": "backend_data_integrity",
    "complexity": 0.84,
    "regression_risk": 0.88
  }
}
```

---

# 5. Why Pi stays outside Docker

Initially:

```text
Docker
├── frontend
└── backend

Host Windows
└── Pi Bridge
```

This makes authentication much easier.

Pi stores/uses the user's local provider credentials and can authenticate subscription providers using `/login`. ([Pi.dev][3])

The Docker backend contacts:

```text
http://host.docker.internal:31415
```

Later we can containerize Pi if there is a good reason.

There isn't one for V1.

---

# 6. The backend is the real product

Recommended stack:

```text
Python
FastAPI
Pydantic
SQLAlchemy
SQLite
```

The backend contains five modules:

```text
Task Analyzer Adapter
Routing Engine
Model Registry
Learning Engine
Execution Receipt Processor
```

The Pi Bridge is simply another input adapter.

---

# 7. Task Fingerprint

Define this schema before writing routing logic.

```json
{
  "schema_version": "1.0",

  "task_family": "",
  "task_type": "",

  "complexity": 0.0,
  "ambiguity": 0.0,
  "requirements_clarity": 0.0,

  "regression_risk": 0.0,
  "architecture_reasoning": 0.0,
  "database_reasoning": 0.0,
  "concurrency_risk": 0.0,
  "security_risk": 0.0,
  "repository_understanding": 0.0,

  "scope": "",
  "estimated_files": 0,

  "frontend": false,
  "backend": false,
  "database": false,
  "infrastructure": false,

  "tests_required": "",
  "confidence": 0.0
}
```

All scores:

```text
0.00 → 1.00
```

This prevents arbitrary 1–10 vs 1–100 scaling across components.

---

# 8. Initial task families

Start with approximately 20 families:

```text
ui_cosmetic
ui_behavior
frontend_state
api_implementation
backend_business_logic
backend_data_integrity
database_schema
database_migration
bug_fix_local
bug_fix_cross_system
debugging
root_cause_analysis
performance
concurrency
security
testing
refactor_local
refactor_architectural
architecture_design
devops
documentation
repository_analysis
```

Don't create hundreds of categories initially.

The historical data would become too fragmented.

---

# 9. Model Registry

Create:

```text
config/models.yaml
```

Example conceptual structure:

```yaml
providers:

  codex:
    enabled: true

    models:
      sol:
        enabled: true

        efforts:
          - low
          - medium
          - high
          - xhigh
          - max

        capability:
          coding: 0.95
          debugging: 0.94
          architecture: 0.90
          database: 0.91

        burn:
          low: 1.0
          medium: 1.3
          high: 1.8
          xhigh: 2.5
          max: 3.5

  claude:
    enabled: true
```

The numbers are **routing priors**, not claims about official token multipliers.

They can be adjusted as our own usage data accumulates.

---

# 10. Don't optimize dollars

Since this is personal use and you're using subscriptions, the primary metric should be:

# Effective Quota Burn

rather than:

```text
$0.034
```

Conceptually:

```text
Effective Burn =
Base model burn
× reasoning effort
+ retries
+ debugging
+ escalation
+ failed attempts
```

Example:

```text
Cheap configuration

initial burn       3
retry              3
debugging          2
escalation         5

Effective burn = 13
```

versus:

```text
Stronger configuration

initial burn       6
no retries

Effective burn = 6
```

The second configuration was actually cheaper.

---

# 11. Routing Engine

Input:

```text
Task Fingerprint
```

plus:

```text
Model Registry
Historical Results
Learning Statistics
```

Output:

```json
{
  "provider": "codex",
  "model": "sol",
  "effort": "high",
  "confidence": 0.89
}
```

---

# 12. Routing algorithm

First determine required reliability.

For example:

```text
simple cosmetic task
        ↓
~low reliability requirement

normal implementation
        ↓
medium-high

database integrity
        ↓
very high

security/concurrency
        ↓
highest
```

Then calculate predicted reliability for every configuration:

```text
Predicted Reliability =
Base Capability Match
+
Task-Family Match
+
Effort Adjustment
+
Historical Performance Adjustment
+
Similar-Task Adjustment
```

Then eliminate configurations that don't meet the required reliability.

Among those remaining:

```text
choose the lowest expected effective burn
```

This is the fundamental algorithm.

---

# 13. Minimum sufficient intelligence

Suppose backend predicts:

```text
                     Reliability   Burn

Terra Medium             82%        2.0
Terra High               89%        2.8
Sol Medium               94%        4.0
Sol High                 97%        5.5
Sol xHigh                98%        8.5
```

Task requires:

```text
95%
```

Eligible:

```text
Sol High
Sol xHigh
```

Choose:

```text
Sol High
```

That's the philosophy of the entire application.

---

# 14. Recommendation UI

User shouldn't see the mathematics.

They see:

```text
Recommended

CODEX

Sol
High Reasoning

Confidence: 89%

Task
Backend Data Integrity

Complexity
High

Regression Risk
High
```

Then:

```text
WHY

This change modifies persistent identity semantics and
multiple downstream readers.

WHY NOT LIGHTER?

Historical/base routing estimates indicate increased
retry or regression risk.

WHY NOT STRONGER?

Requirements are already explicit. Additional reasoning
is unlikely to justify the extra quota consumption.
```

And:

```text
COPY TASK
```

---

# 15. Task handoff

Every task receives:

```text
RT-000001
```

Copying the task produces:

```text
[LLM-ROUTER]
task_id=RT-000001
recommended_provider=codex
recommended_model=sol
recommended_effort=high
router_version=1.0
[/LLM-ROUTER]

TASK

<original task>
```

That metadata is essential for later learning.

---

# 16. Outcome Skill

Create one canonical portable Agent Skill:

```text
skills/router-outcome/
    SKILL.md
```

Pi implements the Agent Skills standard and can load compatible skill folders, including skills from Claude/Codex locations. ([Pi.dev][4])

For our purposes, maintain **one source skill** and install/copy it into whichever agent directories are required.

The skill should NOT rate itself with arbitrary scores.

Its role is collecting evidence.

---

# 17. Outcome Skill behavior

After a task is finished, user runs something like:

```text
/router-result
```

or tells the agent:

```text
Generate the router result receipt.
```

The Skill inspects what occurred during the task and returns:

```text
[LLM-ROUTER-RESULT]

{
  "receipt_version": "1.0",

  "task_id": "RT-000001",

  "execution": {
    "provider": "codex",
    "model": "sol",
    "effort": "high"
  },

  "outcome": {
    "status": "success",
    "implementation_complete": true,
    "first_pass_success": true,
    "tests_passed": true,
    "build_passed": true,
    "known_regression": false
  },

  "work": {
    "files_read": 9,
    "files_modified": 4,
    "debug_cycles": 1,
    "major_replans": 0,
    "user_corrections": 0
  },

  "escalation": {
    "occurred": false
  },

  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "reasoning_tokens": null,
    "source": "unavailable"
  }
}

[/LLM-ROUTER-RESULT]
```

The user copies this.

---

# 18. Never allow the Skill to invent usage

If the provider exposes token data:

```text
source = provider_reported
```

Store it.

If not:

```text
tokens = null
source = unavailable
```

Never allow:

```text
"I estimate I probably used 35,000 tokens."
```

That would poison the learning data.

---

# 19. Import Result

Router has a second major action:

```text
PASTE RESULT RECEIPT

[                                     ]
[                                     ]

IMPORT
```

Backend:

```text
Parse markers
↓
Validate JSON
↓
Find Task ID
↓
Compare recommendation vs actual execution
↓
Create Execution record
↓
Update learning statistics
```

Then:

```text
Result learned ✓
```

---

# 20. What actually learns

This is important.

Don't make Pi edit:

```text
weights.json
```

after every task.

Instead the database accumulates observations.

Example:

```text
backend_data_integrity
+
Codex
+
Sol
+
High

Attempts               63
Success                 60
First-pass success      57
Escalations              1
Avg debugging cycles    0.4
Avg effective burn      5.8
```

The router queries those statistics next time.

That's your learning system.

---

# 21. Learning algorithm V1

Use **smoothed statistical learning**, not machine-learning training.

For each:

```text
Task Family
× Provider
× Model
× Effort
```

track:

```text
attempts
successes
first_pass_success
failures
debug_cycles
corrections
escalations
effective_burn
```

Do not allow one success/failure to drastically change behavior.

Use minimum evidence levels:

```text
0–4 results
base routing policy dominates

5–19
history has weak influence

20–49
history has moderate influence

50+
history has strong influence
```

---

# 22. Better reliability calculation

Rather than simply:

```text
successes / attempts
```

use Bayesian smoothing.

Conceptually:

```text
Prior reliability
        +
Observed outcomes
        ↓
Posterior reliability
```

This avoids:

```text
1 successful task
=
100% reliable
```

which would obviously be wrong.

---

# 23. Recency

Old data should gradually matter less.

Because models change.

Store:

```text
model_version
date
router_version
```

and weight newer executions more strongly than older ones.

---

# 24. Learning upgrade AND downgrade

The system must discover both situations.

### Underpowered

```text
Terra Medium
↓
failure
↓
retry
↓
escalation
↓
Sol High succeeds
```

Future similar tasks may start higher.

### Overpowered

```text
Sol High
98% success

Terra Medium
97% success
¼ effective burn
```

Future similar tasks should move downward.

That's what eventually makes this router useful.

---

# 25. Similar-task learning

Not V1.

Add in V1.5.

Use:

```text
pgvector
or local vector storage
```

Then:

```text
New Task
 ↓
Find 30–50 most similar historical tasks
 ↓
Compare which configurations worked
 ↓
Blend with general family statistics
```

This produces much more precise routing.

---

# 26. SQLite schema

For personal use, SQLite is enough.

Core tables:

```text
tasks
task_fingerprints
routing_decisions
executions
execution_metrics
escalations
model_registry
routing_statistics
app_settings
```

---

# 27. `tasks`

Important fields:

```text
id
original_task
created_at
status
```

---

# 28. `task_fingerprints`

```text
task_id
schema_version
task_family
task_type
fingerprint_json
analyzer_provider
analyzer_model
analyzer_confidence
created_at
```

This is useful because even the Task Analyzer itself can eventually be evaluated.

---

# 29. `routing_decisions`

```text
id
task_id

provider
model
effort

confidence
required_reliability
predicted_reliability
predicted_burn

reasoning_json

router_version
created_at
```

---

# 30. `executions`

```text
id
task_id
routing_decision_id

actual_provider
actual_model
actual_effort

recommendation_followed

status

receipt_json
created_at
```

---

# 31. `execution_metrics`

```text
execution_id

implementation_complete
first_pass_success

tests_passed
build_passed
regression_found

files_read
files_modified

debug_cycles
major_replans
user_corrections

measured_tokens
estimated_burn
```

---

# 32. Backend endpoints

V1 requires approximately:

```text
POST /api/tasks
POST /api/tasks/{id}/analyze
GET  /api/tasks/{id}
GET  /api/tasks/{id}/recommendation

POST /api/results/import

GET  /api/history
GET  /api/analytics

GET  /api/models
PUT  /api/models/{id}

GET  /api/pi/status
```

---

# 33. Pi prompt

Pi needs a very strict analyzer system instruction.

Something conceptually like:

```text
You are the Task Intelligence Engine.

Your only responsibility is to classify the supplied
software-development task.

Do not solve the task.
Do not recommend an LLM.
Do not recommend a model.
Do not recommend reasoning effort.

Return only the TaskFingerprint schema.

Judge difficulty based on reasoning requirements,
not prompt length.

A long mechanical task can be easy.
A short race-condition request can be difficult.

Do not infer repository facts not contained in the task.

Return uncertainty through confidence and ambiguity.
```

This will be one of the most important prompts in the application.

---

# 34. Which Pi model do we use?

Make this configurable.

Settings:

```text
Task Analyzer Provider

○ ChatGPT/Codex
○ Claude
```

and:

```text
Analyzer Model
[ dropdown ]
```

Your default can use a relatively inexpensive/fast model available through your subscription.

We do **not need the strongest model** for task classification.

If confidence comes back below something like:

```text
0.75
```

the system can optionally retry using a stronger analyzer model.

So:

```text
Fast model
 ↓
confidence good?
 ├─ yes → continue
 └─ no  → stronger analyzer
```

This should be optional in V1.1 rather than required in the first build.

---

# 35. Frontend screens

Keep V1 very small.

### Router

```text
Paste Task
Analyze
Recommendation
Copy Task
```

### Result Import

```text
Paste Receipt
Import
```

### History

```text
Task
Recommendation
Actual configuration
Outcome
Date
```

### Analytics

```text
Success by configuration
Average debugging cycles
Escalations
Underpowered routes
Overpowered routes
```

### Settings

```text
Pi status
Analyzer provider/model
Claude/Codex model registry
Reasoning levels
Routing thresholds
```

That's enough.

---

# 36. Project structure

I would organize the repository as:

```text
adaptive-llm-router/

├── frontend/
│   ├── src/
│   ├── package.json
│   └── Dockerfile
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── task_service.py
│   │   │   ├── routing_service.py
│   │   │   ├── learning_service.py
│   │   │   ├── receipt_service.py
│   │   │   └── pi_service.py
│   │   │
│   │   └── core/
│   │       ├── router/
│   │       │   ├── engine.py
│   │       │   ├── scoring.py
│   │       │   ├── reliability.py
│   │       │   └── burn.py
│   │       │
│   │       └── learning/
│   │           ├── updater.py
│   │           ├── statistics.py
│   │           └── priors.py
│   │
│   ├── config/
│   │   ├── models.yaml
│   │   ├── task_families.yaml
│   │   └── routing.yaml
│   │
│   └── Dockerfile
│
├── pi-bridge/
│   ├── src/
│   ├── prompts/
│   │   └── task-analyzer.md
│   └── package.json
│
├── skills/
│   └── router-outcome/
│       ├── SKILL.md
│       └── schemas/
│           └── receipt.schema.json
│
├── data/
│   └── router.db
│
├── scripts/
│   ├── start-pi.ps1
│   ├── install-skills.ps1
│   └── setup.ps1
│
├── tests/
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

# 37. Docker Compose

V1 Compose only needs two containers:

```text
frontend
backend
```

SQLite remains in:

```text
./data/
```

mounted into the backend.

Conceptually:

```yaml
services:

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "8000:8000"

    volumes:
      - ./data:/app/data
      - ./backend/config:/app/config

    environment:
      PI_BRIDGE_URL: http://host.docker.internal:31415
      DATABASE_URL: sqlite:////app/data/router.db
```

Then:

```bash
docker compose up -d
```

Open:

```text
http://localhost:3000
```

---

# 38. Startup workflow

Eventually setup should be almost one-click.

First time:

```text
git clone ...
cd adaptive-llm-router

docker compose up -d

cd pi-bridge
npm install
npm start
```

Then Pi authentication once:

```text
/login
```

Select your subscription provider.

Pi documents subscription authentication through `/login`. ([Pi.dev][3])

After that:

```text
localhost:3000
```

---

# 39. Security

Even though this is personal use:

Pi should only bind to:

```text
127.0.0.1
```

not:

```text
0.0.0.0
```

Otherwise another machine on your network could potentially invoke the agent.

Also, Pi runs with the permissions of the user account that launches it, so project trust and tool permissions matter. Pi's own documentation makes clear that project trust is not a full sandbox. ([Pi.dev][5])

For our Task Analyzer, Pi does not need write/edit tools.

Ideally give it:

```text
read only
```

or even no filesystem access initially.

---

# 40. An important architecture decision

Pi should **not need to read the router source code on every task**.

Earlier we discussed:

```text
Pi reads backend code
→ executes learning calculations
→ updates weights
```

I would now explicitly avoid that.

Better:

```text
Pi
↓
produces fingerprint

Backend
↓
runs router

Backend
↓
updates learning
```

This is safer, faster and deterministic.

Pi remains an interpreter.

Our application remains the brain.

---

# 41. What Pi can eventually do

Later we can let Pi optionally inspect the repository.

For example:

```text
Task:
"Fix duplicate recruiter contacts."
```

Pi could inspect:

```text
models
services
tests
migration structure
```

and produce a more accurate fingerprint.

But that's V2.

For V1:

> Analyze only the pasted task.

This keeps the implementation very manageable.

---

# 42. Testing dataset

Before using this on real work, create approximately 50 artificial/reference tasks covering:

```text
simple UI
frontend logic
API work
database
migration
refactoring
debugging
concurrency
security
architecture
testing
DevOps
```

Have expected routing bands.

Example:

```text
Change button from blue to gray

Expected:
light model
low reasoning
```

versus:

```text
Fix cross-worker race condition causing duplicate payments
while preserving idempotency

Expected:
strong model
high/xHigh reasoning
```

We are testing routing sanity, not exact model names.

---

# 43. Unit tests

The routing engine must be deterministic.

Same:

```text
fingerprint
model registry
historical data
router version
```

must produce the same recommendation.

Test:

```text
risk thresholds
model filtering
effort escalation
historical adjustment
burn calculation
fallback behavior
receipt validation
learning update
```

---

# 44. Integration tests

Mock Pi.

Send:

```text
task
```

receive known:

```text
fingerprint
```

then assert recommendation.

Also test:

```text
Task
→ recommendation
→ receipt
→ learning update
→ same-family next task
→ recommendation changes appropriately
```

That's the most important end-to-end test.

---

# 45. V1 acceptance criteria

V1 is finished when you can:

```text
1. Start stack.

2. Confirm Pi connected.

3. Paste arbitrary coding task.

4. Receive structured fingerprint.

5. Backend chooses:
   Claude/Codex
   model
   effort.

6. Copy handoff.

7. Execute externally.

8. Generate Outcome Skill receipt.

9. Paste receipt.

10. Backend stores outcome.

11. Historical statistics update.

12. Future routing can use those statistics.
```

Nothing else is required for V1.

---

# 46. What NOT to build yet

Avoid:

```text
VS Code extension
desktop application
cloud hosting
multi-user accounts
billing
OpenAI API integration
Anthropic API integration
complex embeddings
automatic task execution
automatic Claude/Codex launching
repository scanning
multi-agent routing
browser extension
```

Every one of those can wait.

---

# 47. Phase plan

### Phase 1 — Contracts

Build:

```text
TaskFingerprint schema
ExecutionReceipt schema
Model Registry schema
RoutingDecision schema
```

No UI.

No Pi.

---

### Phase 2 — Routing Brain

Build:

```text
model registry
base capability matrix
risk calculation
reliability calculation
burn calculation
route selector
```

Feed it fake fingerprints.

It must already produce reasonable recommendations.

---

### Phase 3 — Learning Brain

Build:

```text
receipt importer
execution records
smoothed reliability stats
effective burn stats
historical adjustments
```

Test with synthetic execution histories.

---

### Phase 4 — Pi Bridge

Integrate Pi.

Pi:

```text
Task
↓
TaskFingerprint
```

Nothing more.

---

### Phase 5 — Web UI

Build the four screens:

```text
Router
Results
History
Settings
```

---

### Phase 6 — Outcome Skill

Build the canonical:

```text
router-outcome/SKILL.md
```

Install it in Claude and Codex.

Pi itself supports the Agent Skills format and can also discover compatible Claude/Codex skills, which makes keeping a canonical skill definition practical. ([Pi.dev][4])

---

### Phase 7 — Docker + Setup

Create:

```text
docker-compose.yml
setup.ps1
start-pi.ps1
install-skills.ps1
```

Goal:

```text
docker compose up -d
```

plus one Pi startup command.

---

### Phase 8 — Real Usage

Use it on your actual coding work.

Do not tune the router after every individual task.

Collect:

```text
50+
```

real executions first.

Then inspect patterns.

---

# 48. The first thing we should actually implement

Do **not** start creating React screens.

Our first commit should establish:

```text
schemas/
    task_fingerprint.py
    execution_receipt.py
    routing_decision.py

config/
    models.yaml
    routing.yaml

core/router/
    engine.py
```

Then create about ten test fingerprints and verify:

```text
simple task
→ cheap/light route

medium task
→ medium route

high-risk task
→ stronger route

high complexity + clear requirements
→ strong/high rather than blindly Max

low complexity + long prompt
→ still lightweight
```

Once that foundation works, Pi becomes easy to plug in.

---

# Final architecture

The finished V1 is therefore:

```text
               LOCAL WEB APP
                     │
              user pastes task
                     │
                     ▼
                Pi Agent
        user's existing subscription
                     │
                     ▼
              Task Fingerprint
                     │
                     ▼
         ┌───────────────────────┐
         │ OUR ROUTING ENGINE    │
         │                       │
         │ capabilities          │
         │ task risk             │
         │ quota burn            │
         │ execution history     │
         │ learning statistics   │
         └───────────┬───────────┘
                     │
                     ▼

              CODEx / CLAUDE
               MODEL + EFFORT

                     │
                 execution
                     │
                     ▼
              Outcome Skill
                     │
                  receipt
                     │
                     ▼
              Learning Engine
                     │
                     ▼
                  SQLite
                     │
                     ▼

        BETTER NEXT RECOMMENDATION
```

That is the version I would build. It keeps **LLM judgment where semantic understanding is required**, normal code where consistency matters, and uses the Claude/Codex subscriptions you already have rather than introducing another per-task API bill.

[1]: https://pi.dev/docs/latest?utm_source=chatgpt.com "Pi Documentation · Documentation · Pi"
[2]: https://pi.dev/docs/latest/sdk?utm_source=chatgpt.com "SDK · Documentation · Pi"
[3]: https://pi.dev/docs/latest/quickstart?utm_source=chatgpt.com "Quickstart · Documentation · Pi"
[4]: https://pi.dev/docs/latest/skills?utm_source=chatgpt.com "Skills · Documentation · Pi"
[5]: https://pi.dev/docs/latest/security?utm_source=chatgpt.com "Security · Documentation · Pi"
