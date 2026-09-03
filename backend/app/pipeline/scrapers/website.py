"""Website signal extraction.

Turns a company's own site into the `WebSignals` the scoring engine reads. Every
signal here is something an operator could verify by opening the page — no
inference, no model output, no guessing at numbers the page does not state. The
digital-gap and health factors are only defensible if what they read is
observable.

Two passes: the homepage always, then at most a couple of linked about/contact
pages, because that is where owner names and real mailboxes live and the
homepage rarely has either. The cap is deliberate — an unbounded crawl of a
stranger's site to fill in a lead record is not proportionate.

Truncation is handled carefully. `raw_text_excerpt` is capped for storage, but
any sentence matching an ownership or PE pattern is *also* copied into
`owner_mentions` / `pe_backed_mentions`, so a signal can never be lost merely
because it appeared below the cut. The scoring corpus reads all three.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from app.pipeline.scrapers.http import PoliteClient
from app.schemas import Contact, VerificationStatus, WebSignals

logger = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 6_000
MAX_SECONDARY_PAGES = 2

# Analytics and tag managers. Presence means the owner can see their own demand;
# absence is the single cheapest post-acquisition fix there is.
_ANALYTICS_MARKERS = (
    "google-analytics.com", "googletagmanager.com", "gtag(", "ga('create'",
    "plausible.io", "fathom", "matomo", "piwik", "hotjar", "segment.com",
    "clarity.ms", "connect.facebook.net", "mixpanel", "heap.io",
)

# Site builders and frameworks, mapped onto the vocabulary the digital-gap
# factor already understands (see _MODERN_STACK / _LEGACY_STACK in factors.py).
_TECH_MARKERS: tuple[tuple[str, str], ...] = (
    ("/_next/", "next.js"),
    ("__next", "next.js"),  # the mount point, present even when assets are on a CDN
    ("wp-content", "wordpress"),
    ("wix.com", "wix"),
    ("squarespace", "squarespace"),
    ("shopify", "shopify"),
    ("webflow", "webflow"),
    ("gatsby", "gatsby"),
    ("nuxt", "nuxt"),
    ("react", "react"),
    ("frontpage", "frontpage"),
    ("dreamweaver", "dreamweaver"),
    ("joomla", "joomla"),
    ("godaddy", "godaddy website builder"),
    (".swf", "flash"),
)

_JQUERY_VERSION = re.compile(r"jquery[-./](\d+)\.\d+", re.I)

_CAREERS_HINT = re.compile(r"\b(careers?|jobs?|employment|join[- ]our[- ]team|we'?re hiring)\b", re.I)
_TEAM_HINT = re.compile(r"\b(our[- ]team|meet[- ]the[- ]team|staff|our[- ]people|leadership)\b", re.I)
_ABOUT_HINT = re.compile(r"\b(about|our[- ]story|who[- ]we[- ]are|history)\b", re.I)
_CONTACT_HINT = re.compile(r"\bcontact\b", re.I)

_COPYRIGHT_YEAR = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,20}(\d{4})", re.I)
_ANY_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_FOUNDED_YEAR = re.compile(
    r"\b(?:founded|established|est\.?|serving\s.{0,40}?since|since)\s*(?:in\s*)?(\d{4})\b", re.I
)

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")

# A company or personal LinkedIn profile linked from the site. Worth capturing
# because it is a second outreach channel the contactability factor scores, and
# because it is the one identifier a searcher can use to check who they are
# actually dealing with before making contact.
#
# `/company/` and `/in/` only: share widgets, post permalinks and the bare
# linkedin.com homepage in a footer nav are not profiles and would put a
# useless link in front of the user.
_LINKEDIN = re.compile(
    r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9\-_%.]{2,80}",
    re.I,
)

# Sentences worth keeping verbatim, because the scoring engine pattern-matches
# them and the UI quotes them back to the user as evidence.
_OWNERSHIP_SENTENCE = re.compile(
    r"\b(family[- ](?:owned|run|operated)|owner[- ](?:operated|operator)|independently owned"
    r"|(?:2nd|3rd|second|third)[- ]generation|our founder|founded|established|since \d{4})\b",
    re.I,
)
_PE_SENTENCE = re.compile(
    r"\b((?:portfolio|platform) company of|backed by|a (?:subsidiary|division) of"
    r"|nasdaq\s*:|nyse\s*:)\b",
    re.I,
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Decision makers, as they are actually written on an about page. Two spellings
# cover almost everything: "Dale Whitaker, Owner" and "Owner: Dale Whitaker".
# Reaching the person who can actually sell the business is worth 30 points in
# the contactability factor, and it is the difference between a lead and a name.
_TITLE_WORDS = (
    r"Owner|Co-?Owner|Founder|Co-?Founder|President|CEO|Principal|Proprietor"
    r"|General Manager|Managing (?:Director|Partner)|Practice Manager"
)
# The non-ASCII characters below are load-bearing, not typos. Real about pages
# are written in a CMS that curls apostrophes and turns hyphens into dashes, so
# matching only the ASCII forms would miss "O'Brien" and "Dale Whitaker - Owner"
# in their real, typeset spellings — exactly the strings this exists to catch.
#
# The surname class allows an interior capital so that O'Brien, McCarthy and
# Smith-Jones survive; restricting it to lowercase silently drops every name
# with one, which is a lot of them.
_NAME = r"(?:Dr\.?\s+|Mr\.?\s+|Mrs\.?\s+|Ms\.?\s+)?[A-Z][a-z]{1,20}(?:\s+[A-Z]\.)?\s+[A-Z][A-Za-z'’-]{1,24}"  # noqa: RUF001

_PERSON_THEN_TITLE = re.compile(rf"\b({_NAME})\s*[,\-–—]\s*({_TITLE_WORDS})\b")  # noqa: RUF001
_TITLE_THEN_PERSON = re.compile(rf"\b({_TITLE_WORDS})\s*[:\-–—]\s*({_NAME})\b")  # noqa: RUF001

# Words that pass the "two capitalised tokens" shape but are not people. Without
# this, "Columbus Ohio, Owner" and "Contact Us, Owner" become decision makers.
_NOT_A_NAME = re.compile(
    r"\b(Columbus|Ohio|Contact|About|Home|Services|Team|Our|The|Us|Call|Email|Phone"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Company|Business"
    r"|Center|Centre|Group|Inc|LLC|Suite|Street|Avenue|Road)\b",
    re.I,
)


def _extract_decision_maker(text: str) -> tuple[str, str] | None:
    """Find a named owner or principal. Returns `(name, title)` or None."""
    for pattern, name_first in ((_PERSON_THEN_TITLE, True), (_TITLE_THEN_PERSON, False)):
        for match in pattern.finditer(text):
            name = (match.group(1) if name_first else match.group(2)).strip()
            title = (match.group(2) if name_first else match.group(1)).strip()
            if _NOT_A_NAME.search(name):
                continue
            return " ".join(name.split()), title
    return None


def _visible_text(tree: HTMLParser) -> str:
    """Page text with script, style and nav chrome removed.

    Scripts matter: leaving them in means a Google Analytics snippet's inline
    copyright or a JSON blob full of years poisons every year-based signal.
    """
    for tag in ("script", "style", "noscript", "svg", "template"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.body or tree
    text = body.text(separator=" ", strip=True) if body else ""
    return re.sub(r"\s+", " ", text).strip()


def _sentences_matching(text: str, pattern: re.Pattern[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        cleaned = sentence.strip()
        if 15 <= len(cleaned) <= 400 and pattern.search(cleaned):
            out.append(cleaned)
            if len(out) >= limit:
                break
    return out


def _detect_tech(html: str, generator: str | None) -> list[str]:
    haystack = html.lower()
    hints: set[str] = set()
    if generator:
        hints.add(generator.split()[0].lower())
        low = generator.lower()
        for marker, label in _TECH_MARKERS:
            if marker in low:
                hints.add(label)
    for marker, label in _TECH_MARKERS:
        if marker in haystack:
            hints.add(label)
    match = _JQUERY_VERSION.search(html)
    if match and match.group(1) == "1":
        # jQuery 1.x in 2026 means the site has not been touched in a decade.
        hints.add("jquery 1")
    return sorted(hints)


def _year_signals(text: str, html: str, now: datetime) -> tuple[int | None, int | None, int | None]:
    """Returns (copyright_year, latest_content_year, founded_year)."""
    copyright_year: int | None = None
    match = _COPYRIGHT_YEAR.search(html)
    if match:
        year = int(match.group(1))
        if 1990 <= year <= now.year + 1:
            copyright_year = year

    # Years far in the future are template placeholders or phone numbers that
    # happened to parse; years before 1950 are almost always addresses.
    years = [int(y) for y in _ANY_YEAR.findall(text)]
    plausible = [y for y in years if 1950 <= y <= now.year]
    latest = max(plausible) if plausible else None

    founded: int | None = None
    fmatch = _FOUNDED_YEAR.search(text)
    if fmatch:
        year = int(fmatch.group(1))
        if 1800 <= year <= now.year:
            founded = year

    return copyright_year, latest, founded


def _same_host(base: str, candidate: str) -> bool:
    return urlparse(base).netloc.lower() == urlparse(candidate).netloc.lower()


def _pick_secondary_links(tree: HTMLParser, base_url: str) -> list[str]:
    """About and contact pages, in that order of usefulness.

    Owner names live on about pages; real mailboxes live on contact pages. The
    homepage usually has neither.
    """
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for node in tree.css("a[href]"):
        href = node.attributes.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if absolute in seen or not _same_host(base_url, absolute):
            continue
        seen.add(absolute)
        label = f"{node.text(strip=True)} {href}"
        if _ABOUT_HINT.search(label):
            scored.append((0, absolute))
        elif _CONTACT_HINT.search(label):
            scored.append((1, absolute))
    scored.sort(key=lambda pair: pair[0])
    return [url for _, url in scored[:MAX_SECONDARY_PAGES]]


def _extract_contacts(text: str, html: str) -> list[Contact]:
    """Best-effort contact extraction.

    Everything comes back `UNKNOWN`: this pass observes, the validation pass
    decides. Putting a "verified" badge on a regex match would be the exact
    dishonesty the product is pitched against.
    """
    emails = [
        e
        for e in dict.fromkeys(m.group(0).lower() for m in _EMAIL.finditer(html))
        # Asset filenames routinely look like addresses once minified.
        if not e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"))
    ]
    phones = list(dict.fromkeys(m.group(0) for m in _PHONE.finditer(text)))
    person = _extract_decision_maker(text)
    linkedin = next(
        (m.group(0).rstrip("/") for m in _LINKEDIN.finditer(html)),
        None,
    )
    if not emails and not phones and not person and not linkedin:
        return []
    return [
        Contact(
            name=person[0] if person else None,
            title=person[1] if person else None,
            email=emails[0] if emails else None,
            email_status=VerificationStatus.UNKNOWN,
            phone=phones[0] if phones else None,
            phone_valid=None,
            linkedin_url=linkedin,
            is_decision_maker=person is not None,
        )
    ]


async def crawl_site(
    client: PoliteClient,
    website: str,
    *,
    now: datetime | None = None,
    follow_secondary: bool = True,
) -> tuple[WebSignals, list[Contact]]:
    """Crawl one company site and return its signals plus any contacts found.

    Never raises. A site that is down, blocked by robots.txt, or serving
    something unparseable yields empty signals — and empty signals are exactly
    what the scoring engine reports as missing, with low confidence, which is the
    honest answer.
    """
    at = now or datetime.now(UTC)
    result = await client.get_or_none(website)
    if result is None or not result.ok or not result.text:
        return WebSignals(), []

    html = result.text
    tree = HTMLParser(html)
    text = _visible_text(tree)

    generator_node = tree.css_first('meta[name="generator"]')
    generator = generator_node.attributes.get("content") if generator_node else None

    if follow_secondary:
        for link in _pick_secondary_links(tree, result.url):
            extra = await client.get_or_none(link)
            if extra is not None and extra.ok and extra.text:
                extra_tree = HTMLParser(extra.text)
                text = f"{text} {_visible_text(extra_tree)}"
                html = f"{html}\n{extra.text}"

    copyright_year, latest_year, founded_year = _year_signals(text, html, at)
    lower_html = html.lower()

    signals = WebSignals(
        fetched_at=at,
        https=result.url.lower().startswith("https://"),
        mobile_viewport=tree.css_first('meta[name="viewport"]') is not None,
        has_analytics=any(marker in lower_html for marker in _ANALYTICS_MARKERS),
        generator=generator,
        copyright_year=copyright_year,
        latest_content_year=latest_year,
        page_bytes=len(result.text),
        tech_hints=_detect_tech(html, generator),
        has_careers_page=bool(_CAREERS_HINT.search(text)),
        has_team_page=bool(_TEAM_HINT.search(text)),
        founded_year=founded_year,
        owner_mentions=_sentences_matching(text, _OWNERSHIP_SENTENCE),
        pe_backed_mentions=_sentences_matching(text, _PE_SENTENCE),
        raw_text_excerpt=text[:MAX_EXCERPT_CHARS] or None,
    )
    return signals, _extract_contacts(text, html)


__all__ = ["MAX_EXCERPT_CHARS", "crawl_site"]
