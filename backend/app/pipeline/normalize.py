"""Normalisation: the step that makes everything downstream comparable.

Dedupe, joins and grouping all assume two records describing one business look
alike. Raw sources guarantee the opposite — the same company arrives as
"Whitaker Heating & Cooling, Inc.", "WHITAKER HEATING AND COOLING" and
"whitaker heating + cooling llc", on `www.WhitakerHVAC.com/` and
`https://whitakerhvac.com`. Fix it once, here, rather than in every comparison.

Nothing here is lossy in the record: normalisation produces *additional* keys for
matching. The original strings stay exactly as the source gave them, because
those are what the UI shows and what a user would check against.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Legal suffixes and joining words. Two businesses are not different because one
# spells itself "Inc." and the other does not.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "llp", "ltd", "limited", "corp",
    "corporation", "co", "company", "pllc", "pc", "pa", "plc", "gmbh", "sa",
    "dds", "dmd", "dvm", "md", "cpa",
}
_NOISE_WORDS = {"the", "and", "of", "a", "an"}

_PUNCT = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")

# Multi-label public suffixes we actually meet in US small-business data. A full
# PSL would be better, but pulling one in for a handful of cases is not worth the
# dependency — and getting this wrong only costs a slightly weaker blocking key.
_COMPOUND_TLDS = {"co.uk", "com.au", "co.nz", "org.uk", "ac.uk", "com.br"}


def normalise_domain(value: str | None) -> str | None:
    """Bare, lowercase, no scheme, no `www.`, no port, no trailing dot.

    Works on either a URL or a bare domain, because sources supply both and the
    caller should not have to care which it has.
    """
    if not value:
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    netloc = urlparse(raw if "://" in raw else f"//{raw}").netloc or raw
    netloc = netloc.split("@")[-1].split(":")[0].strip("./")
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def registrable_domain(value: str | None) -> str | None:
    """The part that identifies the owner — `whitakerhvac.com` from
    `shop.whitakerhvac.com`.

    Subdomains routinely differ between two records for one business (a site on
    `www`, a booking page on `book.`), so the registrable domain is the honest
    identity key.
    """
    domain = normalise_domain(value)
    if not domain:
        return None
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    if ".".join(parts[-2:]) in _COMPOUND_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalise_name(name: str) -> str:
    """Company name reduced to its comparable core.

    `&` becomes `and` before punctuation is stripped, so "Heating & Cooling" and
    "Heating and Cooling" converge instead of becoming "heating cooling" and
    "heating and cooling".
    """
    lowered = name.lower().replace("&", " and ").replace("+", " and ")
    stripped = _PUNCT.sub(" ", lowered)
    tokens = [
        token
        for token in _WHITESPACE.sub(" ", stripped).strip().split()
        if token not in _LEGAL_SUFFIXES and token not in _NOISE_WORDS
    ]
    # Everything was a suffix ("The Company Inc") — keep something rather than
    # returning an empty key that would collide with every other empty key.
    return " ".join(tokens) if tokens else _WHITESPACE.sub(" ", stripped).strip()


def name_blocking_key(name: str) -> str:
    """Cheap bucket key for the dedupe pass.

    Blocking is what keeps dedupe linear-ish: only records sharing a key are
    compared, so the expensive fuzzy match runs over small groups instead of the
    whole n². The first four characters of the first meaningful token is crude
    but it is exactly the part that rarely varies between two spellings of one
    business.
    """
    normalised = normalise_name(name)
    first = normalised.split()[0] if normalised else ""
    return first[:4]


def normalise_postcode(value: str | None) -> str | None:
    """US ZIP, trimmed to the 5-digit form. ZIP+4 varies by record for one site."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits[:5] if len(digits) >= 5 else None


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return normalise_domain(email.rsplit("@", 1)[1])


__all__ = [
    "email_domain",
    "name_blocking_key",
    "normalise_domain",
    "normalise_name",
    "normalise_postcode",
    "registrable_domain",
]
