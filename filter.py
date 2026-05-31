import re
import logging
from typing import Optional

import config

logger = logging.getLogger(__name__)


# ── Compiled patterns (built once) ───────────────────────────────────────────

_FRESHER_EXP_RE = re.compile(
    r"""
    \b(
        0\s*[-–to]+\s*[12]\s*years? |
        0\s*[-–to]+\s*[12]\s*yrs?   |
        0\s*years?                   |
        0\s*yrs?                     |
        1\s*year                     |
        1\s*yr                       |
        fresher s?                   |
        entry[\s\-]level             |
        no[\s\-]experience           |
        no[\s\-]exp\b                |
        fresh\s*graduate             |
        recent\s*graduate            |
        campus\s*hire                |
        trainee                      |
        junior
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SENIOR_EXP_RE = re.compile(
    r"\b([3-9]|\d{2})\+?\s*years?\b|\b([3-9]|\d{2})\+?\s*yrs?\b",
    re.IGNORECASE,
)

_EXP_YEARS_RE = re.compile(
    r"\b(\d+)\s*[-–to]*\s*\d*\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)

_WALKIN_RE = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bwalk[\s\-]in\b",
        r"\bwalkin\b",
        r"\bdirect\s*interview\b",
        r"\bspot\s*(?:selection|offer|hiring)\b",
        r"\bno\s*appointment\b",
        r"\bopen\s*interview\b",
        r"\bmass\s*hiring\b",
        r"\bmega\s*(?:drive|hiring)\b",
        r"\bhiring\s*drive\b",
        r"\bcampus\s*drive\b",
        r"\bdrop\s*(?:your\s*)?(?:cv|resume)\b",
        r"\bimmediate\s*(?:joining|interview)\b",
        r"\boffline\s*interview\b",
        r"\bface[\s\-]to[\s\-]face\s*interview\b",
    ]
]

_EXCLUDED_RE = [
    re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    for kw in config.EXCLUDED_TITLE_KEYWORDS
]

_LOCATION_RE = [
    re.compile(re.escape(loc), re.IGNORECASE)
    for loc in config.TARGET_LOCATIONS
]

_ROLE_RE = [
    re.compile(re.escape(role), re.IGNORECASE)
    for role in config.TARGET_ROLES
]


# ── Individual checks ─────────────────────────────────────────────────────────

def is_excluded_title(title: str) -> bool:
    return any(p.search(title) for p in _EXCLUDED_RE)


def is_target_location(location: str, description: str = "") -> bool:
    text = f"{location} {description}"
    return any(p.search(text) for p in _LOCATION_RE)


def _min_years_from_text(text: str) -> Optional[int]:
    m = _EXP_YEARS_RE.search(text)
    if m:
        return int(m.group(1))
    return None


def is_fresher_eligible(
    experience_field: str,
    description: str = "",
    title: str = "",
) -> tuple[bool, str]:
    """
    Returns (eligible: bool, reason: str).
    """
    combined = f"{experience_field} {description} {title}"

    # Hard exclude: senior in title
    if is_excluded_title(title):
        return False, f"Excluded keyword in title"

    # Hard exclude: explicit high years in experience field
    years = _min_years_from_text(experience_field)
    if years is not None:
        if years > config.MAX_EXPERIENCE_YEARS:
            return False, f"Requires {years} years > limit {config.MAX_EXPERIENCE_YEARS}"
        return True, f"Experience field: {years} yr(s)"

    # Senior pattern in combined text
    if _SENIOR_EXP_RE.search(experience_field):
        return False, "Senior experience pattern in field"

    # Positive fresher match
    m = _FRESHER_EXP_RE.search(combined)
    if m:
        return True, f"Fresher pattern: {m.group().strip()}"

    # No experience mentioned — include cautiously
    if not experience_field.strip():
        return True, "No experience field – included cautiously"

    return False, "Could not confirm fresher eligibility"


def is_walkin(title: str, description: str) -> tuple[bool, str]:
    combined = f"{title} {description}"
    for pattern in _WALKIN_RE:
        m = pattern.search(combined)
        if m:
            return True, m.group().strip()
    return False, ""


def matches_target_role(title: str) -> bool:
    return any(p.search(title) for p in _ROLE_RE)


# ── Master evaluate ───────────────────────────────────────────────────────────

def evaluate_job(job: dict) -> dict:
    """
    Annotate and decide whether a raw job dict should be included.
    Adds: should_include, is_walkin, is_fresher_confirmed, filter_reason.
    """
    title       = job.get("title", "")
    location    = job.get("location", "")
    experience  = job.get("experience", "")
    description = job.get("description", "")

    result = {**job, "should_include": True, "filter_reason": "", "is_walkin": False}

    # 1. Excluded role
    if is_excluded_title(title):
        result["should_include"] = False
        result["filter_reason"] = "Excluded title keyword"
        return result

    # 2. Location
    if not is_target_location(location, description):
        result["should_include"] = False
        result["filter_reason"] = f"Location '{location}' not in Pune area"
        return result

    # 3. Experience
    eligible, reason = is_fresher_eligible(experience, description, title)
    if not eligible:
        result["should_include"] = False
        result["filter_reason"] = reason
        return result

    result["is_fresher_confirmed"] = True
    result["filter_reason"] = reason

    # 4. Walk-in detection (enrichment)
    wk, wk_reason = is_walkin(title, description)
    result["is_walkin"] = wk
    if wk:
        result["filter_reason"] += f" | Walk-in: {wk_reason}"

    return result


def filter_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (accepted, rejected)."""
    accepted, rejected = [], []
    for job in jobs:
        ev = evaluate_job(job)
        if ev["should_include"]:
            accepted.append(ev)
        else:
            rejected.append(ev)
            logger.debug("Rejected [%s]: %s @ %s",
                         ev["filter_reason"], job.get("title"), job.get("company"))
    logger.info("Filter: %d accepted / %d rejected", len(accepted), len(rejected))
    return accepted, rejected
