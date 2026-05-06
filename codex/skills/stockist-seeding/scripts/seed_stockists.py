#!/usr/bin/env python3
"""Collect evidence-backed candidate stockists for Grainfinder.

The script is deliberately conservative: it collects search results, fetches
candidate websites, scores page text for film-sales evidence, and emits drafts
for Codex or human review. It does not edit data/stockists.json directly.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import os
import re
import ssl
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


USER_AGENT = (
    "GrainfinderStockistSeeder/0.1 "
    "(https://github.com/danielbradburn/grainfinder)"
)
MAX_DOWNLOAD_BYTES = 2_000_000
MAX_TEXT_CHARS = 24_000
DEFAULT_MIN_SCORE = 4
PROMISING_SCORE = 8

DEFAULT_QUERY_TEMPLATES = [
    "shops selling photographic film in {location}",
    "35mm film shop in {location}",
    "buy camera film in {location}",
    "analog photography store film rolls in {location}",
]

SIGNAL_GROUPS = [
    {
        "key": "film_sales",
        "label": "film sales",
        "weight": 5,
        "patterns": [
            r"\bshops?\s+selling\s+photographic\s+film\b",
            r"\bsell(?:s|ing)?\s+(?:camera\s+)?film\b",
            r"\bbuy\s+(?:camera\s+)?film\b",
            r"\bcamera\s+film\b",
            r"\bphotographic\s+film\b",
            r"\bphoto\s+film\b",
            r"\bfilm\s+rolls?\b",
            r"\bfresh\s+film\b",
            r"\bfilm\s+stock\b",
            r"\bpellicules?\s+photo",
            r"\bpellicules?\s+argentiques?\b",
            r"\bvente\s+de\s+pellicules?\b",
            r"\bfotorolletjes\b",
            r"\bfotofilm\b",
            r"\bfilmy\b",
            r"\bfilme\b",
            r"\bpel[ií]cula\s+fotogr[aá]fica\b",
            r"\bcarretes?\s+fotogr[aá]ficos?\b",
            r"\bpellicole?\s+fotografiche?\b",
            r"\bfilmrullar\b",
        ],
    },
    {
        "key": "film_formats",
        "label": "35mm/120 film",
        "weight": 3,
        "patterns": [
            r"\b35\s*mm\b",
            r"\b135\s+film\b",
            r"\b120\s+film\b",
            r"\bmedium\s+format\s+film\b",
            r"\bblack\s+and\s+white\s+film\b",
            r"\bcolour\s+negative\s+film\b",
            r"\bcolor\s+negative\s+film\b",
            r"\bslide\s+film\b",
        ],
    },
    {
        "key": "film_brands",
        "label": "film brands",
        "weight": 2,
        "patterns": [
            r"\bkodak\b",
            r"\bilford\b",
            r"\bfujifilm\b",
            r"\bcinestill\b",
            r"\blomography\b",
            r"\bharman\b",
            r"\bfoma(?:pan)?\b",
            r"\brollei\b",
        ],
    },
    {
        "key": "analog_shop",
        "label": "analog photography shop",
        "weight": 2,
        "patterns": [
            r"\banalog(?:ue)?\s+photography\b",
            r"\banalog(?:ue)?\s+photo\b",
            r"\banalog(?:ue)?\s+cameras?\b",
            r"\bfilm\s+cameras?\b",
            r"\bdarkroom\s+supplies\b",
            r"\bargentique\b",
        ],
    },
    {
        "key": "film_developing",
        "label": "film developing",
        "weight": 1,
        "patterns": [
            r"\bfilm\s+develop(?:ing|ment)\b",
            r"\bdevelop\s+(?:your\s+)?film\b",
            r"\bprocessing\s+(?:and\s+)?scanning\b",
            r"\bdeveloppement\s+argentique\b",
            r"\bd[eé]veloppement\s+film\b",
            r"\brevelado\b",
            r"\bsviluppo\s+pellicole\b",
        ],
    },
    {
        "key": "film_scanning",
        "label": "film scanning",
        "weight": 1,
        "patterns": [
            r"\bfilm\s+scann(?:ing|en)\b",
            r"\bscan\s+(?:your\s+)?film\b",
            r"\bscans?\s+of\s+your\s+negatives\b",
            r"\bnum[eé]risation\b",
        ],
    },
    {
        "key": "retail_presence",
        "label": "retail presence",
        "weight": 2,
        "patterns": [
            r"\bshop\b",
            r"\bstore\b",
            r"\bvisit\s+us\b",
            r"\bopening\s+hours\b",
            r"\bwinkel\b",
            r"\bmagasin\b",
            r"\btienda\b",
            r"\bnegozio\b",
            r"\bfiliale\b",
        ],
    },
    {
        "key": "disposable_cameras",
        "label": "disposable cameras",
        "weight": 1,
        "patterns": [
            r"\bdisposable\s+cameras?\b",
            r"\bwegwerpcamera",
            r"\bappareils?\s+jetables?\b",
        ],
    },
]

NEGATIVE_PATTERNS = [
    r"\bdoes\s+not\s+sell\s+film\b",
    r"\bno\s+longer\s+sells?\s+film\b",
    r"\bnot\s+currently\s+stock(?:ing|ed)\b",
]

LINK_HINTS = [
    "film",
    "films",
    "filmy",
    "filme",
    "analog",
    "analogue",
    "argentique",
    "pellicule",
    "pellicules",
    "fotorol",
    "fotofilm",
    "shop",
    "store",
    "winkel",
    "magasin",
    "tienda",
    "negozio",
    "contact",
    "about",
    "location",
    "locations",
    "filiale",
    "develop",
    "scan",
    "lab",
]

LINK_BLOCKLIST = [
    "account",
    "basket",
    "cart",
    "checkout",
    "cookie",
    "privacy",
    "terms",
    "wishlist",
]

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE_RE = re.compile(r"(?:(?:\+\d{1,3}[\s().-]*)?(?:\d[\s().-]*){7,}\d)")
WHITESPACE_RE = re.compile(r"\s+")


@dataclasses.dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = "manual"
    query: str = ""


@dataclasses.dataclass
class LinkInfo:
    url: str
    text: str


@dataclasses.dataclass
class PageInfo:
    url: str
    title: str
    description: str
    meta: dict[str, str]
    text: str
    links: list[LinkInfo]
    json_ld: list[Any]
    emails: list[str]
    phones: list[str]
    error: str = ""


class SimpleHTMLExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.description = ""
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self.links: list[LinkInfo] = []
        self.json_ld_blocks: list[str] = []
        self._ignored_depth = 0
        self._in_title = False
        self._in_json_ld = False
        self._json_ld_parts: list[str] = []
        self._current_link_href = ""
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()

        if tag in {"script", "style", "noscript", "svg"}:
            script_type = attrs_dict.get("type", "").lower()
            if tag == "script" and "ld+json" in script_type:
                self._in_json_ld = True
                self._json_ld_parts = []
            else:
                self._ignored_depth += 1
            return

        if self._ignored_depth:
            return

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            content = clean_text(attrs_dict.get("content", ""))
            if key and content:
                self.meta[key] = content
                if key in {"description", "og:description"} and not self.description:
                    self.description = content
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self._current_link_href = href
                self._current_link_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._in_json_ld and tag == "script":
            self.json_ld_blocks.append("".join(self._json_ld_parts))
            self._in_json_ld = False
            self._json_ld_parts = []
            return

        if tag in {"script", "style", "noscript", "svg"} and self._ignored_depth:
            self._ignored_depth -= 1
            return

        if self._ignored_depth:
            return

        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._current_link_href:
            absolute_url = normalize_candidate_url(urljoin(self.base_url, self._current_link_href))
            if absolute_url:
                self.links.append(LinkInfo(absolute_url, clean_text(" ".join(self._current_link_text))))
            self._current_link_href = ""
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_ld_parts.append(data)
            return
        if self._ignored_depth:
            return

        value = clean_text(data)
        if not value:
            return
        if self._in_title:
            self.title = clean_text(f"{self.title} {value}")
        if self._current_link_href:
            self._current_link_text.append(value)
        self.text_parts.append(value)

    def page_info(self, url: str) -> PageInfo:
        text = clean_text(" ".join(self.text_parts))
        json_ld = []
        for block in self.json_ld_blocks:
            json_ld.extend(parse_json_ld(block))
        return PageInfo(
            url=url,
            title=clean_text(html.unescape(self.title)),
            description=self.description,
            meta=self.meta,
            text=text[:MAX_TEXT_CHARS],
            links=self.links,
            json_ld=json_ld,
            emails=ordered_unique(EMAIL_RE.findall(text)),
            phones=ordered_unique(clean_phone(match.group(0)) for match in PHONE_RE.finditer(text)),
        )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    queries = build_queries(args.location, args.query)
    existing_stockists = load_existing_stockists(Path(args.existing_stockists))

    provider = resolve_search_provider(args)
    search_results = collect_search_results(args, provider, queries)
    search_results = dedupe_search_results(search_results)[: args.max_results]

    if not search_results:
        raise SystemExit("No search results or manual URLs were supplied.")

    candidates = []
    for index, result in enumerate(search_results, start=1):
        print(
            f"[{index}/{len(search_results)}] analyzing {display_host(result.url)}",
            file=sys.stderr,
        )
        candidate = analyze_result(result, args, existing_stockists)
        if args.include_rejected or candidate["score"] >= args.min_score:
            candidates.append(candidate)
        if args.delay and index < len(search_results):
            time.sleep(args.delay)

    candidates.sort(key=lambda item: item["score"], reverse=True)

    output = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "location": args.location,
        "queries": queries,
        "searchProvider": provider,
        "candidateCount": len(candidates),
        "criteria": {
            "minScore": args.min_score,
            "promisingScore": PROMISING_SCORE,
            "maxPagesPerSite": args.max_pages_per_site,
            "geocoded": args.geocode,
        },
        "candidates": candidates,
        "nextSteps": [
            "Review each candidate's evidence and codexReviewPrompt.",
            "Verify exact address and coordinates before adding to data/stockists.json.",
            "Do not add entries supported only by search snippets or generic lab-service text.",
        ],
    }

    write_output(output, args.output)
    print(f"wrote {len(candidates)} candidate(s) to {args.output}", file=sys.stderr)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect candidate photographic film stockists for Grainfinder.",
    )
    parser.add_argument("location", help="Country, city, or region to search.")
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Custom query template. Use {location}; can be repeated.",
    )
    parser.add_argument(
        "--search-provider",
        choices=["auto", "serpapi", "google-cse", "brave", "manual"],
        default="auto",
        help="Search provider. auto chooses an API provider based on env vars.",
    )
    parser.add_argument(
        "--urls",
        nargs="*",
        default=[],
        help="Manual candidate URLs. Useful when no search API is configured.",
    )
    parser.add_argument(
        "--search-file",
        help="JSON or newline-delimited file of search result URLs/objects.",
    )
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--max-pages-per-site", type=int, default=4)
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--geocode", action="store_true", help="Geocode exact addresses with Nominatim.")
    parser.add_argument("--delay", type=float, default=0.7, help="Delay between site fetches.")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        help="Disable TLS certificate verification for local Python installs with broken CA bundles.",
    )
    parser.add_argument("--output", default="-", help="Output path, or '-' for stdout.")
    parser.add_argument("--today", default=dt.date.today().isoformat())
    parser.add_argument(
        "--existing-stockists",
        default="data/stockists.json",
        help="Existing stockist JSON for duplicate hints.",
    )
    return parser.parse_args(argv)


def build_queries(location: str, custom_queries: list[str]) -> list[str]:
    templates = custom_queries or DEFAULT_QUERY_TEMPLATES
    return [template.format(location=location) for template in templates]


def resolve_search_provider(args: argparse.Namespace) -> str:
    if args.search_provider != "auto":
        return args.search_provider
    if args.urls or args.search_file:
        return "manual"
    if os.getenv("SERPAPI_API_KEY"):
        return "serpapi"
    if os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_ID"):
        return "google-cse"
    if os.getenv("BRAVE_SEARCH_API_KEY"):
        return "brave"
    raise SystemExit(
        "No search provider configured. Set SERPAPI_API_KEY, GOOGLE_API_KEY + "
        "GOOGLE_CSE_ID, BRAVE_SEARCH_API_KEY, or pass --urls/--search-file."
    )


def collect_search_results(
    args: argparse.Namespace,
    provider: str,
    queries: list[str],
) -> list[SearchResult]:
    manual_results = load_manual_results(args.urls, args.search_file)
    if provider == "manual":
        return manual_results

    results = list(manual_results)
    per_query_limit = max(1, min(args.max_results, 10))
    for query in queries:
        if provider == "serpapi":
            results.extend(search_serpapi(query, per_query_limit, args.timeout, args.insecure_skip_verify))
        elif provider == "google-cse":
            results.extend(search_google_cse(query, per_query_limit, args.timeout, args.insecure_skip_verify))
        elif provider == "brave":
            results.extend(search_brave(query, per_query_limit, args.timeout, args.insecure_skip_verify))
    return results


def load_manual_results(urls: list[str], search_file: str | None) -> list[SearchResult]:
    results = [SearchResult(title="", url=normalize_manual_url(url), source="manual") for url in urls]
    if not search_file:
        return [result for result in results if result.url]

    path = Path(search_file)
    raw = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [line.strip() for line in raw.splitlines() if line.strip()]

    if isinstance(parsed, dict):
        parsed = parsed.get("results") or parsed.get("items") or parsed.get("candidates") or []

    if not isinstance(parsed, list):
        raise SystemExit(f"{search_file} must contain a JSON list/object or newline URLs.")

    for item in parsed:
        if isinstance(item, str):
            url = normalize_manual_url(item)
            if url:
                results.append(SearchResult(title="", url=url, source="manual-file"))
        elif isinstance(item, dict):
            url = normalize_manual_url(str(item.get("url") or item.get("link") or ""))
            if url:
                results.append(
                    SearchResult(
                        title=str(item.get("title") or ""),
                        url=url,
                        snippet=str(item.get("snippet") or item.get("description") or ""),
                        source=str(item.get("source") or "manual-file"),
                        query=str(item.get("query") or ""),
                    )
                )
    return [result for result in results if result.url]


def search_serpapi(
    query: str,
    limit: int,
    timeout: float,
    insecure_skip_verify: bool = False,
) -> list[SearchResult]:
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        raise SystemExit("SERPAPI_API_KEY is required for --search-provider serpapi.")

    params = urlencode({"engine": "google", "q": query, "num": limit, "api_key": api_key})
    data = http_json(
        f"https://serpapi.com/search.json?{params}",
        timeout=timeout,
        insecure_skip_verify=insecure_skip_verify,
    )
    results = []
    for item in data.get("organic_results", []):
        url = normalize_candidate_url(str(item.get("link") or ""))
        if url:
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("snippet") or ""),
                    source="serpapi-google",
                    query=query,
                )
            )
    return results


def search_google_cse(
    query: str,
    limit: int,
    timeout: float,
    insecure_skip_verify: bool = False,
) -> list[SearchResult]:
    api_key = os.getenv("GOOGLE_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID")
    if not api_key or not cse_id:
        raise SystemExit("GOOGLE_API_KEY and GOOGLE_CSE_ID are required for google-cse.")

    params = urlencode({"q": query, "num": min(limit, 10), "key": api_key, "cx": cse_id})
    data = http_json(
        f"https://www.googleapis.com/customsearch/v1?{params}",
        timeout=timeout,
        insecure_skip_verify=insecure_skip_verify,
    )
    results = []
    for item in data.get("items", []):
        url = normalize_candidate_url(str(item.get("link") or ""))
        if url:
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("snippet") or ""),
                    source="google-cse",
                    query=query,
                )
            )
    return results


def search_brave(
    query: str,
    limit: int,
    timeout: float,
    insecure_skip_verify: bool = False,
) -> list[SearchResult]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        raise SystemExit("BRAVE_SEARCH_API_KEY is required for --search-provider brave.")

    params = urlencode({"q": query, "count": min(limit, 20)})
    data = http_json(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"X-Subscription-Token": api_key},
        timeout=timeout,
        insecure_skip_verify=insecure_skip_verify,
    )
    results = []
    for item in data.get("web", {}).get("results", []):
        url = normalize_candidate_url(str(item.get("url") or ""))
        if url:
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("description") or ""),
                    source="brave",
                    query=query,
                )
            )
    return results


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    insecure_skip_verify: bool = False,
) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
    with urlopen(request, timeout=timeout, context=ssl_context(insecure_skip_verify)) as response:
        raw = response.read(MAX_DOWNLOAD_BYTES)
    return json.loads(raw.decode("utf-8"))


def ssl_context(insecure_skip_verify: bool) -> ssl.SSLContext | None:
    if not insecure_skip_verify:
        return None
    return ssl._create_unverified_context()


def dedupe_search_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    deduped = []
    for result in results:
        key = canonical_url_key(result.url)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def analyze_result(
    result: SearchResult,
    args: argparse.Namespace,
    existing_stockists: list[dict[str, Any]],
) -> dict[str, Any]:
    pages = crawl_candidate_site(
        result.url,
        args.max_pages_per_site,
        args.timeout,
        args.insecure_skip_verify,
    )
    evidence_pages = []
    all_signals: dict[str, dict[str, Any]] = {}

    for page in pages:
        page_analysis = analyze_page_text(page.text, args.location)
        for key, signal in page_analysis["signals"].items():
            existing = all_signals.setdefault(
                key,
                {"label": signal["label"], "weight": signal["weight"], "snippets": []},
            )
            existing["snippets"].extend(signal["snippets"])
        evidence_pages.append(
            {
                "url": page.url,
                "title": page.title,
                "description": page.description,
                "score": page_analysis["score"],
                "signals": sorted(page_analysis["signals"].keys()),
                "snippets": page_analysis["snippets"][:8],
                "error": page.error,
            }
        )

    for signal in all_signals.values():
        signal["snippets"] = ordered_unique(signal["snippets"])[:6]

    total_score = sum(int(signal["weight"]) for signal in all_signals.values())
    negative_hits = find_negative_hits(" ".join(page.text for page in pages))
    if negative_hits:
        total_score -= 3

    source_page = best_source_page(evidence_pages, result.url)
    business = extract_business_info(pages, result, args.location)

    if args.geocode and not business.get("latitude") and has_exact_address(business):
        geocoded = geocode_business(business, args.timeout, args.insecure_skip_verify)
        business.update({key: value for key, value in geocoded.items() if value is not None})

    draft = build_stockist_draft(business, source_page["url"], all_signals, args.today)
    possible_duplicates = find_possible_duplicates(draft, existing_stockists)
    status = classify_candidate(total_score, all_signals, negative_hits)
    review_prompt = build_codex_review_prompt(draft, status, total_score, evidence_pages, negative_hits)

    return {
        "status": status,
        "score": total_score,
        "searchResult": dataclasses.asdict(result),
        "stockistDraft": draft,
        "possibleDuplicates": possible_duplicates,
        "signals": all_signals,
        "negativeSignals": negative_hits,
        "evidence": evidence_pages,
        "codexReviewPrompt": review_prompt,
    }


def crawl_candidate_site(
    url: str,
    max_pages: int,
    timeout: float,
    insecure_skip_verify: bool = False,
) -> list[PageInfo]:
    start_url = normalize_candidate_url(url)
    if not start_url:
        return []

    visited: set[str] = set()
    pending = [start_url]
    pages: list[PageInfo] = []

    while pending and len(pages) < max_pages:
        current_url = pending.pop(0)
        key = canonical_url_key(current_url)
        if not key or key in visited:
            continue
        visited.add(key)
        page = fetch_page(current_url, timeout, insecure_skip_verify)
        pages.append(page)

        if page.error:
            continue

        ranked_links = sorted(
            (link for link in page.links if same_site(start_url, link.url)),
            key=score_link,
            reverse=True,
        )
        for link in ranked_links:
            if len(pending) + len(pages) >= max_pages:
                break
            link_key = canonical_url_key(link.url)
            if link_key and link_key not in visited and link_key not in {canonical_url_key(item) for item in pending}:
                if score_link(link) > 0:
                    pending.append(link.url)

    return pages


def fetch_page(url: str, timeout: float, insecure_skip_verify: bool = False) -> PageInfo:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en,nl,fr,de,es,it;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout, context=ssl_context(insecure_skip_verify)) as response:
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            if content_type and not any(token in content_type.lower() for token in ["html", "xml", "text"]):
                return empty_page(final_url, f"unsupported content type: {content_type}")
            raw = response.read(MAX_DOWNLOAD_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        return empty_page(url, f"HTTP {error.code}")
    except URLError as error:
        return empty_page(url, f"URL error: {error.reason}")
    except TimeoutError:
        return empty_page(url, "timeout")

    try:
        markup = raw.decode(charset, errors="replace")
    except LookupError:
        markup = raw.decode("utf-8", errors="replace")

    extractor = SimpleHTMLExtractor(final_url)
    try:
        extractor.feed(markup)
    except Exception as error:  # HTMLParser can fail on badly malformed markup.
        page = extractor.page_info(final_url)
        page.error = f"partial parse: {error}"
        return page
    return extractor.page_info(final_url)


def empty_page(url: str, error: str) -> PageInfo:
    return PageInfo(
        url=url,
        title="",
        description="",
        meta={},
        text="",
        links=[],
        json_ld=[],
        emails=[],
        phones=[],
        error=error,
    )


def analyze_page_text(text: str, location: str) -> dict[str, Any]:
    signals: dict[str, dict[str, Any]] = {}
    score = 0
    for group in SIGNAL_GROUPS:
        snippets = snippets_for_patterns(text, group["patterns"])
        if snippets:
            signals[group["key"]] = {
                "label": group["label"],
                "weight": group["weight"],
                "snippets": snippets,
            }
            score += int(group["weight"])

    location_snippets = snippets_for_location(text, location)
    if location_snippets:
        signals["target_location"] = {
            "label": "target location",
            "weight": 1,
            "snippets": location_snippets,
        }
        score += 1

    return {
        "score": score,
        "signals": signals,
        "snippets": ordered_unique(
            snippet for signal in signals.values() for snippet in signal["snippets"]
        )[:10],
    }


def snippets_for_patterns(text: str, patterns: list[str], radius: int = 110) -> list[str]:
    snippets = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            start = max(0, match.start() - radius)
            end = min(len(text), match.end() + radius)
            snippets.append(clean_text(text[start:end]))
            if len(snippets) >= 4:
                return ordered_unique(snippets)
    return ordered_unique(snippets)


def snippets_for_location(text: str, location: str) -> list[str]:
    tokens = [
        token
        for token in re.split(r"[^A-Za-z0-9]+", location)
        if len(token) >= 4 and token.lower() not in {"city", "country", "region"}
    ]
    snippets = []
    for token in tokens[:4]:
        snippets.extend(snippets_for_patterns(text, [rf"\b{re.escape(token)}\b"], radius=80))
    return ordered_unique(snippets)[:3]


def find_negative_hits(text: str) -> list[str]:
    return ordered_unique(snippet for pattern in NEGATIVE_PATTERNS for snippet in snippets_for_patterns(text, [pattern]))


def score_link(link: LinkInfo) -> int:
    haystack = f"{link.url} {link.text}".lower()
    if any(blocked in haystack for blocked in LINK_BLOCKLIST):
        return -5
    score = 0
    for hint in LINK_HINTS:
        if hint in haystack:
            score += 1
    if "film" in haystack or "pellic" in haystack or "fotorol" in haystack:
        score += 3
    if "contact" in haystack or "location" in haystack or "about" in haystack:
        score += 1
    return score


def best_source_page(evidence_pages: list[dict[str, Any]], fallback_url: str) -> dict[str, Any]:
    if not evidence_pages:
        return {"url": fallback_url, "score": 0}
    return max(evidence_pages, key=lambda page: page.get("score", 0))


def extract_business_info(
    pages: list[PageInfo],
    result: SearchResult,
    location: str,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "name": "",
        "address": "",
        "city": "",
        "postalCode": "",
        "country": "",
        "latitude": None,
        "longitude": None,
        "website": site_root(result.url),
        "phone": "",
        "email": "",
    }

    for page in pages:
        for entity in iter_json_ld_entities(page.json_ld):
            candidate = business_from_json_ld(entity)
            if not candidate:
                continue
            merge_missing(info, candidate)
            if has_exact_address(info) and info.get("name"):
                break

    if not info["name"]:
        info["name"] = clean_business_name(
            first_non_empty(
                [page.meta.get("og:site_name", "") for page in pages]
            )
        )
    if not info["name"]:
        info["name"] = clean_business_name(first_non_empty([page.title for page in pages]) or result.title)
    if not info["name"]:
        info["name"] = display_host(result.url)

    first_page = pages[0] if pages else None
    if first_page:
        if not info["email"]:
            info["email"] = first_non_empty(first_page.emails)
        if not info["phone"]:
            info["phone"] = first_non_empty(first_page.phones)

    city, country = infer_city_country(location)
    info["city"] = info["city"] or city
    info["country"] = info["country"] or country
    info["website"] = normalize_candidate_url(info["website"]) or site_root(result.url)
    return info


def business_from_json_ld(entity: dict[str, Any]) -> dict[str, Any] | None:
    entity_type = jsonld_type(entity).lower()
    has_business_type = any(
        token in entity_type
        for token in [
            "localbusiness",
            "store",
            "organization",
            "photograph",
            "professionalservice",
        ]
    )
    address = entity.get("address") or entity.get("location", {}).get("address") if isinstance(entity.get("location"), dict) else entity.get("address")
    if not has_business_type and not address:
        return None

    info: dict[str, Any] = {}
    if entity.get("name"):
        info["name"] = clean_text(str(entity["name"]))
    if entity.get("url"):
        info["website"] = str(entity["url"])
    if entity.get("telephone"):
        info["phone"] = clean_phone(str(entity["telephone"]))
    if entity.get("email"):
        info["email"] = clean_text(str(entity["email"]).replace("mailto:", ""))

    if isinstance(address, list):
        address = next((item for item in address if isinstance(item, dict)), address[0] if address else None)
    if isinstance(address, dict):
        info["address"] = clean_text(str(address.get("streetAddress") or ""))
        info["city"] = clean_text(str(address.get("addressLocality") or ""))
        info["postalCode"] = clean_text(str(address.get("postalCode") or ""))
        country = address.get("addressCountry") or ""
        if isinstance(country, dict):
            country = country.get("name") or country.get("@id") or ""
        info["country"] = clean_text(str(country))
    elif isinstance(address, str):
        info["address"] = clean_text(address)

    geo = entity.get("geo")
    if isinstance(geo, dict):
        info["latitude"] = parse_float(geo.get("latitude"))
        info["longitude"] = parse_float(geo.get("longitude"))
    return {key: value for key, value in info.items() if value not in ("", None)}


def iter_json_ld_entities(values: list[Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            if "@graph" in value:
                walk(value["@graph"])
            entities.append(value)

    walk(values)
    return entities


def parse_json_ld(block: str) -> list[Any]:
    block = block.strip()
    if not block:
        return []
    try:
        return [json.loads(block)]
    except json.JSONDecodeError:
        return []


def jsonld_type(entity: dict[str, Any]) -> str:
    value = entity.get("@type", "")
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def has_exact_address(info: dict[str, Any]) -> bool:
    return bool(info.get("address") and (info.get("city") or info.get("postalCode") or info.get("country")))


def geocode_business(
    info: dict[str, Any],
    timeout: float,
    insecure_skip_verify: bool = False,
) -> dict[str, Any]:
    query = ", ".join(
        part
        for part in [info.get("address"), info.get("city"), info.get("postalCode"), info.get("country")]
        if part
    )
    if not query:
        return {}

    url = f"https://nominatim.openstreetmap.org/search?{urlencode({'format': 'jsonv2', 'limit': 1, 'q': query})}"
    try:
        data = http_json(url, timeout=timeout, insecure_skip_verify=insecure_skip_verify)
    except Exception as error:
        print(f"geocode failed for {query}: {error}", file=sys.stderr)
        return {}
    if not isinstance(data, list) or not data:
        return {}
    return {
        "latitude": parse_float(data[0].get("lat")),
        "longitude": parse_float(data[0].get("lon")),
    }


def build_stockist_draft(
    business: dict[str, Any],
    source_url: str,
    signals: dict[str, dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    city = str(business.get("city") or "")
    name = str(business.get("name") or display_host(source_url))
    confirmed_by = display_host(source_url)
    stocks = infer_stocks(signals)
    return {
        "id": slugify("-".join(part for part in [name, city] if part)),
        "name": name,
        "address": str(business.get("address") or ""),
        "city": city,
        "postalCode": str(business.get("postalCode") or ""),
        "country": str(business.get("country") or ""),
        "latitude": business.get("latitude"),
        "longitude": business.get("longitude"),
        "website": str(business.get("website") or site_root(source_url)),
        "phone": str(business.get("phone") or ""),
        "email": str(business.get("email") or ""),
        "stocks": stocks,
        "notes": build_notes(signals),
        "confirmedBy": confirmed_by,
        "confirmedDate": today,
        "confirmationType": "web",
        "sourceUrl": source_url,
        "confirmations": [
            {
                "type": "web",
                "confirmedDate": today,
                "confirmedBy": confirmed_by,
                "sourceUrl": source_url,
            }
        ],
    }


def infer_stocks(signals: dict[str, dict[str, Any]]) -> list[str]:
    stocks = []
    if any(key in signals for key in ["film_sales", "film_formats", "film_brands"]):
        stocks.append("Photographic film")
    if "analog_shop" in signals:
        stocks.append("Analog cameras")
    if "disposable_cameras" in signals:
        stocks.append("Disposable cameras")
    if "film_developing" in signals:
        stocks.append("Film developing")
    if "film_scanning" in signals:
        stocks.append("Film scanning")
    if any("darkroom" in snippet.lower() for signal in signals.values() for snippet in signal.get("snippets", [])):
        stocks.append("Darkroom supplies")
    return stocks or ["Photographic film"]


def build_notes(signals: dict[str, dict[str, Any]]) -> str:
    labels = [signal["label"] for signal in signals.values() if signal["label"] != "target location"]
    if not labels:
        return "Candidate found by search; film stock needs manual confirmation."
    label_text = ", ".join(ordered_unique(labels)[:5])
    return f"Website evidence mentions {label_text}. Review source page snippets before committing."


def classify_candidate(
    score: int,
    signals: dict[str, dict[str, Any]],
    negative_hits: list[str],
) -> str:
    has_sale_signal = any(key in signals for key in ["film_sales", "film_formats", "film_brands"])
    if negative_hits:
        return "needs_review"
    if score >= PROMISING_SCORE and has_sale_signal:
        return "promising"
    if score >= DEFAULT_MIN_SCORE:
        return "needs_review"
    return "weak"


def build_codex_review_prompt(
    draft: dict[str, Any],
    status: str,
    score: int,
    evidence_pages: list[dict[str, Any]],
    negative_hits: list[str],
) -> str:
    evidence_lines = []
    for page in evidence_pages[:4]:
        evidence_lines.append(f"URL: {page['url']}")
        evidence_lines.append(f"Title: {page.get('title') or '(none)'}")
        evidence_lines.append(f"Signals: {', '.join(page.get('signals') or []) or '(none)'}")
        for snippet in page.get("snippets", [])[:5]:
            evidence_lines.append(f"- {snippet}")
    if negative_hits:
        evidence_lines.append("Negative signals:")
        evidence_lines.extend(f"- {hit}" for hit in negative_hits)

    draft_json = json.dumps(draft, ensure_ascii=False, indent=2)
    evidence_text = "\n".join(evidence_lines)
    return (
        "Review this Grainfinder stockist candidate. Decide whether the evidence "
        "confirms a physical shop that sells photographic film. If valid, return a "
        "clean data/stockists.json entry with exact address and coordinates; if not, "
        "reject it with a short reason.\n\n"
        f"Status: {status}\nScore: {score}\n\nDraft:\n{draft_json}\n\nEvidence:\n{evidence_text}"
    )


def find_possible_duplicates(
    draft: dict[str, Any],
    existing_stockists: list[dict[str, Any]],
) -> list[dict[str, str]]:
    draft_host = canonical_host(str(draft.get("website") or draft.get("sourceUrl") or ""))
    draft_name = normalize_name(str(draft.get("name") or ""))
    draft_city = normalize_name(str(draft.get("city") or ""))
    duplicates = []
    for stockist in existing_stockists:
        existing_hosts = {
            canonical_host(str(stockist.get("website") or "")),
            canonical_host(str(stockist.get("sourceUrl") or "")),
        }
        existing_name = normalize_name(str(stockist.get("name") or ""))
        existing_city = normalize_name(str(stockist.get("city") or ""))
        reasons = []
        if draft_host and draft_host in existing_hosts:
            reasons.append("same domain")
        if draft_name and draft_name == existing_name:
            reasons.append("same name")
        if draft_name and draft_name == existing_name and draft_city and draft_city == existing_city:
            reasons.append("same city")
        if reasons:
            duplicates.append(
                {
                    "id": str(stockist.get("id") or ""),
                    "name": str(stockist.get("name") or ""),
                    "city": str(stockist.get("city") or ""),
                    "reason": ", ".join(ordered_unique(reasons)),
                }
            )
    return duplicates[:8]


def load_existing_stockists(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"could not parse {path}: {error}", file=sys.stderr)
        return []
    return data if isinstance(data, list) else []


def write_output(output: dict[str, Any], output_path: str) -> None:
    text = json.dumps(output, ensure_ascii=False, indent=2)
    if output_path == "-":
        print(text)
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def normalize_manual_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    if not re.match(r"https?://", url, re.I):
        url = f"https://{url}"
    return normalize_candidate_url(url)


def normalize_candidate_url(url: str) -> str:
    url = html.unescape(url.strip())
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""
    parsed = parsed._replace(fragment="")
    return urlunparse(parsed)


def canonical_url_key(url: str) -> str:
    url = normalize_candidate_url(url)
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)
    filtered_query = {
        key: value
        for key, value in query.items()
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    }
    encoded_query = urlencode(filtered_query, doseq=True)
    return urlunparse(("https", canonical_host(url), path, "", encoded_query, ""))


def canonical_host(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def display_host(url: str) -> str:
    return canonical_host(url) or url


def same_site(root_url: str, candidate_url: str) -> bool:
    return canonical_host(root_url) == canonical_host(candidate_url)


def site_root(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


def clean_text(value: str) -> str:
    return WHITESPACE_RE.sub(" ", html.unescape(value or "")).strip()


def clean_phone(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"\s+", " ", value)
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8 or len(digits) > 18:
        return ""
    return value


def clean_business_name(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    for separator in [" | ", " - ", " – "]:
        if separator in value:
            value = value.split(separator)[0]
    return value.strip()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "stockist"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def ordered_unique(values: Any) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = value if isinstance(value, (int, float)) else str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def first_non_empty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if value not in ("", None) and target.get(key) in ("", None):
            target[key] = value


def parse_float(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def infer_city_country(location: str) -> tuple[str, str]:
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return "", parts[0] if parts else ""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
