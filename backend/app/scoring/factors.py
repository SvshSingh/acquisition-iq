"""The six acquisition-fit factors.

Every factor is a pure function of a `Company` (plus thesis parameters). No I/O,
no randomness, no LLM. That is what makes the engine testable with golden files
and defensible to a user who asks "why did this score 82?".

Each factor returns a subscore in 0-100, the evidence behind it, and the signals
it looked for but could not find — which is what drives the confidence flag.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.schemas import (
    FACTOR_LABELS,
    BuyBox,
    Company,
    Confidence,
    Evidence,
    FactorKey,
    FactorResult,
    VerificationStatus,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

# Ownership and tenure are scored as two *groups*, best-match-wins within each,
# rather than as one flat list. A site that says "family-owned, owner-operated,
# third generation" is stating one fact three ways; summing all three triple-counts
# a single signal and pushes the factor past its own ceiling.
_OWNERSHIP_PATTERNS = [
    (re.compile(r"\bfamily[- ]owned\b", re.I), "Describes itself as family-owned", 26.0),
    (re.compile(r"\bfamily[- ](?:run|operated)\b", re.I), "Family-run business", 24.0),
    (re.compile(r"\b(?:2nd|3rd|second|third)[- ]generation\b", re.I), "Multi-generational owner", 22.0),
    (re.compile(r"\bowner[- ](?:operated|operator)\b", re.I), "Owner-operated", 20.0),
    (re.compile(r"\bindependently owned\b", re.I), "Independently owned", 18.0),
    (re.compile(r"\bour founder\b", re.I), "Founder still referenced on site", 10.0),
]

# Tenure claims made in prose. Competes with the age computed from a known
# founding year — we take whichever is stronger, never both.
_TENURE_PATTERNS = [
    (re.compile(r"\bserving .{0,40}since (\d{4})\b", re.I), "Long continuous trading history", 14.0),
    (re.compile(r"\bfounded (?:in )?(\d{4})\b", re.I), "States a founding year", 8.0),
]

_PE_PATTERNS = [
    # Deliberately not anchored on a leading article: real copy reads
    # "a family-owned portfolio company of…" as often as "a portfolio company of…".
    (re.compile(r"\b(?:portfolio|platform) company of\b", re.I), "Already PE-backed"),
    (re.compile(r"\bbacked by\b.{0,30}\b(capital|partners|equity)\b", re.I), "Names an institutional backer"),
    (re.compile(r"\b(?:a|the)\s+(?:wholly[- ]owned\s+)?(?:subsidiary|division) of\b", re.I), "Subsidiary of a larger group"),
    (re.compile(r"\b(?:nasdaq|nyse)\s*:", re.I), "Publicly listed"),
]

_MODERN_STACK = {
    "next.js", "react", "gatsby", "nuxt", "svelte", "astro", "hubspot",
    "shopify", "webflow", "squarespace", "wix",
}
_LEGACY_STACK = {
    "frontpage", "dreamweaver", "wordpress 4", "joomla", "flash", "jquery 1",
    "table-layout", "godaddy website builder",
}

_ROLE_LOCALPARTS = {
    "info", "contact", "sales", "support", "hello", "admin", "office", "enquiries",
    "inquiries", "help", "team", "mail", "service",
}

_DECISION_TITLES = re.compile(
    r"\b(owner|founder|president|ceo|principal|managing (?:director|partner)|proprietor|general manager)\b",
    re.I,
)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _confidence(found: int, missing: int) -> Confidence:
    """Confidence is about *coverage*, not about the score's magnitude."""
    total = found + missing
    if total == 0:
        return Confidence.LOW
    ratio = found / total
    if ratio >= 0.66 and found >= 2:
        return Confidence.HIGH
    if ratio >= 0.34:
        return Confidence.MEDIUM
    return Confidence.LOW


def _result(
    key: FactorKey,
    score: float,
    evidence: list[Evidence],
    missing: list[str],
    *,
    measured: bool | None = None,
) -> FactorResult:
    """Build a factor result.

    `measured` defaults to "did we observe anything?" — which is right for most
    factors, because with no observations the score is just the prior. Factors
    where an absence is itself the finding pass it explicitly.
    """
    return FactorResult(
        key=key,
        label=FACTOR_LABELS[key],
        score=round(_clamp(score), 1),
        confidence=_confidence(len(evidence), len(missing)),
        evidence=evidence,
        missing_signals=missing,
        measured=bool(evidence) if measured is None else measured,
    )


def _corpus(company: Company) -> str:
    """All free text we hold about the company, for pattern matching."""
    parts = [
        company.web.raw_text_excerpt or "",
        " ".join(company.web.owner_mentions),
        " ".join(company.web.pe_backed_mentions),
        company.name,
    ]
    return " ".join(parts)


def _best_match(
    text: str, patterns: list[tuple[re.Pattern[str], str, float]]
) -> tuple[re.Match[str], str, float] | None:
    """Strongest matching pattern in a group, or None. Groups are mutually
    redundant phrasings of one signal, so only the best one is credited."""
    best: tuple[re.Match[str], str, float] | None = None
    for pattern, label, impact in patterns:
        match = pattern.search(text)
        if match and (best is None or impact > best[2]):
            best = (match, label, impact)
    return best


# --------------------------------------------------------------------------- #
# 1. succession
# --------------------------------------------------------------------------- #

def score_succession(company: Company, *, today: datetime | None = None) -> FactorResult:
    """Is the owner plausibly approaching an exit?

    The ETA thesis lives or dies here: a great business with no succession
    pressure is not for sale.
    """
    now = today or datetime.now(UTC)
    text = _corpus(company)
    evidence: list[Evidence] = []
    missing: list[str] = []
    score = 35.0  # neutral prior — absence of evidence is not evidence of absence

    owner_claim = _best_match(text, _OWNERSHIP_PATTERNS)
    if owner_claim is not None:
        hit, label, impact = owner_claim
        score += impact
        evidence.append(
            Evidence(
                label=label,
                detail=_excerpt(text, hit.start(), hit.end()),
                source_url=company.website,
                impact=impact,
            )
        )
    else:
        missing.append("ownership language")

    founded = company.founded_year or company.web.founded_year
    age = now.year - founded if founded else None

    if age is not None and age < 8:
        score -= 18.0
        evidence.append(
            Evidence(
                label="Young business",
                detail=f"Founded {founded} — only {age} years old; owner is "
                "unlikely to be seeking an exit.",
                source_url=company.website,
                impact=-18.0,
            )
        )
    else:
        # A computed age and a "serving since 1978" line are the same claim from
        # two sources. Credit the stronger, not the sum.
        tenure: list[tuple[str, str, float]] = []
        if age is not None and age >= 25:
            tenure.append(
                (
                    "Long-established business",
                    f"Founded {founded} — {age} years of trading, "
                    "consistent with an owner near retirement.",
                    min(24.0, 8.0 + (age - 25) * 0.6),
                )
            )
        claim = _best_match(text, _TENURE_PATTERNS)
        if claim is not None:
            hit, label, impact = claim
            tenure.append((label, _excerpt(text, hit.start(), hit.end()), impact))
        if tenure:
            label, detail, impact = max(tenure, key=lambda t: t[2])
            score += impact
            evidence.append(
                Evidence(
                    label=label, detail=detail, source_url=company.website, impact=impact
                )
            )

    if founded is None:
        missing.append("founding year")

    for pattern, label in _PE_PATTERNS:
        match = pattern.search(text)
        if match:
            score -= 45.0
            evidence.append(
                Evidence(
                    label=label,
                    detail=_excerpt(text, match.start(), match.end()),
                    source_url=company.website,
                    impact=-45.0,
                )
            )
            break  # one disqualifying signal is enough

    if not company.web.raw_text_excerpt:
        missing.append("website text")
    if not any(c.is_decision_maker for c in company.contacts):
        missing.append("named owner or principal")
    else:
        owner = next(c for c in company.contacts if c.is_decision_maker)
        score += 8.0
        evidence.append(
            Evidence(
                label="Named decision maker",
                detail=f"{owner.name or 'Decision maker'}"
                + (f" — {owner.title}" if owner.title else ""),
                source_url=owner.linkedin_url or company.website,
                impact=8.0,
            )
        )

    return _result(FactorKey.SUCCESSION, score, evidence, missing)


# --------------------------------------------------------------------------- #
# 2. buy-box fit
# --------------------------------------------------------------------------- #

def score_buy_box(company: Company, buy_box: BuyBox | None = None) -> FactorResult:
    """Is it the right size? Too small can't service debt; too big is out of reach."""
    box = buy_box or BuyBox()
    evidence: list[Evidence] = []
    missing: list[str] = []
    parts: list[float] = []

    if company.employee_count is not None:
        sub = _band_score(
            company.employee_count, box.min_employees, box.max_employees, box.tolerance
        )
        parts.append(sub)
        qualifier = "estimated " if company.employee_count_is_estimate else ""
        evidence.append(
            Evidence(
                label="Headcount",
                detail=f"{qualifier}{company.employee_count} employees against a "
                f"{box.min_employees}-{box.max_employees} target band.",
                source_url=company.source_url,
                impact=sub - 50,
            )
        )
    else:
        missing.append("employee count")

    if company.revenue_usd is not None:
        sub = _band_score(
            company.revenue_usd, box.min_revenue_usd, box.max_revenue_usd, box.tolerance
        )
        parts.append(sub)
        qualifier = "estimated " if company.revenue_is_estimate else ""
        evidence.append(
            Evidence(
                label="Revenue",
                detail=f"{qualifier}${company.revenue_usd:,.0f} against a "
                f"${box.min_revenue_usd:,.0f}-${box.max_revenue_usd:,.0f} target band.",
                source_url=company.source_url,
                impact=sub - 50,
            )
        )
    else:
        missing.append("revenue estimate")

    score = sum(parts) / len(parts) if parts else 30.0
    return _result(FactorKey.BUY_BOX, score, evidence, missing)


def _band_score(value: float, lo: float, hi: float, tolerance: float) -> float:
    """100 inside the band, decaying linearly to 0 at the tolerance edge."""
    if lo <= value <= hi:
        return 100.0
    if value < lo:
        floor = lo * (1 - tolerance)
        if value <= floor:
            return 0.0
        return 100.0 * (value - floor) / (lo - floor)
    ceiling = hi * (1 + tolerance)
    if value >= ceiling:
        return 0.0
    return 100.0 * (ceiling - value) / (ceiling - hi)


# --------------------------------------------------------------------------- #
# 3. digital maturity gap
# --------------------------------------------------------------------------- #

def score_digital_gap(company: Company, *, today: datetime | None = None) -> FactorResult:
    """How much cheap upside is there post-acquisition?

    Counter-intuitively, a *worse* website scores *higher* — an operator buying
    a business with no analytics and no mobile site has obvious levers to pull.
    """
    now = today or datetime.now(UTC)
    web = company.web
    evidence: list[Evidence] = []
    missing: list[str] = []
    # Base and impacts are sized so that a site failing *every* check lands at 89,
    # not past 100. Clamping at the ceiling would erase the ordering between a bad
    # site and a very bad one, and ordering is the whole product.
    score = 28.0

    if not company.website:
        return _result(
            FactorKey.DIGITAL_GAP,
            50.0,
            [
                Evidence(
                    label="No website found",
                    detail="No web presence located. Large modernisation upside, "
                    "but also a signal the business may be very small or inactive.",
                    impact=10.0,
                )
            ],
            ["website", "site technology", "content freshness"],
        )

    checks: list[tuple[bool | None, str, str, float]] = [
        (web.https, "No HTTPS", "Site served over plain HTTP.", 11.0),
        (web.mobile_viewport, "No mobile viewport", "Page is not mobile-responsive.", 13.0),
        (web.has_analytics, "No analytics", "No analytics tag detected — the owner is "
         "flying blind on demand.", 9.0),
    ]
    for value, label, detail, impact in checks:
        if value is None:
            missing.append(label.lower())
            continue
        if value is False:
            score += impact
            evidence.append(
                Evidence(label=label, detail=detail, source_url=company.website, impact=impact)
            )
        else:
            score -= impact * 0.5
            evidence.append(
                Evidence(
                    label=label.replace("No ", "Has ").replace("no ", "has "),
                    detail="Already in place — less modernisation headroom here.",
                    source_url=company.website,
                    impact=-impact * 0.5,
                )
            )

    hints = {h.lower() for h in web.tech_hints}
    if hints & _LEGACY_STACK:
        score += 13.0
        evidence.append(
            Evidence(
                label="Legacy site technology",
                detail=f"Detected {', '.join(sorted(hints & _LEGACY_STACK))}.",
                source_url=company.website,
                impact=13.0,
            )
        )
    elif hints & _MODERN_STACK:
        score -= 12.0
        evidence.append(
            Evidence(
                label="Modern site technology",
                detail=f"Detected {', '.join(sorted(hints & _MODERN_STACK))} — "
                "digital basics already covered.",
                source_url=company.website,
                impact=-12.0,
            )
        )
    elif not hints:
        missing.append("site technology")

    latest = web.latest_content_year or web.copyright_year
    if latest:
        staleness = now.year - latest
        if staleness >= 2:
            impact = min(15.0, 4.0 * staleness)
            score += impact
            evidence.append(
                Evidence(
                    label="Stale content",
                    detail=f"Most recent dated content is from {latest} — "
                    f"{staleness} years out of date.",
                    source_url=company.website,
                    impact=impact,
                )
            )
        else:
            score -= 8.0
            evidence.append(
                Evidence(
                    label="Current content",
                    detail=f"Site content is current as of {latest}.",
                    source_url=company.website,
                    impact=-8.0,
                )
            )
    else:
        missing.append("content freshness")

    return _result(FactorKey.DIGITAL_GAP, score, evidence, missing)


# --------------------------------------------------------------------------- #
# 4. niche fragmentation
# --------------------------------------------------------------------------- #

def score_fragmentation(company: Company) -> FactorResult:
    """Fragmented niches support roll-ups. One dominant player does not.

    `peer_count_in_niche` is filled in by a neighbourhood query over the same
    industry and geography, so this is a real measurement rather than a guess.
    """
    peers = company.peer_count_in_niche
    if peers is None:
        return _result(
            FactorKey.FRAGMENTATION,
            45.0,
            [],
            ["peer density in niche"],
        )

    # 0 peers => nothing to roll up. 8-40 peers => healthy fragmentation.
    # 100+ => commoditised, likely low margin and hard to differentiate.
    if peers <= 2:
        score, note = 25.0, "Very few comparable operators nearby — thin roll-up potential."
    elif peers <= 7:
        score, note = 58.0, "A handful of comparable operators — modest consolidation room."
    elif peers <= 40:
        score, note = 90.0, "Well-fragmented niche — strong roll-up and consolidation thesis."
    elif peers <= 90:
        score, note = 70.0, "Highly fragmented but crowded — consolidation possible, competition high."
    else:
        score, note = 45.0, "Commoditised niche — many operators, likely compressed margins."

    return _result(
        FactorKey.FRAGMENTATION,
        score,
        [
            Evidence(
                label="Peer density",
                detail=f"{peers} comparable operators found in "
                f"{company.city or 'the same area'}. {note}",
                source_url=company.source_url,
                impact=score - 50,
            )
        ],
        [],
    )


# --------------------------------------------------------------------------- #
# 5. contactability
# --------------------------------------------------------------------------- #

def score_contactability(company: Company) -> FactorResult:
    """A lead you cannot reach is worth nothing, however good the business."""
    evidence: list[Evidence] = []
    missing: list[str] = []
    score = 0.0

    if not company.contacts:
        # Measured, emphatically. "We found no way to reach this business" is a
        # finding about the lead, not a gap in our data, and a searcher should
        # see it rank accordingly rather than have the factor quietly excused.
        return _result(
            FactorKey.CONTACTABILITY,
            0.0,
            [],
            ["email address", "phone number", "named contact"],
            measured=True,
        )

    best = company.primary_contact
    assert best is not None

    if best.email:
        localpart = best.email.split("@", 1)[0].lower()
        is_role = localpart in _ROLE_LOCALPARTS
        if best.email_status is VerificationStatus.VERIFIED:
            impact = 30.0 if not is_role else 20.0
            detail = (
                "Deliverable mailbox confirmed by MX lookup."
                if not is_role
                else "Deliverable, but a shared role address rather than a person."
            )
        elif best.email_status is VerificationStatus.RISKY:
            impact, detail = 12.0, "Domain accepts all mail — deliverability unconfirmed."
        elif best.email_status is VerificationStatus.INVALID:
            impact, detail = -10.0, "Address failed validation and will bounce."
        else:
            impact, detail = 8.0, "Address found but not yet verified."
        score += impact
        evidence.append(
            Evidence(label="Email", detail=detail, source_url=company.website, impact=impact)
        )
    else:
        missing.append("email address")

    if best.phone:
        impact = 22.0 if best.phone_valid else 10.0
        score += impact
        evidence.append(
            Evidence(
                label="Phone",
                detail="Valid, normalised to E.164." if best.phone_valid
                else "Present but could not be validated.",
                source_url=company.source_url,
                impact=impact,
            )
        )
    else:
        missing.append("phone number")

    if best.name and _DECISION_TITLES.search(best.title or ""):
        score += 30.0
        evidence.append(
            Evidence(
                label="Decision maker identified",
                detail=f"{best.name} — {best.title}. Outreach can go straight to "
                "the person who can sell.",
                source_url=best.linkedin_url or company.website,
                impact=30.0,
            )
        )
    elif best.name:
        score += 12.0
        evidence.append(
            Evidence(
                label="Named contact",
                detail=f"{best.name}"
                + (f" — {best.title}" if best.title else " — title unknown."),
                source_url=best.linkedin_url or company.website,
                impact=12.0,
            )
        )
    else:
        missing.append("named contact")

    if best.linkedin_url:
        score += 12.0
        evidence.append(
            Evidence(
                label="LinkedIn profile",
                detail="A second channel for outreach.",
                source_url=best.linkedin_url,
                impact=12.0,
            )
        )

    return _result(FactorKey.CONTACTABILITY, score, evidence, missing)


# --------------------------------------------------------------------------- #
# 6. business health
# --------------------------------------------------------------------------- #

def score_health(company: Company, *, today: datetime | None = None) -> FactorResult:
    """Cheap liveness check. Filters the dead and the dormant out of the list."""
    now = today or datetime.now(UTC)
    web = company.web
    evidence: list[Evidence] = []
    missing: list[str] = []
    score = 50.0

    latest = web.latest_content_year or web.copyright_year
    if latest is None:
        missing.append("content dates")
    elif now.year - latest <= 1:
        score += 22.0
        evidence.append(
            Evidence(
                label="Site actively maintained",
                detail=f"Content dated {latest}.",
                source_url=company.website,
                impact=22.0,
            )
        )
    elif now.year - latest >= 4:
        score -= 28.0
        evidence.append(
            Evidence(
                label="Possibly dormant",
                detail=f"No content newer than {latest}. Verify the business is still trading.",
                source_url=company.website,
                impact=-28.0,
            )
        )

    if web.has_careers_page:
        score += 16.0
        evidence.append(
            Evidence(
                label="Hiring",
                detail="Site has a careers or jobs page — a growth or turnover signal.",
                source_url=company.website,
                impact=16.0,
            )
        )
    else:
        missing.append("hiring signal")

    if web.has_team_page:
        score += 10.0
        evidence.append(
            Evidence(
                label="Team page",
                detail="Names staff publicly — useful for mapping the org before outreach.",
                source_url=company.website,
                impact=10.0,
            )
        )

    if web.fetched_at is None:
        missing.append("reachable website")
    elif web.page_bytes is not None and web.page_bytes < 2_000:
        score -= 20.0
        evidence.append(
            Evidence(
                label="Placeholder site",
                detail="Homepage is nearly empty — likely a parked domain or "
                "under-construction page.",
                source_url=company.website,
                impact=-20.0,
            )
        )

    return _result(FactorKey.HEALTH, score, evidence, missing)


# --------------------------------------------------------------------------- #

def _excerpt(text: str, start: int, end: int, window: int = 70) -> str:
    """Quote the matched phrase with a little surrounding context, so the user
    can see the actual sentence we based a claim on."""
    lo = max(0, start - window)
    hi = min(len(text), end + window)
    snippet = " ".join(text[lo:hi].split())
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


__all__ = [
    "score_buy_box",
    "score_contactability",
    "score_digital_gap",
    "score_fragmentation",
    "score_health",
    "score_succession",
]
