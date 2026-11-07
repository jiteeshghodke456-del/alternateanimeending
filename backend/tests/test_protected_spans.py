"""Links, emails and phone numbers are not copy: no brand-name fixes or number claims inside them."""
from app.pipeline.tier0 import in_protected, protected_spans


def _covered(text: str, needle: str) -> bool:
    i = text.index(needle)
    return in_protected(i, i + len(needle), protected_spans(text))


def test_urls_domains_emails_phones_are_protected() -> None:
    t = ("Web App: https://brandapp.web.app/ and brandapp.com, mail someone@example.com, "
         "call +1 (555) 010-0199. Article: https://indianexpress.com/article/x-10880313/")
    for needle in ("brandapp", "brandapp.com", "example.com", "+1 (555) 010-0199", "10880313"):
        assert _covered(t, needle), needle


def test_plain_copy_is_not_protected() -> None:
    t = "Over 2,400 users in 72 countries. Try brandapp today."
    assert not _covered(t, "2,400")
    assert not _covered(t, "72")
    assert not _covered(t, "brandapp")
