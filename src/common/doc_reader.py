"""Find the expiry date (and a name, type and provider) in OCR text lines.

Pure functions, no AWS calls: the extractor feeds it the lines Amazon Textract
read from a photo or PDF. Every date found is scored by the words around it,
so "valid until 14/03/2027" beats "issued 14/03/2024" even though both are
dates on the same card.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {
    name.lower(): i
    for i in range(1, 13)
    for name in (calendar.month_name[i], calendar.month_abbr[i])
}
MONTHS["sept"] = 9
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))

# Words that mean "this is when it runs out" vs "this is some other date".
EXPIRY_WORDS = re.compile(
    r"expir|\bexp\b|\bexp\.|valid\s*(?:until|till|thru|through|to|upto|up\s*to)|\bvalid\b|good\s*thru|"
    r"renewal\s*date|renew(?:s|al)?\s*(?:on|by|date)|due\s*date|\bdue\b|end\s*date|\bends?\b|"
    r"cover(?:age)?\s*(?:end|until|to)|policy\s*(?:end|expiry)|period\s*(?:to|end)|"
    r"best\s*before|use\s*by|next\s*(?:billing|payment|renewal)|warranty\s*(?:until|till|expires|end)|"
    r"date\s*of\s*expiry|lapse",
    re.IGNORECASE,
)
OTHER_DATE_WORDS = re.compile(
    r"issue|issued|purchas|bought|invoice|bill\s*date|order\s*date|date\s*of\s*birth|\bdob\b|\bborn\b|"
    r"\bstart|commence|effective|inception|printed|generated|transaction|paid\s*on|receipt\s*date|\bfrom\b",
    re.IGNORECASE,
)
PURCHASE_WORDS = re.compile(r"purchas|bought|invoice|bill\s*date|order\s*date|receipt|date\s*of\s*sale|\bsold\b", re.IGNORECASE)
WARRANTY_SPAN = re.compile(
    r"(\d{1,2})\s*[- ]?\s*(year|yr|month|mo)s?\b[^\n]{0,25}?(?:warranty|guarantee)|"
    r"(?:warranty|guarantee)[^\n\d]{0,25}?(\d{1,2})\s*[- ]?\s*(year|yr|month|mo)s?\b",
    re.IGNORECASE,
)

TYPE_WORDS = [
    ("insurance", re.compile(r"insur|polic(?:y|ies)|\binsurer|cover\s*note|\bclaims?\b", re.IGNORECASE)),
    ("warranty", re.compile(r"warrant|guarantee|applecare|extended\s*protection", re.IGNORECASE)),
    ("passport", re.compile(r"passport|driv(?:ing|er'?s?)\s*licen[cs]e|\blicen[cs]e\b|identity\s*card|\bid\s*card|visa\s*(?:type|category|no|number)|residence\s*permit|aadhaar|\bpan\b", re.IGNORECASE)),
    ("subscription", re.compile(r"subscri|membership|\bplan\b|billing|renews|auto[- ]?renew|monthly|annual\s*plan", re.IGNORECASE)),
]
TYPE_LABELS = {"insurance": "Insurance", "warranty": "Warranty", "passport": "ID document", "subscription": "Subscription", "other": "Document"}
COMPANY_HINT = re.compile(r"\b(?:ltd|limited|inc|llc|plc|corp|corporation|co\.|company|pvt|gmbh|insurance|assurance|bank)\b", re.IGNORECASE)


@dataclass
class Candidate:
    when: date
    line_no: int
    score: int
    month_only: bool


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _end_of_month(y: int, m: int) -> date | None:
    if not 1 <= m <= 12:
        return None
    return date(y, m, calendar.monthrange(y, m)[1])


NUMERIC = re.compile(r"(?<!\d)(\d{1,4})[./\-](\d{1,2})[./\-](\d{2,4})(?!\d)")
TEXT_DMY = re.compile(rf"(?<!\w)(\d{{1,2}})(?:st|nd|rd|th)?[\s\-./,]*({MONTH_RE})\.?[\s\-./,]*(\d{{2,4}})(?!\d)", re.IGNORECASE)
TEXT_MDY = re.compile(rf"(?<!\w)({MONTH_RE})\.?[\s\-./]*(\d{{1,2}})(?:st|nd|rd|th)?,?[\s\-./]+(\d{{4}})(?!\d)", re.IGNORECASE)
TEXT_MY = re.compile(rf"(?<!\w)({MONTH_RE})\.?[\s\-./,]*(\d{{4}})(?!\d)", re.IGNORECASE)
CARD_MY = re.compile(r"(?<![\d/.\-])(0[1-9]|1[0-2])\s*/\s*(\d{2}|\d{4})(?![\d/.\-])")


def find_dates(line: str, order: str = "dmy") -> list[tuple[date, bool, int]]:
    """Every date in one line as (date, month_only, position). ``order`` breaks dd/mm vs mm/dd ties."""
    found: list[tuple[date, bool, int]] = []
    taken: list[tuple[int, int]] = []

    def free(m: re.Match) -> bool:
        return all(m.end() <= a or m.start() >= b for a, b in taken)

    def add(d: date | None, m: re.Match, month_only: bool = False) -> None:
        if d and 1990 <= d.year <= 2100 and free(m):
            found.append((d, month_only, m.start()))
            taken.append((m.start(), m.end()))

    for m in NUMERIC.finditer(line):
        a, b, c = m.groups()
        if len(a) == 4:  # yyyy-mm-dd
            add(_safe_date(int(a), int(b), int(c)), m)
            continue
        y, x, z = _year(c), int(a), int(b)
        if x > 12:
            add(_safe_date(y, z, x), m)          # 25/03/2027 -> day first
        elif z > 12:
            add(_safe_date(y, x, z), m)          # 03/25/2027 -> month first
        elif order == "mdy":
            add(_safe_date(y, x, z), m)
        else:
            add(_safe_date(y, z, x), m)
    for m in TEXT_DMY.finditer(line):
        add(_safe_date(_year(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))), m)
    for m in TEXT_MDY.finditer(line):
        add(_safe_date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2))), m)
    for m in TEXT_MY.finditer(line):
        add(_end_of_month(int(m.group(2)), MONTHS[m.group(1).lower()]), m, month_only=True)
    for m in CARD_MY.finditer(line):
        add(_end_of_month(_year(m.group(2)), int(m.group(1))), m, month_only=True)
    found.sort(key=lambda t: t[2])
    return found


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _warranty_months(text: str) -> int | None:
    m = WARRANTY_SPAN.search(text)
    if not m:
        return None
    n, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    n = int(n)
    return n * 12 if unit.lower().startswith("y") else n


def _score_line(lines: list[str], i: int, pos: int) -> int:
    """Context score for a date at ``pos`` in line ``i``: label text before it counts most."""
    before = lines[i][:pos]
    after = lines[i][pos:]
    label = re.split(r"\d", before)[-1]  # words between the previous number and this date
    prev = lines[i - 1] if i > 0 else ""
    score = 0
    if EXPIRY_WORDS.search(before):
        score += 10
    elif EXPIRY_WORDS.search(prev) and not NUMERIC.search(prev):
        score += 8  # label on the line above the value, as on most cards
    elif EXPIRY_WORDS.search(after[:40]):
        score += 4
    if OTHER_DATE_WORDS.search(label[-30:]):
        score -= 8
    elif not before.strip() and OTHER_DATE_WORDS.search(prev) and not EXPIRY_WORDS.search(prev) and not NUMERIC.search(prev):
        score -= 6
    # "from 01/04/2026 to 31/03/2027": the date after "to" is the end.
    if re.search(r"(?:\bto|\btill|\buntil|-|–)\s*$", label, re.IGNORECASE) and re.search(r"\d", before):
        score += 6
    return score


def guess_type(text: str) -> str:
    """The type whose words appear most often (ties go to the earlier, more specific type)."""
    counts = [(len(rx.findall(text)), -i, kind) for i, (kind, rx) in enumerate(TYPE_WORDS)]
    hits, _, kind = max(counts)
    return kind if hits else "other"


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" -:|,.")


def guess_provider(lines: list[str]) -> str:
    for line in lines[:12]:
        if COMPANY_HINT.search(line) and len(line) <= 60 and not NUMERIC.search(line):
            return _clean(line)[:60]
    for line in lines[:4]:
        letters = sum(ch.isalpha() for ch in line)
        if letters >= 3 and letters >= 0.6 * len(line.replace(" ", "")) and len(line) <= 40 and not EXPIRY_WORDS.search(line):
            return _clean(line)[:60]
    return ""


def guess_name(lines: list[str], kind: str, provider: str) -> str:
    rx = dict(TYPE_WORDS).get(kind)
    if rx:
        matches = [
            _clean(line) for line in lines[:15]
            if rx.search(line) and 3 <= len(line) <= 50 and not find_dates(line) and not EXPIRY_WORDS.search(line)
        ]
        # Prefer a line that describes the item over the issuer's own name.
        matches.sort(key=lambda m: m.lower() == provider.lower())
        if matches:
            name = matches[0]
            return name[:60].title() if name.isupper() else name[:60]
    label = TYPE_LABELS[kind]
    if provider and kind == "other":
        return (provider.title() if provider.isupper() else provider)[:60]
    if provider:
        short = re.sub(COMPANY_HINT, "", provider).strip(" ,.-") or provider
        short = short.title() if short.isupper() else short
        return f"{short} {label}"[:60] if label.lower() not in short.lower() else short[:60]
    return label if kind != "other" else "Uploaded document"


@dataclass
class Reading:
    found: bool
    expiry: str          # YYYY-MM-DD or ""
    confident: bool      # True when an expiry label (or warranty term) backs the date
    name: str
    type: str
    provider: str


def read_document(lines: list[str], today: date, order: str = "dmy") -> Reading:
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    text = "\n".join(lines)
    kind = guess_type(text)
    provider = guess_provider(lines)
    name = guess_name(lines, kind, provider)
    if provider and name.lower() == provider.lower():
        provider = ""

    candidates: list[Candidate] = []
    for i, line in enumerate(lines):
        for d, month_only, pos in find_dates(line, order):
            candidates.append(Candidate(d, i, _score_line(lines, i, pos), month_only))

    best: Candidate | None = None
    confident = False
    labelled = [c for c in candidates if c.score >= 6]
    if labelled:
        # Several labelled dates (e.g. "valid from … to …"): take the highest score, then the latest.
        best = max(labelled, key=lambda c: (c.score, c.when))
        confident = True
    else:
        months = _warranty_months(text)
        if months and candidates:
            bought = [c for c in candidates if PURCHASE_WORDS.search(lines[c.line_no])] or candidates
            start = min(bought, key=lambda c: (c.score < 0 and not PURCHASE_WORDS.search(lines[c.line_no]), c.line_no))
            expiry = _add_months(start.when, months)
            kind = "warranty" if kind == "other" else kind
            return Reading(True, expiry.isoformat(), True, name if name != "Uploaded document" else "Warranty", kind, provider)
        # No label: the latest date that isn't marked as some other date is a reasonable guess,
        # but the user has to confirm it.
        unlabelled = [c for c in candidates if c.score >= 0]
        if unlabelled:
            best = max(unlabelled, key=lambda c: c.when)

    if best is None:
        return Reading(False, "", False, name, kind, provider)
    return Reading(True, best.when.isoformat(), confident, name, kind, provider)
