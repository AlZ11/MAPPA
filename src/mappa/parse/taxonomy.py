"""Google Play Data Safety vocabulary: the common taxonomy for every evidence source.

Source: the table in docs/PROJECT_OUTLINE.md. The outline asks for it to be checked
against Google's current help page, which this environment can't reach, so treat it as
unverified until then. A mismatch can't lose data: an unknown string is kept verbatim
with ``mapped = false``, so it shows up in counts instead of disappearing.

Matching ignores case, spacing and curly-vs-straight apostrophes, nothing more: anything
fuzzier would risk mapping a string to the wrong category.
"""

from mappa.models.enums import LabelPractice

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Location": ("Approximate location", "Precise location"),
    "Personal info": (
        "Name",
        "Email address",
        "User IDs",
        "Address",
        "Phone number",
        "Race and ethnicity",
        "Political or religious beliefs",
        "Sexual orientation",
        "Other info",
    ),
    "Financial info": (
        "User payment info",
        "Purchase history",
        "Credit score",
        "Other financial info",
    ),
    "Health and fitness": ("Health info", "Fitness info"),
    "Messages": ("Emails", "SMS or MMS", "Other in-app messages"),
    "Photos and videos": ("Photos", "Videos"),
    "Audio": ("Voice or sound recordings", "Music files", "Other audio files"),
    "Files and docs": ("Files and docs",),
    "Calendar": ("Calendar events",),
    "Contacts": ("Contacts",),
    "App activity": (
        "App interactions",
        "In-app search history",
        "Installed apps",
        "Other user-generated content",
        "Other actions",
    ),
    "Web browsing": ("Web browsing history",),
    "App info and performance": ("Crash logs", "Diagnostics", "Other app performance data"),
    "Device or other IDs": ("Device or other IDs",),
}

PURPOSES: tuple[str, ...] = (
    "App functionality",
    "Analytics",
    "Developer communications",
    "Advertising or marketing",
    "Fraud prevention, security, and compliance",
    "Personalization",
    "Account management",
)

# Security-practice statements -> (practice, value). Both the positive and the negative
# wording are statements by the developer, so both are recorded. Unverified wording.
PRACTICE_STATEMENTS: dict[str, tuple[LabelPractice, bool]] = {
    "data is encrypted in transit": (LabelPractice.ENCRYPTED_IN_TRANSIT, True),
    "data isn't encrypted": (LabelPractice.ENCRYPTED_IN_TRANSIT, False),
    "data is not encrypted": (LabelPractice.ENCRYPTED_IN_TRANSIT, False),
    "you can request that data be deleted": (LabelPractice.DELETION_REQUEST, True),
    "data can't be deleted": (LabelPractice.DELETION_REQUEST, False),
    "data cannot be deleted": (LabelPractice.DELETION_REQUEST, False),
    "committed to follow the play families policy": (LabelPractice.FAMILIES_POLICY, True),
    "independent security review": (LabelPractice.INDEPENDENT_REVIEW, True),
}


def norm(text: str) -> str:
    return " ".join(text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").lower().split())


_CATEGORY_BY_NORM = {norm(c): c for c in CATEGORIES}
_TYPE_BY_NORM = {cat: {norm(t): t for t in types} for cat, types in CATEGORIES.items()}
_PURPOSE_BY_NORM = {norm(p): p for p in PURPOSES}


def map_data_type(raw_category: str, raw_label: str) -> tuple[str | None, str | None]:
    """(category, data_type) as taxonomy values; either is None when not recognised.
    A data type only counts as mapped under its own category."""
    category = _CATEGORY_BY_NORM.get(norm(raw_category))
    if category is None:
        return None, None
    return category, _TYPE_BY_NORM[category].get(norm(raw_label))


def split_purposes(raw: str) -> list[str]:
    """Split a purposes string such as "App functionality, Analytics" into purposes.

    A plain split on commas would break "Fraud prevention, security, and compliance",
    so the longest run of comma-separated pieces that forms a known purpose wins.
    Unknown pieces are kept verbatim rather than dropped.
    """
    pieces = [piece.strip() for piece in raw.split(",")]
    purposes: list[str] = []
    i = 0
    while i < len(pieces):
        for size in range(len(pieces) - i, 0, -1):
            known = _PURPOSE_BY_NORM.get(norm(", ".join(pieces[i : i + size])))
            if known is not None:
                purposes.append(known)
                i += size
                break
        else:
            if pieces[i]:
                purposes.append(pieces[i])
            i += 1
    return purposes


def map_practice(statement: str) -> tuple[LabelPractice, bool] | None:
    return PRACTICE_STATEMENTS.get(norm(statement))
