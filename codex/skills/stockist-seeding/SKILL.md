---
name: stockist-seeding
description: Find and prepare evidence-backed Grainfinder stockist candidates for a country, city, or region, using the bundled Python collector plus Codex review before editing data/stockists.json.
---

# Stockist Seeding

Use this skill when asked to seed or expand `data/stockists.json` with shops that sell photographic film.

## Workflow

1. Run the bundled collector from the project root:

   ```sh
   python3 codex/skills/stockist-seeding/scripts/seed_stockists.py "Amsterdam, Netherlands" --output /tmp/grainfinder-stockists.json
   ```

2. If running inside Codex with network-restricted shell commands, do not first try the collector in the default sandbox. The collector fetches websites, so request network approval immediately for the single command and suggest this reusable prefix:

   ```text
   ["python3", "-B", "codex/skills/stockist-seeding/scripts/seed_stockists.py"]
   ```

   When the user allows that prefix permanently in Codex, later collector runs should not need another confirmation. This is a Codex permission setting, not something the skill can bypass from project code.

3. Prefer API-backed search providers instead of scraping Google result pages:
   - `SERPAPI_API_KEY` uses SerpAPI's Google engine.
   - `GOOGLE_API_KEY` plus `GOOGLE_CSE_ID` uses Google Custom Search.
   - `BRAVE_SEARCH_API_KEY` uses Brave Search.
   - If no search API is available, pass explicit URLs with `--urls` or `--search-file`.

4. Review each candidate's `evidence` and `codexReviewPrompt`. Do not add a shop from search snippets alone; the shop website or another primary page must indicate film sales or clearly confirm it is an analog photo shop that sells film.

5. When a candidate is valid, add an object to `data/stockists.json` matching the existing schema:
   - Stable `id` generated from shop name and city.
   - Exact public shop address and coordinates where possible.
   - `stocks` only for services/products supported by evidence.
   - `confirmationType: "web"` and a `confirmations` entry for web evidence.
   - `sourceUrl` pointing to the best confirming page, not just the homepage when a product/category/contact page is stronger.

6. Keep uncertain candidates out of `data/stockists.json`. If useful, preserve them in a temporary candidate JSON file for later manual follow-up.

## Collector Notes

- The script intentionally separates collection from final judgment. It scores website text and drafts entries, but Codex or a human should decide whether evidence is strong enough.
- Use `--geocode` only when you have an exact address; it calls OpenStreetMap Nominatim and should be run politely. Prefer this over separate `curl` geocoding commands so there is only one network-approved command.
- Use `--include-rejected` when debugging search quality.
- Use `--insecure-skip-verify` only when local Python certificate verification is broken; keep normal TLS verification on by default.
- Check `possibleDuplicates` before adding branch entries, because some stockists have multiple locations on the same domain.
