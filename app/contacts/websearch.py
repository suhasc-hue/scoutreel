"""Web-search step of the contact pipeline (feature-flagged).

Priority order for finding a filmmaker's public business contact:
  1. channel description          (free, always on)
  2. video descriptions           (free, always on)
  3. bio links via polite crawler (free, always on)
  4. web search -> crawl results  (requires BRAVE_API_KEY; off otherwise)

The search step queries the Brave Search API (an official, paid-for API — no
search-engine scraping) and then feeds promising result URLs to the same
polite, robots.txt-respecting crawler. Domains whose terms prohibit scraping
(LinkedIn, IMDb, Instagram, Facebook, X/Twitter) are never crawled — results
from them are surfaced to the user as leads instead.
"""
import httpx
from loguru import logger

from app.config import get_settings
from app.contacts.crawl import SKIP_DOMAINS, CrawledContact, PoliteCrawler

# Never crawled — shown to the user as manual leads only.
LEAD_ONLY_DOMAINS = ("linkedin.com", "imdb.com", "instagram.com",
                     "facebook.com", "x.com", "twitter.com")

MAX_RESULTS_CRAWLED = 4


def search_web(query: str, count: int = 8) -> list[dict]:
    """Brave Search API. Returns [] when no key is configured."""
    settings = get_settings()
    if not settings.brave_api_key:
        return []
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": settings.brave_api_key,
                     "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [{"title": r.get("title", ""), "url": r.get("url", "")} for r in results]
    except Exception as exc:  # noqa: BLE001
        logger.warning("web search failed for {!r}: {}", query, exc)
        return []


def _crawlable(url: str) -> bool:
    low = url.lower()
    if any(d in low for d in LEAD_ONLY_DOMAINS):
        return False
    return not any(d in low for d in SKIP_DOMAINS)


def find_contacts_via_search(
    channel_name: str, film_title: str, director: str | None = None
) -> tuple[list[CrawledContact], list[dict]]:
    """Returns (crawled contacts, lead-only results e.g. IMDb/LinkedIn pages).
    No-op without a search API key."""
    queries = [f'"{channel_name}" filmmaker contact email']
    if director:
        queries.insert(0, f'"{director}" "{film_title}" filmmaker contact')
    crawl_urls: list[str] = []
    leads: list[dict] = []
    for q in queries:
        for r in search_web(q):
            url = r["url"]
            if not url:
                continue
            if any(d in url.lower() for d in LEAD_ONLY_DOMAINS):
                if r not in leads:
                    leads.append(r)
            elif _crawlable(url) and url not in crawl_urls:
                crawl_urls.append(url)
        if crawl_urls:
            break  # first query that yields crawlable results wins

    contacts: list[CrawledContact] = []
    if crawl_urls:
        crawler = PoliteCrawler()
        try:
            contacts = crawler.find_contacts(crawl_urls[:MAX_RESULTS_CRAWLED])
        finally:
            crawler.close()
    return contacts, leads[:5]
