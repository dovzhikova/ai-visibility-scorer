#!/usr/bin/env python3
"""ai-visibility-scorer — measure whether AI engines recommend your product.

The honest version of an "AI visibility" check:

  * scores ORGANIC (non-branded) buyer questions only — it never injects your
    name into a prompt, because "tell me about {you}" just measures whether the
    model can echo a name it was handed (branded recall, ~90% for everyone).
  * scores every engine SEPARATELY — a single blended "visibility score" hides
    that the same brand can rank #1 on Perplexity and #3 on an AI Overview.
  * reports branded recall on its own line, never averaged into the organic
    number.
  * shows who gets cited *instead* of you, and which domains the engines lean
    on (your citation supply chain).

Zero dependencies (Python 3.8+ standard library). One API key: OpenRouter.

    export OPENROUTER_API_KEY=sk-or-...
    python3 ai_visibility.py \\
        --brand "Firecrawl" --domain firecrawl.dev \\
        --category "web scraping and web data extraction APIs" \\
        --competitors "Apify:apify.com,Bright Data:brightdata.com,ScrapingBee:scrapingbee.com"

Full write-up of the methodology (and what the four numbers mean):
https://dariadovzhikova.substack.com/p/your-ai-visibility-number-is-lying

MIT licensed. Built by Daria Dovzhikova / The GTM Labs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

OR_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
URL_RE = re.compile(r"https?://[^\s\)\]\"'>}]+")

# Default engines. `cites=True` engines retrieve the live web and return real
# citations; framing-only engines (Claude, Gemini without browsing) are offered
# as opt-in because they measure who a model *names*, not who it cites.
DEFAULT_ENGINES = [
    {"name": "perplexity", "model": "perplexity/sonar", "cites": True},
    {"name": "chatgpt-search", "model": "openai/gpt-4o-search-preview", "cites": True},
]

BRANDED_TEMPLATES = [
    "What is {brand}?",
    "Is {brand} any good? What do people say about it?",
    "{brand} pricing and plans",
    "{brand} vs its alternatives — how does it compare?",
    "Should I use {brand} for my project?",
]


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def openrouter(model: str, prompt: str, key: str, timeout: int = 120):
    """Return (content, [citation_urls]). Raises on transport error."""
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": prompt}]}
    ).encode()
    req = urllib.request.Request(
        OR_BASE + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/dovzhikova/ai-visibility-scorer",
            "X-Title": "ai-visibility-scorer",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    msg = data["choices"][0]["message"]
    content = msg.get("content") or ""
    cites = []
    for a in msg.get("annotations") or []:
        if a.get("type") == "url_citation":
            u = (a.get("url_citation") or {}).get("url")
            if u:
                cites.append(u)
    # Fall back to URLs embedded in the prose when the engine does not annotate.
    cites += [u.rstrip(".,);]'\"") for u in URL_RE.findall(content)]
    return content, cites


def generate_prompts(category: str, key: str, n: int, model: str) -> list[str]:
    """Ask an LLM for the non-branded questions a buyer asks in `category`.

    The generator is explicitly told never to name a vendor, so the resulting
    prompts test earned discovery, not recall.
    """
    instruction = (
        f"You are helping test which products AI assistants recommend in the "
        f"category: {category}.\n\n"
        f"Write exactly {n} questions a real buyer would type into an AI "
        f"assistant while researching this category, BEFORE they have a "
        f"shortlist. Rules:\n"
        f"- NEVER mention a specific company, product, brand, or tool name.\n"
        f"- Write high-intent questions ('best X for Y', 'how do I do Z', "
        f"comparisons of approaches), not definitions.\n"
        f"- One question per line, no numbering, no preamble.\n"
    )
    try:
        content, _ = openrouter(model, instruction, key)
        prompts = [
            re.sub(r"^\s*[\d.\-*)]+\s*", "", ln).strip()
            for ln in content.splitlines()
            if ln.strip()
        ]
        prompts = [p for p in prompts if len(p) > 12][:n]
        if prompts:
            return prompts
    except Exception as e:  # noqa: BLE001 - fall back to templates on any failure
        print(f"  (prompt generation failed: {e}; using templates)", file=sys.stderr)
    # Deterministic fallback so the tool still runs offline of the generator.
    return [
        f"What are the best tools for {category}?",
        f"What should I look for when choosing a {category} tool?",
        f"What do developers recommend for {category}?",
        f"Which {category} solution is the most reliable at scale?",
        f"What are the top alternatives to consider for {category}?",
    ][:n]


def host(url: str) -> str:
    try:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        return hostname[4:] if hostname.startswith("www.") else hostname
    except ValueError:
        return ""


def domain_owner(url: str, owned: dict[str, str]) -> str | None:
    h = host(url)
    for dom, name in owned.items():
        if h == dom or h.endswith("." + dom):
            return name
    return None


def named_in(text: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text or "", re.IGNORECASE)
    }


def run(args) -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        die("Set OPENROUTER_API_KEY (get one at https://openrouter.ai/keys)")

    competitors = {}
    for pair in filter(None, (args.competitors or "").split(",")):
        name, _, dom = pair.partition(":")
        competitors[name.strip()] = dom.strip().lower()

    # name -> domain, for every tracked vendor (you + competitors)
    owned = {}
    if args.domain:
        owned[host("//" + args.domain)] = args.brand
    for name, dom in competitors.items():
        if dom:
            owned[host("//" + dom)] = name
    tracked_names = [args.brand] + list(competitors)

    engines = list(DEFAULT_ENGINES)
    if args.engines:
        wanted = {e.strip() for e in args.engines.split(",")}
        engines = [e for e in DEFAULT_ENGINES if e["name"] in wanted] or engines

    gen_model = engines[0]["model"] if engines else "perplexity/sonar"
    if args.prompt_file:
        try:
            with open(args.prompt_file, encoding="utf-8") as stream:
                organic = [line.strip() for line in stream if line.strip()]
        except (OSError, UnicodeError) as exc:
            die(f"Cannot read prompt file: {exc}")
        if not organic or len(set(organic)) != len(organic):
            die("Supply at least one buyer question and remove duplicate lines.")
    else:
        print(f"Generating {args.prompts} non-branded buyer questions for: {args.category}")
        organic = generate_prompts(args.category, key, args.prompts, gen_model)
    if any(named_in(q, tracked_names) for q in organic):
        die("A buyer question contains a tracked brand. Use unbranded questions.")
    for q in organic:
        print(f"  · {q}")
    branded = [t.format(brand=args.brand) for t in BRANDED_TEMPLATES]

    jobs = [("organic", q, e) for q in organic for e in engines]
    jobs += [("branded", q, e) for q in branded for e in engines]
    print(f"\nRunning {len(jobs)} calls across {len(engines)} engine(s)...\n")

    def one(kind, prompt, engine):
        check = {"kind": kind, "engine": engine["name"], "model": engine["model"],
                 "prompt": prompt, "cites_web": engine["cites"]}
        try:
            content, cites = openrouter(engine["model"], prompt, key)
            if not content.strip():
                raise ValueError("Empty model response")
            check.update(content=content, citations=list(dict.fromkeys(cites)), err=None)
        except Exception as exc:
            check.update(content=None, citations=[], err=f"{type(exc).__name__}: {exc}")
        return check

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(one, kind, question, engine) for kind, question, engine in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            r = future.result()
            tag = "ERR" if r.get("err") else f"{len(r.get('citations', []))} cites"
            print(f"[{i}/{len(jobs)}] {r['engine']:14s} {r['kind']:7s} {tag}")
    results = [future.result() for future in futures]

    report(args, engines, tracked_names, owned, competitors, results)


def report(args, engines, tracked_names, owned, competitors, results) -> None:
    ok = [r for r in results if not r.get("err")]
    per_engine = {e["name"]: [r for r in ok if r["engine"] == e["name"]] for e in engines}

    print("\n" + "=" * 70)
    print(f"  AI VISIBILITY — {args.brand}")
    print(f"  category: {args.category}")
    print("=" * 70)

    # ── 1. Organic citation share, per engine (unbranded prompts only) ─────────
    print("\n1. ORGANIC VISIBILITY  (non-branded questions — earned, not recalled)")
    print("   Each engine scored on its own. There is no single blended number,")
    print("   on purpose: the same brand ranks differently on each engine.\n")
    supply = defaultdict(lambda: defaultdict(int))  # engine -> domain -> count
    leaderboard = defaultdict(lambda: defaultdict(int))  # engine -> vendor -> queries named
    organic_named = {}
    for e in engines:
        rows = [r for r in per_engine[e["name"]] if r["kind"] == "organic"]
        nq = len(rows)
        you_named = 0
        for r in rows:
            named = named_in(r["content"], tracked_names)
            if args.brand in named:
                you_named += 1
            for v in named:
                leaderboard[e["name"]][v] += 1
            for u in r["citations"]:
                d = host(u)
                if d:
                    supply[e["name"]][d] += 1
        # citation share among tracked vendors (needs domains)
        cite_share = ""
        if owned:
            vendor_cites = defaultdict(int)
            for r in rows:
                seen = set()
                for u in r["citations"]:
                    o = domain_owner(u, owned)
                    if o and o not in seen:
                        seen.add(o)
                        vendor_cites[o] += 1
            total = sum(vendor_cites.values())
            if total:
                you = vendor_cites.get(args.brand, 0)
                cite_share = f" · citation share among tracked vendors: {100*you/total:.0f}% ({you}/{total})"
        attempts = sum(r["kind"] == "organic" and r["engine"] == e["name"] for r in results)
        organic_named[e["name"]] = {
            "named": you_named, "total": nq, "attempted": attempts, "failed": attempts - nq,
        }
        print(f"   {e['name']:14s} named in {you_named}/{nq} organic answers{cite_share}"
              f" ({nq} answered of {attempts} attempted)")

    # ── 2. Branded recall (its own line — NEVER averaged into #1) ──────────────
    print("\n2. BRANDED RECALL  (does the engine know you when handed your name?)")
    print("   Reported separately. High here + low above = a discovery problem,")
    print("   not an awareness problem. Do not blend this into visibility.\n")
    for e in engines:
        rows = [r for r in per_engine[e["name"]] if r["kind"] == "branded"]
        hit = sum(1 for r in rows if named_in(r["content"], [args.brand]))
        nq = len(rows)
        print(f"   {e['name']:14s} recognized in {hit}/{nq} branded prompts")

    # ── 3. Who gets named instead of you ───────────────────────────────────────
    print("\n3. NAMED INSTEAD OF YOU  (organic questions — your competitive set)")
    for e in engines:
        board = leaderboard[e["name"]]
        if not board:
            continue
        ranked = sorted(board.items(), key=lambda kv: -kv[1])
        print(f"\n   {e['name']}:")
        for vendor, cnt in ranked[:8]:
            star = "  <- you" if vendor == args.brand else ""
            print(f"     {cnt:2d}x  {vendor}{star}")

    # ── 4. Citation supply chain (which domains the engines actually cite) ─────
    print("\n4. CITATION SUPPLY CHAIN  (the domains engines cite — where to earn a mention)")
    print("   Engines cite what other people publish, not your own site. This is")
    print("   your content-placement roadmap.\n")
    for e in engines:
        if not e["cites"]:
            continue
        dom = supply[e["name"]]
        if not dom:
            continue
        ranked = sorted(dom.items(), key=lambda kv: -kv[1])[:10]
        print(f"   {e['name']}: " + ", ".join(f"{d} ({c})" for d, c in ranked))

    errs = [r for r in results if r.get("err")]
    if errs:
        print(f"\n({len(errs)} call(s) errored — partial data above.)")

    print("\n" + "-" * 70)
    print("  What this does NOT do (and a full audit should): weight the gap list")
    print("  by CPC/intent, run a larger prompt set for significance, and track")
    print("  the trend month over month. Methodology + the four numbers:")
    print("  https://dariadovzhikova.substack.com/p/your-ai-visibility-number-is-lying")
    print("  Human-reviewed GTM Labs baseline: https://gtm-labs.co/pricing"
          "?utm_source=github&utm_medium=cli&utm_campaign=ai_visibility_scorer")
    print("-" * 70)

    if args.json:
        payload = {
            "brand": args.brand,
            "category": args.category,
            "engines": [e["name"] for e in engines],
            "organic_named": organic_named,
            "checks": results,
            "supply_chain": {e["name"]: dict(supply[e["name"]]) for e in engines},
            "generated_at": int(time.time()),
        }
        with open(args.json, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nWrote {args.json}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Measure whether AI engines recommend your product (organic, per-engine).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--brand", required=True, help="Your product/company name")
    ap.add_argument("--category", required=True, help="What buyers are shopping for, in plain words")
    ap.add_argument("--domain", default="", help="Your domain, e.g. example.com (enables citation share)")
    ap.add_argument("--competitors", default="",
                    help="Comma list of Name:domain, e.g. 'Apify:apify.com,Bright Data:brightdata.com'")
    prompts = ap.add_mutually_exclusive_group()
    prompts.add_argument("--prompts", type=int, default=8, help="How many non-branded questions (default 8)")
    prompts.add_argument("--prompt-file", help="Use one unbranded question per line from a UTF-8 file")
    ap.add_argument("--engines", default="", help="Comma list to limit engines (default: perplexity,chatgpt-search)")
    ap.add_argument("--concurrency", type=int, default=6, help="Parallel API calls (default 6)")
    ap.add_argument("--json", default="", help="Also write machine-readable results to this path")
    run(ap.parse_args())


if __name__ == "__main__":
    main()
