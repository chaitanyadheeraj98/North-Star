I checked the current OpenAI/Codex documentation for **September 25, 2026**. For Codex, there’s a very important distinction between **token count** and **how expensive those tokens are against your usage/credits**.

### Codex model burn

For the main models currently available in Codex, the currently documented model catalog includes GPT-6 Astra, GPT-6 Sol, GPT-6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-5.6 Luna, and GPT-5.5. Pricing is documented for the earlier GPT-5.6/Astra set in the existing notes; the supplied catalog lists the newer candidate models and supported effort levels but does not provide credit pricing for GPT-6 Sol, GPT-6 Luna, or GPT-5.5.

| Codex model       | Input / 1M | Cached input / 1M | Output + reasoning / 1M | Relative output burn |
| ----------------- | ---------: | ----------------: | ----------------------: | -------------------: |
| **GPT-5.5**       |    unknown |           unknown |             **unknown** |              unknown |
| **GPT-5.6 Luna**  |  5 credits |               0.5 |          **30 credits** |               **1×** |
| **GPT-5.6 Terra** |         50 |                 5 |                 **300** |         **10× Luna** |
| **GPT-5.6 Sol**   |        100 |                10 |                 **500** |       **16.7× Luna** |
| **GPT-6 Luna**    |    unknown |           unknown |             **unknown** |              unknown |
| **GPT-6 Sol**     |    unknown |           unknown |             **unknown** |              unknown |
| **GPT-6 Astra**   |        250 |                25 |               **1,250** |       **41.7× Luna** |

So in pure token-price terms for the models with supplied pricing:

**Luna << Terra < Sol << Astra**

Astra output/reasoning tokens cost about **41.7× as many credits as Luna**, **4.17× Terra**, and **2.5× Sol**. ([OpenAI Help Center][1])

---

## But effort matters too

This is the important part.

OpenAI explicitly says **reasoning tokens are hidden from the visible response but count as output usage**. So if a model spends 20,000 tokens internally thinking and only gives you 2,000 visible tokens, you're not merely consuming those 2,000 visible tokens—the reasoning tokens matter too. ([OpenAI Help Center][2])

For the currently supplied Codex catalog, the reasoning controls support:

| Effort     | Reasoning usage                                   | Practical token burn        |
| ---------- | ------------------------------------------------- | --------------------------- |
| **Low**    | Light reasoning                                   | 🟢 Low                      |
| **Medium** | Normal reasoning                                  | 🟡 Moderate                 |
| **High**   | More extensive reasoning                          | 🟠 High                     |
| **xHigh**  | Very extensive reasoning                          | 🔴 Very high                |
| **Max**    | Maximum reasoning budget                          | 🔴🔴 Highest                |
| **Ultra**  | Maximum reasoning + potentially additional agents | 🔴🔴🔴 Potentially enormous |

The current catalog says GPT-6 Astra and GPT-6 Sol support `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`; GPT-6 Luna supports `low`, `medium`, `high`, `xhigh`, and `max`; GPT-5.5 supports `low`, `medium`, `high`, and `xhigh`.

Codex can additionally expose **Ultra** for eligible models/users. OpenAI says Ultra can use maximum reasoning and may invoke additional agents, so its usage can be substantially higher depending on what those agents do. ([OpenAI Help Center][4])

### There is NO official multiplier for effort

This is critical. OpenAI does **not** say:

* Medium = 2× tokens
* High = 3× tokens
* xHigh = 5× tokens
* Max = 10× tokens

Those numbers would be made up.

The same High-effort request might require only a little reasoning on an easy task and tens of thousands of reasoning tokens on a nasty repository bug.

The **price per token remains the same** for the selected model regardless of effort. Effort changes **how many reasoning tokens the model may actually generate**. ([OpenAI Help Center][1])

---

# Model × effort burn chart

So this is the useful practical matrix. The colors represent relative expected consumption, **not an OpenAI-published numerical multiplier**:

| Model ↓ / Effort → | Low  | Medium | High | xHigh | Max    | Ultra  |
| ------------------ | ---- | ------ | ---- | ----- | ------ | ------ |
| **GPT-5.5**        | 🟢   | 🟢     | 🟢🟡 | 🟡    | —      | —      |
| **Luna**           | 🟢   | 🟢     | 🟢🟡 | 🟡    | 🟡🟠   | —      |
| **Terra**          | 🟢   | 🟢🟡   | 🟡   | 🟠    | 🔴     | 🔴🔴   |
| **Sol**            | 🟢🟡 | 🟡     | 🟠   | 🔴    | 🔴🔴   | 🔴🔴🔴 |
| **Astra**          | 🟡   | 🟠     | 🔴   | 🔴🔴  | 🔴🔴🔴 | 🔥🔥🔥 |

---

## Here's a concrete example

Suppose an identical coding task somehow consumed:

**100,000 uncached input tokens + 20,000 output/reasoning tokens**

and we ignore cached tokens for simplicity.

The cost would be:

| Model     |  Input cost | Output/reasoning cost |           Total |
| --------- | ----------: | --------------------: | --------------: |
| **Luna**  | 0.5 credits |                   0.6 | **1.1 credits** |
| **Terra** |           5 |                     6 |  **11 credits** |
| **Sol**   |          10 |                    10 |  **20 credits** |
| **Astra** |          25 |                    25 |  **50 credits** |

The catch is that **they won't necessarily consume the same number of tokens**. Astra can sometimes solve something in fewer steps, while Max/Ultra reasoning or extra agents can push consumption much higher.

OpenAI says a typical Codex task using **GPT-5.6 Sol may consume roughly 5–30 credits**, which gives you a useful real-world reference point. ([OpenAI Help Center][1])

One especially important optimization: **cached input is 10× cheaper than normal input for the priced GPT-5.6/Astra models in these notes**. For example, Sol is 100 credits/M normal input but only **10 credits/M cached input**. So preserving context/cache efficiently can matter almost as much as model selection. ([OpenAI Help Center][1])

And compared with the Claude research from your previous question, **Codex gives us more concrete consumption data for the priced models** because OpenAI publishes the per-million-token credit rates and confirms that hidden reasoning counts toward output usage.

[1]: https://help.openai.com/en/articles/11481834?utm_source=chatgpt.com "ChatGPT Rate Card (Business, Enterprise/Edu credit-based pricing) | OpenAI Help Center"
[2]: https://help.openai.com/en/articles/4936856-wha?utm_source=chatgpt.com "Understanding and counting tokens | OpenAI Help Center"
[3]: https://developers.openai.com/api/docs/models?utm_source=chatgpt.com "Models | OpenAI API"
[4]: https://help.openai.com/en/articles/11481834-chatgpt-rate-card-business-enterpriseedu%20...%20OpenAI%27s%20website%20directly.%20External%20Link%20Content%20...?utm_source=chatgpt.com "ChatGPT Rate Card (Business, Enterprise/Edu credit-based pricing) | OpenAI Help Center"

Codex model token burn

Official Codex credit rate per 1 million output tokens, including reasoning-token usage where supplied. Lower is more economical.

model credits
GPT-5.5 unknown
GPT-5.6 Luna 30
GPT-5.6 Terra 300
GPT-5.6 Sol 500
GPT-6 Luna unknown
GPT-6 Sol unknown
GPT-6 Astra 1,250
