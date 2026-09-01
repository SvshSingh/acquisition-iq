"""Deduplication.

The same business reaches us more than once for boring reasons: OpenStreetMap
maps a shop as a node and its building as a way, a franchise lists each branch,
someone typed the name twice with different punctuation. Left alone, a searcher
opens what looks like a list of 250 targets and finds it is really 240 with ten
counted twice — and worse, the duplicates cluster at the top, because whatever
made a company attractive made it attractive in both copies.

**Blocking, not pairwise.** Comparing every record with every other is n²: at 250
rows that is 31,125 comparisons, at 5,000 rows it is 12.5 million and the feature
stops being usable at exactly the point it starts to matter. Instead each record
gets a set of cheap keys, only records sharing a key are compared, and the fuzzy
match runs inside those small groups. Same answers, a fraction of the work.

**Evidence, not a verdict.** A merge decision a user cannot audit is a merge
decision they cannot trust, so every match carries the reason and the score that
produced it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.pipeline.geo import haversine_km
from app.pipeline.normalize import (
    email_domain,
    name_blocking_key,
    normalise_name,
    normalise_postcode,
    registrable_domain,
)
from app.schemas import Company

logger = logging.getLogger(__name__)

# Above this, two names in the same block are the same business. Tuned against
# the real seed set: "Whitaker Heating and Cooling" vs "Whitaker Heating &
# Cooling" scores ~97, while "Columbus Dental Care" vs "Columbus Dental Group" —
# genuinely different practices — scores ~84, so the line sits between them.
NAME_SIMILARITY_THRESHOLD = 90.0

# A weaker name match still merges when geography agrees, because two records
# with the same-ish name at the same postcode are not a coincidence.
NAME_WITH_LOCATION_THRESHOLD = 80.0

# Two records further apart than this are different premises, however alike their
# names and domains. The value is read off the data rather than guessed: across
# the 137 same-name pairs in the Columbus seed set, the distances fall into two
# clusters with an empty gap between them — one pair at 0.41km (a vet clinic
# mapped twice), then nothing until 1.33km, above which every pair is a chain
# branch. 0.75km sits in that gap.
SAME_PREMISES_KM = 0.75


@dataclass(frozen=True)
class DuplicateMatch:
    kept_id: str
    dropped_id: str
    reason: str
    similarity: float


@dataclass
class DedupeResult:
    companies: list[Company]
    matches: list[DuplicateMatch] = field(default_factory=list)

    @property
    def removed(self) -> int:
        return len(self.matches)


def blocking_keys(company: Company) -> set[str]:
    """Cheap keys under which this record might meet its duplicate.

    Several keys per record on purpose: a duplicate that differs in name may
    still share a domain, and one that differs in domain may share a phone. One
    key per record would make the block a single point of failure.
    """
    keys: set[str] = set()

    domain = registrable_domain(company.website or company.domain)
    if domain:
        keys.add(f"d:{domain}")

    for contact in company.contacts:
        mail_domain = email_domain(contact.email)
        # Free-mail domains are useless as a key: every gmail.com business would
        # land in one giant block and blocking would buy nothing.
        if mail_domain and mail_domain != domain and "." in mail_domain:
            keys.add(f"e:{mail_domain}")
        if contact.phone:
            digits = "".join(ch for ch in contact.phone if ch.isdigit())[-10:]
            if len(digits) == 10:
                keys.add(f"p:{digits}")

    name_key = name_blocking_key(company.name)
    if name_key:
        # A key per locality field, not one key built from "postcode else city".
        # Two records for one business routinely disagree about which of those
        # they carry, and a single combined key puts them in different blocks —
        # where they are never compared and the duplicate survives. Emitting both
        # costs one extra set entry and removes the whole failure mode.
        postcode = normalise_postcode(company.postcode)
        if postcode:
            keys.add(f"n:{name_key}:{postcode}".lower())
        if company.city:
            keys.add(f"n:{name_key}:{company.city}".lower())
        if not postcode and not company.city:
            keys.add(f"n:{name_key}:".lower())

    return keys


def _too_far_apart(a: Company, b: Company) -> bool:
    """Whether coordinates rule these two out as the same premises.

    This guard is what separates a duplicate from a branch. The first run of
    dedupe on the seed set happily merged three NTB tyre centres because they
    share ntb.com and an identical name — but they are three shops in three parts
    of Columbus, and collapsing them silently deletes two real businesses from
    the user's list. A chain's locations are distinct records; only records
    describing the same premises are duplicates.

    When either record lacks coordinates we cannot rule it out, and fall through
    to the name and domain evidence.
    """
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return False
    assert a.latitude is not None and a.longitude is not None
    assert b.latitude is not None and b.longitude is not None
    return haversine_km(a.latitude, a.longitude, b.latitude, b.longitude) > SAME_PREMISES_KM


def _similarity(a: Company, b: Company) -> tuple[float, str] | None:
    """How alike are these two, and why? None means not a duplicate."""
    if _too_far_apart(a, b):
        return None

    domain_a = registrable_domain(a.website or a.domain)
    domain_b = registrable_domain(b.website or b.domain)
    name_score = fuzz.token_sort_ratio(normalise_name(a.name), normalise_name(b.name))

    # A shared registrable domain is near-decisive: two businesses do not share
    # one. The name floor only guards against a directory site that lists many
    # unrelated companies under its own domain.
    if domain_a and domain_a == domain_b and name_score >= 60:
        return name_score, f"same domain ({domain_a})"

    if name_score >= NAME_SIMILARITY_THRESHOLD:
        return name_score, "near-identical name"

    if name_score >= NAME_WITH_LOCATION_THRESHOLD:
        post_a, post_b = normalise_postcode(a.postcode), normalise_postcode(b.postcode)
        if post_a and post_a == post_b:
            return name_score, f"similar name at the same postcode ({post_a})"
        if a.city and a.city.lower() == (b.city or "").lower():
            return name_score, f"similar name in the same city ({a.city})"

    return None


def _completeness(company: Company) -> tuple[int, int, int]:
    """Sort key deciding which of two duplicates survives.

    Keep the record a user can act on: contacts first, then crawled signals,
    then raw field count. Picking arbitrarily would sometimes discard the copy
    that had the owner's email in it.
    """
    contact_score = sum(
        bool(getattr(c, f)) for c in company.contacts for f in ("email", "phone", "name")
    )
    web_score = 1 if company.web.fetched_at else 0
    filled = sum(
        1
        for f in ("website", "industry", "city", "postcode", "founded_year", "employee_count")
        if getattr(company, f) is not None
    )
    return contact_score, web_score, filled


def _merge(keep: Company, drop: Company) -> Company:
    """Fold the dropped record's non-empty fields into the survivor.

    Deduplication should never lose information — if the copy being discarded
    knew the founding year and the survivor did not, the survivor learns it.
    """
    updates: dict[str, object] = {}
    for field_name in (
        "website", "domain", "industry", "naics", "city", "state", "postcode",
        "latitude", "longitude", "founded_year", "employee_count", "revenue_usd",
        "peer_count_in_niche",
    ):
        if getattr(keep, field_name) is None and getattr(drop, field_name) is not None:
            updates[field_name] = getattr(drop, field_name)

    if drop.contacts and not keep.contacts:
        updates["contacts"] = drop.contacts
    if drop.web.fetched_at and not keep.web.fetched_at:
        updates["web"] = drop.web

    return keep.model_copy(update=updates) if updates else keep


def deduplicate(companies: list[Company]) -> DedupeResult:
    """Collapse duplicate records, keeping the most complete copy of each."""
    by_key: dict[str, list[int]] = defaultdict(list)
    for index, company in enumerate(companies):
        for key in blocking_keys(company):
            by_key[key].append(index)

    # Union-find over indices: a three-way duplicate must collapse to one
    # record, not to a chain of unresolved pairs.
    parent = list(range(len(companies)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    matches: list[DuplicateMatch] = []
    seen_pairs: set[tuple[int, int]] = set()

    for key, indices in by_key.items():
        if len(indices) < 2:
            continue
        if len(indices) > 50:
            # A block this size means the key was not selective (a directory
            # domain, a shared switchboard). Comparing it is the n² we came here
            # to avoid, and its members are not duplicates of each other anyway.
            logger.debug("skipping oversized block %s (%d records)", key, len(indices))
            continue
        for position, i in enumerate(indices):
            for j in indices[position + 1 :]:
                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                verdict = _similarity(companies[i], companies[j])
                if verdict is None:
                    continue
                score, reason = verdict
                keeper, dropped = (
                    (i, j) if _completeness(companies[i]) >= _completeness(companies[j]) else (j, i)
                )
                root_keep, root_drop = find(keeper), find(dropped)
                if root_keep == root_drop:
                    continue
                parent[root_drop] = root_keep
                matches.append(
                    DuplicateMatch(
                        kept_id=companies[keeper].id,
                        dropped_id=companies[dropped].id,
                        reason=reason,
                        similarity=round(float(score), 1),
                    )
                )

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(companies)):
        groups[find(index)].append(index)

    survivors: list[Company] = []
    for root, members in groups.items():
        survivor = companies[root]
        for member in members:
            if member != root:
                survivor = _merge(survivor, companies[member])
        survivors.append(survivor)

    # Stable output ordering, so a re-run produces a byte-identical dataset.
    order = {company.id: i for i, company in enumerate(companies)}
    survivors.sort(key=lambda c: order[c.id])
    return DedupeResult(companies=survivors, matches=matches)


__all__ = [
    "CHAIN_LOCATION_THRESHOLD",
    "NAME_SIMILARITY_THRESHOLD",
    "NAME_WITH_LOCATION_THRESHOLD",
    "SAME_PREMISES_KM",
    "DedupeResult",
    "DuplicateMatch",
    "annotate_chain_locations",
    "blocking_keys",
    "deduplicate",
]


# Three locations under one name is a chain. Two is ambiguous — a dentist with a
# second surgery across town is still an owner-operated business a searcher could
# buy, and penalising that would throw away real targets.
CHAIN_LOCATION_THRESHOLD = 3


def annotate_chain_locations(companies: list[Company]) -> int:
    """Count how many premises trade under each name, and record it per company.

    This is the mirror image of deduplication, and it took the real dataset to
    see it. Of 137 same-name pairs in the Columbus seed set, 135 were more than
    2km apart: Valvoline, Walmart Vision Center, Target Optical, Allstate. None
    of them are duplicate records — they are branch networks, and merging them
    would have deleted real businesses from the list.

    But they are not acquisition targets either. A search fund buys a business
    from an owner who wants to retire; a franchise network has no such owner, and
    GEICO ranked 12th before this existed. The same-name clustering that looks
    like a data-quality problem is really a *signal*, and the succession factor
    reads it.

    Returns the number of companies belonging to a chain.
    """
    groups: dict[str, set[tuple[float, float] | str]] = defaultdict(set)
    for company in companies:
        key = normalise_name(company.name)
        if not key:
            continue
        # Distinct premises, not distinct records: a business mapped twice at one
        # location must not count as two branches.
        where: tuple[float, float] | str
        if company.latitude is not None and company.longitude is not None:
            where = (round(company.latitude, 3), round(company.longitude, 3))
        else:
            where = company.id
        groups[key].add(where)

    flagged = 0
    for company in companies:
        key = normalise_name(company.name)
        count = len(groups.get(key, ()))
        company.sibling_location_count = count
        if count >= CHAIN_LOCATION_THRESHOLD:
            flagged += 1
    return flagged
