"""Contact validation: email deliverability and phone normalisation.

The point of this module is to make the difference between a lead you can act on
and a string that looks like one. Every claim it makes is bounded by what it
actually checked, which matters more here than anywhere else in the codebase: a
tool that tells a searcher an address is good, and is wrong, costs them a bounced
outreach and their sender reputation.

**What we check.** Syntax against the RFC, then whether the domain publishes a
mail exchanger. A domain with no MX and no A record cannot receive mail at all,
so the address will bounce — that is a definite negative and we say so.

**What we deliberately do not check.** Whether the individual mailbox exists.
Confirming that means opening an SMTP conversation with someone's mail server and
abandoning it before DATA, thousands of times over. It is unreliable (most
serious providers answer every RCPT identically), it is rude, and it is how a
sending IP gets blocklisted. So `VERIFIED` here means "this domain accepts mail
and the address is well-formed" — never "this person's inbox exists" — and the
evidence string the UI shows says exactly that.

Being straight about the boundary is the product. An overconfident "verified"
badge is worse than an honest "unverified", because the user cannot tell which
one lied.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import dns.asyncresolver
import dns.exception
import dns.resolver
import phonenumbers
from email_validator import EmailNotValidError, validate_email

from app.schemas import ROLE_LOCALPARTS, Contact, VerificationStatus

logger = logging.getLogger(__name__)

# Throwaway mailbox providers. An address here is real but nobody is reading it.
DISPOSABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
        "throwawaymail.com", "yopmail.com", "trashmail.com", "sharklasers.com",
        "getnada.com", "temp-mail.org", "fakeinbox.com", "maildrop.cc",
        "dispostable.com", "mailnesia.com", "spamgourmet.com", "mytemp.email",
    }
)

# Consumer mailboxes. Not a problem — a great many owner-operated businesses run
# on one, and for this product that is itself a mild succession signal rather
# than a defect. Flagged for transparency, never penalised.
FREE_MAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
        "icloud.com", "me.com", "msn.com", "live.com", "comcast.net",
        "sbcglobal.net", "verizon.net", "att.net", "protonmail.com",
    }
)

@dataclass
class EmailVerdict:
    status: VerificationStatus
    reasons: list[str] = field(default_factory=list)
    is_role: bool = False
    is_free_mail: bool = False
    normalised: str | None = None
    mx_hosts: list[str] = field(default_factory=list)


class MxResolver:
    """MX lookups with a per-domain memo.

    Two hundred companies share far fewer mail domains than you would expect —
    every business on Google Workspace resolves to the same handful of hosts — so
    the memo turns most of the work into nothing. The lock matters for the same
    reason it does in the robots fetcher: without it, a burst of addresses on one
    domain fires a burst of identical DNS queries.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.timeout = timeout
        self._resolver.lifetime = timeout
        self._memo: dict[str, list[str] | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def mx_hosts(self, domain: str) -> list[str] | None:
        """Mail exchangers for a domain.

        Returns `[]` when the domain resolves but publishes no mail route, and
        `None` when the lookup itself failed — the difference between "this will
        bounce" and "we could not find out", which must not collapse.
        """
        domain = domain.lower().strip(".")
        if domain in self._memo:
            return self._memo[domain]

        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            if domain in self._memo:
                return self._memo[domain]

            hosts: list[str] | None
            try:
                answer = await self._resolver.resolve(domain, "MX")
                hosts = sorted(str(r.exchange).rstrip(".") for r in answer)
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                # No MX published. That is not automatically fatal: RFC 5321
                # falls back to the A record, and plenty of small businesses
                # rely on exactly that. Distinguishing "no MX but the domain is
                # real" from "no such domain" needs an A lookup, which the
                # caller does via `domain_exists` only when it reaches this case.
                hosts = []
            except (dns.exception.DNSException, OSError) as exc:
                logger.debug("MX lookup failed for %s: %s", domain, exc)
                hosts = None

            self._memo[domain] = hosts
            return hosts

    async def domain_exists(self, domain: str) -> bool | None:
        try:
            await self._resolver.resolve(domain.lower().strip("."), "A")
            return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return False
        except (dns.exception.DNSException, OSError):
            return None


async def verify_email(email: str, resolver: MxResolver) -> EmailVerdict:
    """Classify one address. Never raises."""
    reasons: list[str] = []

    try:
        # check_deliverability=False: we do our own DNS below, with a memo and a
        # timeout we control. Letting the library do it would make every address
        # on a shared domain pay for its own lookup.
        info = validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        return EmailVerdict(
            status=VerificationStatus.INVALID, reasons=[f"Malformed address: {exc}"]
        )

    normalised = info.normalized.lower()
    localpart, _, domain = normalised.partition("@")
    is_role = localpart in ROLE_LOCALPARTS
    is_free_mail = domain in FREE_MAIL_DOMAINS

    if is_role:
        reasons.append("Shared role address rather than a named person")
    if is_free_mail:
        reasons.append("Consumer mailbox — common for owner-operated businesses")

    if domain in DISPOSABLE_DOMAINS:
        reasons.append("Disposable mailbox provider")
        return EmailVerdict(
            status=VerificationStatus.RISKY,
            reasons=reasons,
            is_role=is_role,
            is_free_mail=is_free_mail,
            normalised=normalised,
        )

    hosts = await resolver.mx_hosts(domain)
    if hosts is None:
        reasons.append("DNS lookup did not complete — deliverability unknown")
        status = VerificationStatus.UNKNOWN
    elif not hosts:
        exists = await resolver.domain_exists(domain)
        if exists is False:
            reasons.append("Domain does not resolve — mail to it will bounce")
            status = VerificationStatus.INVALID
        else:
            reasons.append("No MX record; delivery would fall back to the A record")
            status = VerificationStatus.RISKY
    else:
        reasons.append(
            f"Domain publishes {len(hosts)} mail exchanger(s). "
            "Mailbox existence not probed — see module docstring."
        )
        status = VerificationStatus.VERIFIED

    return EmailVerdict(
        status=status,
        reasons=reasons,
        is_role=is_role,
        is_free_mail=is_free_mail,
        normalised=normalised,
        mx_hosts=hosts or [],
    )


def normalise_phone(raw: str, region: str = "US") -> tuple[str | None, bool]:
    """Parse to E.164. Returns `(normalised, is_valid)`.

    E.164 is what every dialer and CRM wants, and normalising here means the
    dedupe pass can treat the phone number as a join key instead of comparing
    seven spellings of the same number.
    """
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None, False

    if not phonenumbers.is_possible_number(parsed):
        return None, False

    formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return formatted, phonenumbers.is_valid_number(parsed)


async def validate_contact(contact: Contact, resolver: MxResolver) -> Contact:
    """Return a copy of `contact` with verification filled in."""
    updates: dict[str, object] = {}

    if contact.email:
        verdict = await verify_email(contact.email, resolver)
        updates["email_status"] = verdict.status
        if verdict.normalised:
            updates["email"] = verdict.normalised

    if contact.phone:
        normalised, valid = normalise_phone(contact.phone)
        updates["phone_valid"] = valid
        if normalised:
            updates["phone"] = normalised

    return contact.model_copy(update=updates) if updates else contact


async def validate_contacts(
    contacts: list[Contact], resolver: MxResolver, *, concurrency: int = 16
) -> list[Contact]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(contact: Contact) -> Contact:
        async with semaphore:
            return await validate_contact(contact, resolver)

    return list(await asyncio.gather(*(one(c) for c in contacts)))


__all__ = [
    "DISPOSABLE_DOMAINS",
    "FREE_MAIL_DOMAINS",
    "ROLE_LOCALPARTS",
    "EmailVerdict",
    "MxResolver",
    "normalise_phone",
    "validate_contact",
    "validate_contacts",
    "verify_email",
]
