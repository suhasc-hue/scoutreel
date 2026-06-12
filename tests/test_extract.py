from app.contacts.extract import extract_emails, extract_links, is_junk_email


def test_finds_business_email_with_context():
    text = "For business inquiries: films@janedoe.com\nSubscribe for more!"
    found = extract_emails(text)
    assert len(found) == 1
    assert found[0].email == "films@janedoe.com"
    assert found[0].confidence == "listed_business"


def test_ambiguous_email_marked_inferred():
    text = "jane@janedoe.com is where I live on the internet"
    found = extract_emails(text)
    assert len(found) == 1
    assert found[0].confidence == "inferred"


def test_trailing_punctuation_stripped():
    found = extract_emails("Contact me at hello@studio.io.")
    assert found[0].email == "hello@studio.io"


def test_noreply_and_placeholder_filtered():
    text = (
        "noreply@youtube.com sent this. Also try you@yourdomain.com "
        "and someone@example.com for testing."
    )
    assert extract_emails(text) == []


def test_junk_helper():
    assert is_junk_email("no-reply@site.com")
    assert is_junk_email("image@2x.png")
    assert not is_junk_email("films@janedoe.com")


def test_dedupes_and_keeps_strongest_confidence():
    text = (
        "jane@doe.com\n... lots of text ...\n"
        "business contact: jane@doe.com"
    )
    found = extract_emails(text)
    assert len(found) == 1
    assert found[0].confidence == "listed_business"


def test_extract_links_hubs_first():
    text = (
        "watch more https://example.com/films and my links "
        "https://linktr.ee/janedoe plus https://imdb.com/name/nm123"
    )
    links = extract_links(text)
    assert links[0] == "https://linktr.ee/janedoe"
    assert "https://example.com/films" in links
    assert len(links) == 3


def test_no_emails_in_empty_text():
    assert extract_emails("") == []
    assert extract_links("") == []
