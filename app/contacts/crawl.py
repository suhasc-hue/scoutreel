"""Polite crawler for linktree/website contact pages.

Hard rules (spec §6.3): respect robots.txt, 1 request/sec/domain, custom
User-Agent including the operator's contact email, 10s timeout, max 5
pages/domain, never submit forms (GET only).

Link hubs (linktr.ee, beacons.ai, ...) are JS-rendered, so emails/links live
in embedded JSON rather than visible text — we therefore also scan the raw
HTML and follow a few outbound links harvested from hub pages.
"""
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.config import get_settings
from app.contacts.extract import (
    LINK_HUB_DOMAINS,
    URL_RE,
    ExtractedEmail,
    extract_emails,
    is_junk_email,
)

MAX_PAGES_PER_DOMAIN = 5
PER_DOMAIN_DELAY_S = 1.0
TIMEOUT_S = 10.0
CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/about-us"]
MAX_HARVESTED_LINKS = 3  # outbound links followed from a hub page

# Domains never worth crawling for a contact page.
SKIP_DOMAINS = (
    "youtube.com", "youtu.be", "instagram.com", "facebook.com", "twitter.com",
    "x.com", "tiktok.com", "spotify.com", "apple.com", "google.com",
    "twitch.tv", "discord.gg", "discord.com", "patreon.com", "imdb.com",
)


@dataclass(frozen=True)
class CrawledContact:
    email: str
    confidence: str
    source_url: str
    context: str


def is_hub_url(url: str) -> bool:
    return any(d in url.lower() for d in LINK_HUB_DOMAINS)


class PoliteCrawler:
    def __init__(self, contact_email: str | None = None, transport: httpx.BaseTransport | None = None):
        contact_email = contact_email or get_settings().crawler_contact_email
        self.user_agent = f"ScoutReelBot/1.0 (contact: {contact_email})"
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=TIMEOUT_S,
            follow_redirects=True,
            transport=transport,
        )
        self._last_request: dict[str, float] = {}
        self._pages_fetched: dict[str, int] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    # ---- politeness machinery ----

    def _robots_for(self, domain: str, scheme: str) -> urllib.robotparser.RobotFileParser:
        if domain not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(domain)  # robots.txt fetch is throttled too
                resp = self.client.get(f"{scheme}://{domain}/robots.txt")
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.parse([])  # no robots.txt -> everything allowed
            except Exception:
                rp.parse([])
            self._robots[domain] = rp
        return self._robots[domain]

    def _can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if self._pages_fetched.get(domain, 0) >= MAX_PAGES_PER_DOMAIN:
            return False
        rp = self._robots_for(domain, parsed.scheme or "https")
        return rp.can_fetch(self.user_agent, url)

    def _throttle(self, domain: str) -> None:
        last = self._last_request.get(domain)
        if last is not None:
            wait = PER_DOMAIN_DELAY_S - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[domain] = time.monotonic()

    def fetch(self, url: str) -> str | None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        domain = parsed.netloc.lower()
        if any(domain == d or domain.endswith("." + d) for d in SKIP_DOMAINS):
            return None
        if not self._can_fetch(url):
            logger.debug("skipping (robots.txt or page cap): {}", url)
            return None
        self._throttle(domain)
        self._pages_fetched[domain] = self._pages_fetched.get(domain, 0) + 1
        try:
            resp = self.client.get(url)
            if resp.status_code != 200:
                return None
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return None
            # The redirect target's domain pays the page-cap cost too.
            final_domain = resp.url.host.lower() if resp.url.host else domain
            if final_domain != domain:
                self._pages_fetched[final_domain] = self._pages_fetched.get(final_domain, 0) + 1
            return resp.text
        except Exception as exc:
            logger.debug("fetch failed {}: {}", url, exc)
            return None

    # ---- contact discovery ----

    def find_contacts(self, start_urls: list[str]) -> list[CrawledContact]:
        """Visit each bio link plus obvious /contact, /about paths. mailto:
        links count as listed_business (explicitly published for contact).
        Hub pages contribute a few outbound links to follow."""
        found: dict[str, CrawledContact] = {}
        harvested: list[str] = []
        for start in start_urls:
            queue = [start]
            parsed = urlparse(start)
            if (
                parsed.scheme in ("http", "https")
                and parsed.netloc
                and not is_hub_url(start)
            ):
                base = f"{parsed.scheme}://{parsed.netloc}"
                for path in CONTACT_PATHS:
                    candidate = urljoin(base, path)
                    if candidate not in queue:
                        queue.append(candidate)
            for url in queue:
                html = self.fetch(url)
                if not html:
                    continue
                self._merge(found, self._extract_from_html(html, url))
                if is_hub_url(url):
                    harvested.extend(self._harvest_links(html, start_urls))

        for url in list(dict.fromkeys(harvested))[:MAX_HARVESTED_LINKS]:
            html = self.fetch(url)
            if html:
                self._merge(found, self._extract_from_html(html, url))
        return list(found.values())

    @staticmethod
    def _merge(found: dict[str, CrawledContact], new: list[CrawledContact]) -> None:
        for contact in new:
            prev = found.get(contact.email)
            if prev is None or (
                prev.confidence == "inferred" and contact.confidence == "listed_business"
            ):
                found[contact.email] = contact

    @staticmethod
    def _harvest_links(html: str, already: list[str]) -> list[str]:
        """Outbound links from a hub page's raw HTML (incl. embedded JSON)."""
        links: list[str] = []
        for m in URL_RE.finditer(html):
            url = m.group(0).rstrip(".,;:!?\\\"'").replace("\\u002F", "/").replace("\\/", "/")
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
            if not domain or url in already:
                continue
            if any(domain == d or domain.endswith("." + d) for d in SKIP_DOMAINS):
                continue
            if is_hub_url(url):  # don't hop hub-to-hub
                continue
            if any(url.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".svg", ".css", ".js", ".woff", ".woff2", ".ico")):
                continue
            if url not in links:
                links.append(url)
        return links

    @staticmethod
    def _extract_from_html(html: str, url: str) -> list[CrawledContact]:
        out: list[CrawledContact] = []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return out
        # mailto: links are explicit contact listings.
        for a in soup.select('a[href^="mailto:"]'):
            email = a["href"].split("mailto:", 1)[1].split("?")[0].strip().lower()
            if email and not is_junk_email(email):
                out.append(
                    CrawledContact(
                        email=email,
                        confidence="listed_business",
                        source_url=url,
                        context=f"mailto link: {a.get_text(strip=True)[:80]}",
                    )
                )
        # Visible text, classified by surrounding context.
        text = soup.get_text(" ", strip=True)
        extracted: list[ExtractedEmail] = extract_emails(text)
        # JS-rendered pages (Linktree & co) keep emails in embedded JSON that
        # never shows up in visible text — scan the raw HTML as a fallback.
        seen = {e.email for e in extracted}
        for e in extract_emails(html):
            if e.email not in seen:
                extracted.append(e)
                seen.add(e.email)
        for e in extracted:
            out.append(
                CrawledContact(
                    email=e.email,
                    confidence=e.confidence,
                    source_url=url,
                    context=e.context,
                )
            )
        return out

    def close(self) -> None:
        self.client.close()
