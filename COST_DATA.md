# GrabOn Merchant Deal Audit Agent — Submission Cost Data

## One Full Live Agent Run (`python main.py`, 20 merchants)

### Groq (from `outputs/provider_calls.json`)
| Model | Purpose | Calls | Tokens |
|---|---|---|---|
| llama-3.3-70b-versatile | loop_plan | 80 | ~40,000 |
| llama-3.3-70b-versatile | loop_act | 80 | ~32,000 |
| llama-3.3-70b-versatile | loop_observe | 80 | ~13,700 |
| llama-3.1-8b-instant | loop_decide | 68 | ~52,000 |
| llama-3.1-8b-instant | extract | 20 | ~4,500 |
| **Total Groq** | | **328 calls** | **~141,966 tokens** |

### Groq Pricing (public, as of May 2026)
- llama-3.3-70b-versatile: $0.59 / 1M input tokens, $0.79 / 1M output tokens
- llama-3.1-8b-instant: $0.05 / 1M input tokens, $0.08 / 1M output tokens

### Estimated Cost Per Full Run
| Model | Tokens | Estimated Cost |
|---|---|---|
| llama-3.3-70b-versatile (~85,700 tokens) | 85,700 | ~$0.055 |
| llama-3.1-8b-instant (~56,500 tokens) | 56,500 | ~$0.005 |
| **Groq subtotal** | **142,200** | **~$0.06** |

### Gemini (gemini-2.0-flash for report generation)
- 20 report calls, each ~500 tokens
- gemini-2.0-flash: free tier / $0.075 per 1M tokens
- Estimated: ~10,000 tokens → **~$0.001**

### Total Per Full Run: **~$0.06** (effectively free on both free tiers)

---

## One Eval Run (13 scenarios, fully deterministic)

| Metric | Value |
|---|---|
| Provider calls | 0 (deterministic only) |
| Tokens used | 0 |
| Cost | **$0.00** |
| Latency | ~1.23 seconds total |

---

## Entire Development Process Cost Estimate

Development involved approximately 15–20 full agent runs and ~50 partial runs for debugging.

| Phase | Runs | Estimated Cost |
|---|---|---|
| Initial loop development | ~10 runs | ~$0.60 |
| Recovery/budget testing | ~8 runs | ~$0.48 |
| Eval harness development | 0 (deterministic) | $0.00 |
| Final demo runs | ~5 runs | ~$0.30 |
| **Total Development** | | **~$1.40** |

All Groq usage was within free-tier rate limits. Gemini usage was within free-tier quota.
Eval harness costs zero — all 13 scenarios are deterministic and require no API calls.

---

## Source
- Groq token counts: `outputs/provider_calls.json`
- Agent token counter: `outputs/token_curve.jsonl` (final step: 4,790 internal tokens)
- Groq pricing: https://groq.com/pricing
- Gemini pricing: https://ai.google.dev/pricing
