# ai-visibility-scorer

**Measure whether ChatGPT, Perplexity, and other AI engines actually recommend your product — the honest way.**

Half of B2B software buyers now start vendor research in an AI assistant instead of Google. So "does the AI name us?" is a real metric. The problem is that most tools measuring it are measuring the wrong thing, and reporting one number that hides the answer.

This is a small, zero-dependency script that does it the way it should be done. It runs on your own machine, costs a few cents per run, and prints four numbers no dashboard should hide.

```
$ python3 ai_visibility.py --brand "Firecrawl" --domain firecrawl.dev \
    --category "web scraping and web data extraction APIs" \
    --competitors "Apify:apify.com,Bright Data:brightdata.com,ScrapingBee:scrapingbee.com"

1. ORGANIC VISIBILITY  (non-branded questions — earned, not recalled)
   perplexity     named in 0/3 organic answers · citation share among tracked vendors: 0% (0/3)

2. BRANDED RECALL  (does the engine know you when handed your name?)
   perplexity     recognized in 5/5 branded prompts

3. NAMED INSTEAD OF YOU  (organic questions — your competitive set)
   perplexity:  2x Bright Data   1x ScrapingBee

4. CITATION SUPPLY CHAIN  (the domains engines cite — where to earn a mention)
   perplexity: reddit.com (5), brightdata.com (3), scrapfly.io (1), stackoverflow.com (1), ...
```

That gap — recognized every time it's *handed* the name, named zero times when it *isn't* — is the whole story, and it's the thing a blended "82% visibility" score erases.

## Why most "AI visibility" numbers are wrong

**They ask branded prompts.** A tool that asks *"tell me about Acme"* is measuring whether the model can repeat a name you just gave it. That's **recall**, and it's near-100% for almost everyone. Blend enough branded prompts into the average and any brand looks like it's winning. This script never puts your name in a prompt it scores for visibility. It generates the questions a buyer asks *before* they have a shortlist, and checks whether you come up on merit.

**They report one score.** The same brand can be #1 on Perplexity and #3 inside a Google AI Overview in the same week, because the engines draw on different sources. A single blended number averages engines that disagree and tells you nothing you can act on. This script scores every engine on its own line.

**They stop at the score.** Knowing you're invisible isn't actionable. Knowing that engines cite Reddit, GitHub, and three listicles — and not your blog — is. The script prints the domains the engines actually cite, which is your content-placement roadmap.

## The four numbers

1. **Organic citation share, per engine** — of the buyer questions where a tracked vendor is named/cited, how often is it *you*? Scored per engine, unbranded prompts only. This is discovery, and it's usually humbling.
2. **Branded recall** — do the engines know you when handed your name? Reported on its own line, never averaged into #1. High recall + low organic = a discovery problem, not an awareness problem (a different, cheaper fix).
3. **Named instead of you** — the competitors the engines surface on your organic questions. Your real AI-era competitive set.
4. **Citation supply chain** — the domains engines cite. Engines cite what other people publish about you, not your own site, so this list is where the work is.

## Install & run

Read a [sample report with a partial response](examples/reviewed-run.json). It uses synthetic answers so you can inspect a complete run offline. The existing Firecrawl examples are historical snapshots.

No dependencies beyond Python 3.8+. One API key — [OpenRouter](https://openrouter.ai/keys), which fronts Perplexity, OpenAI, and others behind a single key.

```bash
git clone https://github.com/dovzhikova/ai-visibility-scorer.git
cd ai-visibility-scorer
export OPENROUTER_API_KEY=sk-or-...

python3 ai_visibility.py \
  --brand "Your Product" \
  --domain yourproduct.com \
  --category "what your buyers are actually shopping for, in plain words" \
  --competitors "Rival One:rivalone.com,Rival Two:rivaltwo.io"
```

### Options

| Flag | What it does |
|---|---|
| `--brand` | Your product/company name (required) |
| `--category` | What buyers shop for, in plain words (required) |
| `--domain` | Your domain — enables the citation-share % |
| `--competitors` | `Name:domain` pairs, comma-separated |
| `--prompts` | How many non-branded questions to generate (default 8) |
| `--prompt-file PATH` | Reuse an exact question set across runs. Supply a UTF-8 file with one unbranded question per line. |
| `--engines` | Limit engines, e.g. `perplexity` (default: `perplexity,chatgpt-search`) |
| `--json PATH` | Also write machine-readable results |
| `--concurrency` | Parallel API calls (default 6) |

Cost is a few cents to ~$0.30 a run depending on `--prompts` and engines. Run it monthly and watch the trend — a single run on a small prompt set is noisy, so treat one number as a data point, not a verdict.

In a JSON report, `organic_named.total` counts answered organic questions per engine. Compare it with `attempted` and `failed` to see coverage. Each entry in `checks` records the question, requested model, answer, citation URLs and any error. Save the question file alongside the report when repeating a measurement. The matcher counts brand mentions, including critical or neutral mentions. Read the answer to judge whether the engine recommended the product.

For a human review across four engines, the [GTM Labs AI Answer Visibility Baseline](https://gtm-labs.co/pricing?utm_source=github&utm_medium=readme&utm_campaign=ai_visibility_scorer) includes 48 unbranded buyer questions and two proposed experiments.

## How it works

- **Prompt generation** — an LLM writes the non-branded, high-intent questions a buyer asks in your category, explicitly instructed never to name a vendor. Falls back to templates if generation fails.
- **Fan-out** — every prompt runs against each engine. Perplexity Sonar and GPT-4o-search retrieve the live web and return real citations.
- **Scoring** — organic visibility counts how often you're named/cited without being handed your name; branded recall is measured separately with name-in-prompt questions; the supply chain is the set of domains cited on organic questions.

The counting is per-question (a vendor scores at most once per question per engine) so one heavily-cited listicle can't inflate anyone's number.

## What it deliberately doesn't do

A production audit should also weight the gap list by CPC and buyer intent (a query with 140 searches/month and a $40 CPC matters more than one with 10,000 searches and no commercial intent), run a large enough prompt set to be statistically meaningful, and track the trend over time. This script is the honest core of the method, not the whole thing.

The full methodology — and what happened when the author ran it on her own company and scored **0 out of 96** — is here:
**[Your AI visibility number is lying to you →](https://dariadovzhikova.substack.com/p/your-ai-visibility-number-is-lying)**

## License

MIT. Built by [Daria Dovzhikova](https://dariadovzhikova.substack.com) / The GTM Labs — AI-native go-to-market for developer-first companies. PRs welcome, especially new engine adapters.
