Yes. I researched the **current Claude lineup as of September 17, 2026**, including Claude Code. The most important thing is that there are **two separate kinds of “burn”**:

1. **Model burn** — Fable/Opus/Sonnet/Haiku have different per-token costs and consume subscription quota differently.
2. **Effort burn** — Low → Medium → High → xHigh → Max controls how much reasoning Claude is allowed/encouraged to do.

Anthropic **does not publish fixed multipliers** such as “Max always consumes 4× High.” Adaptive reasoning decides how many thinking tokens to spend based on the actual task. ([Claude Help Center][1])

### Current Claude model cost/burn

For a clean comparison, here is the official API price for the major current models:

| Model         | Input / 1M tokens | Output / 1M tokens | Cost vs Sonnet 5 | General quota/token intensity |
| ------------- | ----------------: | -----------------: | ---------------: | ----------------------------- |
| **Haiku 4.5** |                $1 |                 $5 |         **0.5×** | 🟢 Very low                   |
| **Sonnet 5**  |                $2 |                $10 |  **1× baseline** | 🟢/🟡 Moderate                |
| **Opus 5**    |                $5 |                $25 |         **2.5×** | 🟠 High                       |
| **Fable 5.1** |               $10 |                $50 |           **5×** | 🔴 Very high                  |

Sonnet 5's $2/$10 pricing was made permanent in August 2026. Opus 5 is $5/$25, while Fable 5.1 is $10/$50. ([Anthropic][2])

So for **the exact same number of raw output tokens**, the simple relationship is:

**Haiku 4.5 → 0.5× Sonnet → Sonnet 5 → 1× → Opus 5 → 2.5× → Fable 5.1 → 5×**

But don't confuse that with “Fable always uses 5× as many tokens.” It costs 5× as much **per output token** as Sonnet. On some difficult tasks, a stronger model can actually finish in fewer steps/tokens.

### Effort level token burn

Here is the useful chart for Claude/Claude Code:

| Effort     | Reasoning/token burn | Good for                                    | My practical interpretation |
| ---------- | -------------------- | ------------------------------------------- | --------------------------- |
| **Low**    | 🟩 Lowest            | Renames, tiny edits, simple questions       | Token saver                 |
| **Medium** | 🟩🟩 Low–moderate    | Normal implementation, straightforward bugs | **Best efficiency**         |
| **High**   | 🟨🟨🟨 Moderate–high | Hard coding, debugging, planning            | **Best general balance**    |
| **xHigh**  | 🟧🟧🟧🟧 High        | Long agentic coding, complex refactors      | Use selectively             |
| **Max**    | 🟥🟥🟥🟥🟥 Highest   | Extremely difficult bugs/architecture       | Can burn quota very quickly |

Anthropic explicitly describes **Low/Medium as ways to stretch usage**, High as the quality/speed balance, xHigh as deeper reasoning without Max's full token cost, and Max as the most thorough setting. ([Claude Help Center][1])

There is **no official conversion like this**:

> Low = 1×
> Medium = 1.5×
> High = 2×
> xHigh = 4×
> Max = 8×

Those would be invented numbers. Claude uses **adaptive thinking**, so a trivial task at High might barely think, while a difficult repository bug at High could consume many thousands of thinking tokens. Max can also overthink simple tasks. ([Claude Platform Docs][3])

### Model × effort: what will drain your Claude limit fastest?

For **Claude Code**, this is the more useful practical matrix:

| Model ↓ / Effort → |  Low | Medium | High | xHigh |    Max |
| ------------------ | ---: | -----: | ---: | ----: | -----: |
| **Haiku 4.5**      |   🟢 |     🟢 |    — |     — |      — |
| **Sonnet 5**       |   🟢 |   🟢🟢 |   🟡 |    🟠 |     🔴 |
| **Opus 5**         | 🟢🟡 |     🟡 |   🟠 |    🔴 |   🔴🔴 |
| **Fable 5.1**      |   🟡 |     🟠 |   🔴 |  🔴🔴 | 🔴🔴🔴 |

The symbols above are a **practical relative scale, not Anthropic-published multipliers**. Anthropic itself classifies Sonnet as roughly moderate token intensity, Opus as high, and Fable as very high. ([Claude Help Center][4])

### For the kind of Claude Code work you're doing

Since you're using Claude on a **large codebase, debugging workflows, architecture changes, and multi-file implementations**, I would structure usage like this:

**Sonnet 5 Medium**
→ routine implementation, UI changes, known fixes, tests, documentation.

**Sonnet 5 High**
→ your normal development default when Claude needs to inspect several files and reason about workflow behavior.

**Opus 5 High**
→ architecture/design decisions, difficult bugs, database/data-flow problems, or when Sonnet starts going in circles.

**Opus 5 xHigh**
→ complicated cross-cutting changes where Claude must understand multiple services and preserve invariants.

**Opus 5 Max**
→ only for a genuinely nasty root-cause investigation. Don't leave Max on permanently.

**Fable 5.1**
→ reserve for extremely difficult work where Opus isn't enough. On Pro, importantly, **Fable uses pay-as-you-go usage credits rather than your included normal usage**; on Max it can use up to 50% of the weekly usage allocation and burns that allocation faster. ([Claude Help Center][5])

And there's a particularly useful built-in approach: Anthropic recommends the pattern of **Opus for planning and Sonnet for execution**. Claude Code even has `/model opusplan` for essentially that workflow. ([Claude Help Center][6])

### One thing that burns more tokens than people realize

The **model/effort setting isn't always the biggest culprit**.

Claude Code resends the conversation history, project context, and relevant files on subsequent turns. A very long session where Claude has already inspected 20–30 files can therefore burn much more quota than a clean session. Anthropic specifically recommends `/clear` between unrelated tasks and `/compact` during long tasks. ([Claude Help Center][6])

For example, **Sonnet 5 High in a clean session can easily be more economical than Sonnet 5 Medium inside a huge bloated context**.

If your goal is specifically to maximize your Claude Code subscription, a strong default for your development workflow would be:

**Sonnet 5 Medium/High → ~80% of work**
**Opus 5 High → ~15%**
**Opus 5 xHigh/Max → ~5% hardest problems**
**Fable 5.1 → exceptional cases only**

That will stretch your usage dramatically better than leaving **Opus/Fable + Max** running for every task.

[1]: https://support.claude.com/en/articles/8664678-change-the-model-effort-and-thinking-settings?utm_source=chatgpt.com "Change the model, effort, and thinking settings | Claude Help Center"
[2]: https://www.anthropic.com/news/claude-sonnet-5?utm_source=chatgpt.com "Introducing Claude Sonnet 5 \ Anthropic"
[3]: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/prompt-templates-and-variables?utm_source=chatgpt.com "Prompting best practices - Claude Platform Docs"
[4]: https://support.claude.com/en/articles/14782391-claude-enterprise-consumption-guide?utm_source=chatgpt.com "Claude Enterprise consumption guide | Claude Help Center"
[5]: https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan?utm_source=chatgpt.com "Claude Fable models on your plan | Claude Help Center"
[6]: https://support.claude.com/en/articles/14552983-models-usage-and-limits-in-claude-code?utm_source=chatgpt.com "Models, usage, and limits in Claude Code | Claude Help Center"

Claude model token cost

Official API output-token price per 1 million tokens. This shows monetary/quota intensity, not a guarantee of how many tokens a task will generate.

model cost
Haiku 4.5 5
Sonnet 5 10
Opus 5 25
Fable 5.1 50
