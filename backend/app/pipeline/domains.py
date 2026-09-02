"""Finding a company's website when no source publishes one.

Licence registers carry no URL field, and OpenStreetMap knew one business in
3,846. That left `digital_gap` and `health` — 24% of the default thesis —
measured on 3% of rows, which is a hard ceiling on how much the scorer can say.

So candidates are derived from the business name and then **proved or
discarded**. That distinction is the whole module. Guessing that
`hickeyplumbing.com` belongs to Hickey Plumbing and writing it into the record
would be exactly the fabrication this product is pitched against — a user
clicking through to check the evidence would find a stranger's business, and
every other claim we make becomes suspect.

Proof, in order of strength:

* **Phone.** The licence phone number, stripped to ten digits, appearing
  anywhere in the page's digits. Two unrelated businesses do not share a phone
  number, so this is conclusive.
* **Name.** Every distinctive token of the business name — trade words like
  "plumbing" and legal suffixes removed, because those match half the internet —
  appearing in the page text. Suggestive rather than conclusive, and recorded as
  the weaker evidence it is.

Anything that resolves but proves neither is discarded. So is anything that
looks parked. The verification method is stored on the company, so the UI can
say how the link was established rather than presenting all websites as equally
certain.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from selectolax.parser import HTMLParser

from app.pipeline.scrapers.http import PoliteClient
from app.schemas import Company

logger = logging.getLogger(__name__)

# Words that appear in thousands of trade names and prove nothing about
# identity. A page matching only these is not evidence of anything.
_GENERIC_TOKENS = frozenset(
    {
        "plumbing", "plumber", "electric", "electrical", "electrician",
        "heating", "cooling", "air", "conditioning", "hvac", "refrigeration",
        "mechanical", "services", "service", "systems", "system", "solutions",
        "company", "co", "inc", "incorporated", "llc", "corp", "corporation",
        "contractor", "contractors", "construction", "the", "and", "of", "a",
        "son", "sons", "brothers", "bros", "group", "enterprises", "enterprise",
        "industries", "professional", "quality", "best", "affordable",
    }
)

# Phrases that mean a domain resolves but nobody is home. Registrar parking is
# the most common false positive: the domain exists, returns 200, and belongs to
# nobody in particular.
_PARKED_MARKERS = (
    "this domain is for sale",
    "buy this domain",
    "domain is parked",
    "parked free",
    "godaddy.com/domainsearch",
    "future home of something",
    "under construction",
    "coming soon",
    "default web site page",
    "sedoparking",
    "hugedomains",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"\D+")

MAX_CANDIDATES = 4
MIN_CONTENT_BYTES = 600


@dataclass(frozen=True)
class DomainMatch:
    url: str
    method: str  # "phone" or "name"
    detail: str


def _tokens(name: str) -> list[str]:
    # Apostrophes are removed rather than split on, so "Johnny's" yields
    # "johnnys" — which is what the domain would be — instead of a stray "s"
    # that turns into "johnny-s-air-conditioning".
    cleaned = name.lower().replace("'", "").replace("’", "")  # noqa: RUF001
    return [t for t in _NON_ALNUM.sub(" ", cleaned).split() if t]


def distinctive_tokens(name: str) -> list[str]:
    """The parts of a name that actually identify this business.

    "Hickey Plumbing" is distinguished by "hickey"; "plumbing" is shared with
    every competitor. A name with no distinctive token — "Quality Plumbing
    Services" — cannot be verified by name at all, and this returning empty is
    how the caller knows that.
    """
    return [t for t in _tokens(name) if t not in _GENERIC_TOKENS and len(t) > 2]


# Trade words a contractor is likely to keep in a domain, and the short form
# they usually keep it as. "Kaprelian Electrical Contractor" becomes
# kaprelianelectric.com far more often than kaprelianelectricalcontractor.com.
_TRADE_STEM: dict[str, str] = {
    "plumbing": "plumbing",
    "plumber": "plumbing",
    "electrical": "electric",
    "electric": "electric",
    "electrician": "electric",
    "heating": "heating",
    "cooling": "cooling",
    "conditioning": "air",
    "air": "air",
    "hvac": "hvac",
    "refrigeration": "refrigeration",
    "mechanical": "mechanical",
    "construction": "construction",
}

# Words nobody carries into a domain.
# "a" is deliberately absent: in a trade name it is far more often an initial
# than an article, and dropping it turned "A K International" into
# "kinternational.com" — a domain for a company that does not exist.
_DROP_FROM_DOMAIN = frozenset(
    {"inc", "llc", "co", "corp", "corporation", "incorporated", "the", "and", "of"}
)

MAX_STEM_LENGTH = 28


def candidate_domains(name: str) -> list[str]:
    """Plausible domains for a business name, most likely first.

    Deliberately few, and shaped like domains people actually register. The
    naive "join every word" produces `gabekaprelianelectricalcontractor.com`,
    which nobody owns, so the real business is missed while a pointless request
    is made. Contractors shorten: the distinctive part of the name plus a short
    trade word, or the distinctive part alone.

    Each candidate is a request to a stranger's server, so the list is capped.
    The hit rate on a fifth guess does not justify the traffic, and every extra
    candidate is another chance to land on an unrelated business that happens to
    pass verification.
    """
    tokens = [t for t in _tokens(name) if t not in _DROP_FROM_DOMAIN]
    if not tokens:
        return []

    distinctive = [t for t in tokens if t not in _GENERIC_TOKENS and len(t) > 2]
    trade = next((_TRADE_STEM[t] for t in tokens if t in _TRADE_STEM), None)

    stems: list[str] = []

    def add(stem: str) -> None:
        if 4 <= len(stem) <= MAX_STEM_LENGTH and stem not in stems:
            stems.append(stem)

    # Whole name first — right for the short two-word names that dominate.
    add("".join(tokens))
    # Distinctive part plus the trade, which is how longer names get shortened.
    if distinctive and trade:
        add("".join(distinctive) + trade)
    # Distinctive part alone, for names that are simply a surname.
    if distinctive:
        add("".join(distinctive))

    candidates: list[str] = []
    for index, stem in enumerate(stems):
        candidates.append(f"https://{stem}.com")
        # .net only for the primary spelling; it is a distant second in practice
        # and not worth a request on every variant.
        if index == 0:
            candidates.append(f"https://{stem}.net")
    return candidates[:MAX_CANDIDATES]


def _looks_parked(text: str, byte_count: int) -> bool:
    if byte_count < MIN_CONTENT_BYTES:
        return True
    low = text[:4000].lower()
    return any(marker in low for marker in _PARKED_MARKERS)


def verify(company: Company, url: str, html: str) -> DomainMatch | None:
    """Decide whether this page belongs to this company. None means unproven."""
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.body or tree
    text = body.text(separator=" ", strip=True) if body else ""

    # Markup *after* the scripts have been removed. Both are needed: the visible
    # text misses `href="tel:+16267153902"`, which is often the only place a
    # number appears, while the raw source would let a tracking snippet or a
    # JSON blob that happens to contain the digits count as proof. A script is
    # not the business publishing its phone number.
    cleaned_markup = tree.html or ""

    if _looks_parked(text, len(html)):
        return None

    # Phone: conclusive. Strip both sides to digits so every formatting of the
    # same number matches — (818) 555-0142, 818.555.0142, 8185550142.
    phone = next((c.phone for c in company.contacts if c.phone), None)
    if phone:
        wanted = _DIGITS.sub("", phone)[-10:]
        if len(wanted) == 10 and wanted in _DIGITS.sub("", f"{text} {cleaned_markup}"):
            return DomainMatch(
                url=url,
                method="phone",
                detail=(
                    f"The licensed phone number {phone} appears on this page, which "
                    "is conclusive — two unrelated businesses do not share one."
                ),
            )

    # Name: suggestive, and only ever accepted alongside the city.
    #
    # Every distinctive token must appear, and so must the licensed city. The
    # name test alone is not safe: "A K International" reduces to the single
    # token "international", which appears on international.com — a real site
    # belonging to somebody else entirely, which would have passed. Requiring
    # the city closes that, because a national domain does not advertise Van
    # Nuys and a local contractor always names its service area.
    tokens = distinctive_tokens(company.name)
    city = (company.city or "").strip().lower()
    if tokens and city:
        haystack = text.lower()
        if all(token in haystack for token in tokens) and city in haystack:
            return DomainMatch(
                url=url,
                method="name",
                detail=(
                    f"Page names {', '.join(tokens)} from the licensed business "
                    f"name and serves {company.city}. Suggestive rather than "
                    "conclusive — the phone number on file does not appear."
                ),
            )

    return None


async def find_website(client: PoliteClient, company: Company) -> DomainMatch | None:
    """Try each candidate in turn, returning the first that proves itself."""
    for url in candidate_domains(company.name):
        result = await client.get_or_none(url)
        if result is None or not result.ok or not result.text:
            continue
        match = verify(company, result.url, result.text)
        if match is not None:
            return match
    return None


async def infer_websites(
    client: PoliteClient, companies: list[Company], *, concurrency: int = 8
) -> dict[str, int]:
    """Fill in `website` for companies that have none. Returns a tally."""
    targets = [c for c in companies if not c.website]
    if not targets:
        return {}

    semaphore = asyncio.Semaphore(concurrency)
    tally: dict[str, int] = {"phone": 0, "name": 0, "unproven": 0}
    done = 0

    async def one(company: Company) -> None:
        nonlocal done
        async with semaphore:
            match = await find_website(client, company)
        if match is None:
            tally["unproven"] += 1
        else:
            company.website = match.url
            company.website_source = f"inferred:{match.method}"
            company.website_evidence = match.detail
            tally[match.method] += 1
        done += 1
        if done % 50 == 0:
            logger.info("checked %d/%d companies", done, len(targets))

    await asyncio.gather(*(one(c) for c in targets))
    logger.info(
        "domain inference: %d proved by phone, %d by name, %d unproven of %d",
        tally["phone"],
        tally["name"],
        tally["unproven"],
        len(targets),
    )
    return tally


__all__ = [
    "DomainMatch",
    "candidate_domains",
    "distinctive_tokens",
    "find_website",
    "infer_websites",
    "verify",
]
