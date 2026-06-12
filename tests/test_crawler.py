"""PoliteCrawler tests using httpx.MockTransport — no real network."""
import httpx
import pytest

from app.contacts.crawl import MAX_PAGES_PER_DOMAIN, PoliteCrawler


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    """Throttling is verified separately; don't actually sleep in tests."""
    monkeypatch.setattr("app.contacts.crawl.time.sleep", lambda s: None)


def make_crawler(routes: dict[str, httpx.Response], requests_log: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(str(request.url))
        return routes.get(
            str(request.url), httpx.Response(404, text="not found")
        )

    return PoliteCrawler(
        contact_email="tester@example.org", transport=httpx.MockTransport(handler)
    )


def html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


def test_mailto_is_listed_business():
    log: list[str] = []
    crawler = make_crawler(
        {
            "https://site.test/robots.txt": httpx.Response(404),
            "https://site.test/": html('<a href="mailto:films@site.test">email me</a>'),
        },
        log,
    )
    contacts = crawler.find_contacts(["https://site.test/"])
    assert any(c.email == "films@site.test" and c.confidence == "listed_business" for c in contacts)


def test_visible_text_email_with_business_context():
    log: list[str] = []
    crawler = make_crawler(
        {
            "https://site.test/robots.txt": httpx.Response(404),
            "https://site.test/": html("<p>For business inquiries: biz@site.test</p>"),
        },
        log,
    )
    contacts = crawler.find_contacts(["https://site.test/"])
    found = {c.email: c for c in contacts}
    assert found["biz@site.test"].confidence == "listed_business"


def test_robots_txt_disallow_respected():
    log: list[str] = []
    crawler = make_crawler(
        {
            "https://blocked.test/robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /", headers={"content-type": "text/plain"}
            ),
            "https://blocked.test/": html("secret@blocked.test business contact"),
        },
        log,
    )
    contacts = crawler.find_contacts(["https://blocked.test/"])
    assert contacts == []
    # only robots.txt was fetched, never the page
    assert all(u.endswith("robots.txt") for u in log)


def test_page_cap_per_domain():
    log: list[str] = []
    routes = {"https://big.test/robots.txt": httpx.Response(404)}
    for path in ["", "contact", "contact-us", "about", "about-us", "extra1", "extra2"]:
        routes[f"https://big.test/{path}"] = html("<p>nothing here</p>")
    crawler = make_crawler(routes, log)
    crawler.find_contacts(["https://big.test/", "https://big.test/extra1", "https://big.test/extra2"])
    page_requests = [u for u in log if not u.endswith("robots.txt")]
    assert len(page_requests) <= MAX_PAGES_PER_DOMAIN


def test_linktree_json_email_and_link_harvesting():
    """Hub pages keep data in embedded JSON — emails there must be found, and
    outbound links followed."""
    log: list[str] = []
    crawler = make_crawler(
        {
            "https://linktr.ee/robots.txt": httpx.Response(404),
            "https://linktr.ee/director": html(
                '<script id="__NEXT_DATA__">{"links":[{"url":"https://mysite.test"}],'
                '"email":"hidden@director.test"}</script>'
            ),
            "https://mysite.test/robots.txt": httpx.Response(404),
            "https://mysite.test": html("<p>work with me: work@director.test</p>"),
            "https://mysite.test/": html("<p>work with me: work@director.test</p>"),
        },
        log,
    )
    contacts = crawler.find_contacts(["https://linktr.ee/director"])
    emails = {c.email for c in contacts}
    assert "hidden@director.test" in emails  # from raw HTML/JSON
    assert "work@director.test" in emails  # via harvested outbound link


def test_never_posts():
    """The crawler must only ever GET."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return html('<form action="/subscribe" method="post"></form>')

    crawler = PoliteCrawler(
        contact_email="t@example.org", transport=httpx.MockTransport(handler)
    )
    crawler.find_contacts(["https://forms.test/"])
    assert set(methods) == {"GET"}
