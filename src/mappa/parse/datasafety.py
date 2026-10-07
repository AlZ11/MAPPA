"""Parse a saved Google Play Data Safety page into label facts and practices.

Where the label lives: Google embeds it as JSON in the page (a ``ds:N`` data block),
whether or not its sections are expanded on screen. The positions below come from the
maintained Node ``google-play-scraper`` 10.1.3 (``lib/datasafety.js``), the cross-check
tool the task names. They are NOT yet validated against pages saved by hand: until the
fixture tests in tests/fixtures/datasafety/ pass, treat this parser's output as
provisional. ``PARSER_VERSION`` changes whenever the logic does, and every row records
the version that wrote it.

Unknown != absent (principle 4), applied to labels:

- "has not provided information" on the page -> ``not_provided`` (a finding).
- "No data shared with third parties" / "No data collected" -> recorded as practices
  ``no_data_shared`` / ``no_data_collected`` with value true. We never infer them from an
  empty list: an empty list could just as well mean the layout changed.
- Anything we can't account for raises ``LabelParseError`` instead of producing a
  plausible-looking empty label.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import lxml.html

from mappa.models.enums import LabelPractice, LabelSection, Status
from mappa.parse import taxonomy
from mappa.parse.play_data import PlayParseError, extract_datasets, lookup

PARSER_VERSION = "datasafety-0.1.0"

LABEL_PATH = (1, 2, 1, 138)
SHARED_PATH = (4, 0, 0)
COLLECTED_PATH = (4, 1, 0)
PRACTICES_PATH = (9, 2)

NOT_PROVIDED_PHRASE = "has not provided information"
NO_DATA_SHARED_PHRASE = "no data shared with third parties"
NO_DATA_COLLECTED_PHRASE = "no data collected"

SECTION_HEADINGS = {LabelSection.SHARED: "Data shared", LabelSection.COLLECTED: "Data collected"}


class LabelParseError(ValueError):
    """The page couldn't be read as a Data Safety label. Recorded, never guessed around."""


@dataclass(frozen=True)
class Fact:
    section: LabelSection
    category: str | None
    data_type: str | None
    raw_category: str
    raw_label: str
    optional: bool
    purposes: list[str] | None
    evidence_text: str

    @property
    def mapped(self) -> bool:
        return self.category is not None and self.data_type is not None


@dataclass(frozen=True)
class Practice:
    practice: LabelPractice
    value: bool
    evidence_text: str


@dataclass(frozen=True)
class ParsedLabel:
    status: Status  # ok or not_provided
    facts: list[Fact] = field(default_factory=list)
    practices: list[Practice] = field(default_factory=list)
    unmapped_statements: list[str] = field(default_factory=list)


def parse_datasafety_page(html: str) -> ParsedLabel:
    text = visible_text(html).lower()
    if NOT_PROVIDED_PHRASE in text:
        return ParsedLabel(status=Status.NOT_PROVIDED)

    try:
        datasets = extract_datasets(html)
    except PlayParseError as exc:
        raise LabelParseError(str(exc)) from None
    label = _find_label(datasets)
    shared = _facts(lookup(label, SHARED_PATH), LabelSection.SHARED)
    collected = _facts(lookup(label, COLLECTED_PATH), LabelSection.COLLECTED)
    practices, unmapped = _practices(lookup(label, PRACTICES_PATH))

    for phrase, practice, listed in (
        (NO_DATA_SHARED_PHRASE, LabelPractice.NO_DATA_SHARED, shared),
        (NO_DATA_COLLECTED_PHRASE, LabelPractice.NO_DATA_COLLECTED, collected),
    ):
        if phrase in text:
            if listed:
                raise LabelParseError(f"page says {phrase!r} but also lists such data")
            practices.append(Practice(practice, True, _capitalised(phrase)))

    if not (shared or collected or practices):
        raise LabelParseError("found the label data but no data types, headlines or practices")
    return ParsedLabel(
        status=Status.OK,
        facts=shared + collected,
        practices=practices,
        unmapped_statements=unmapped,
    )


def visible_text(html: str) -> str:
    """The page's text without scripts and styles, whitespace collapsed."""
    if not html.strip():
        return ""
    document = lxml.html.fromstring(html)
    for hidden in document.xpath("//script | //style | //noscript"):
        hidden.drop_tree()
    return " ".join(document.text_content().split())


def _find_label(datasets: dict[str, Any]) -> Any:
    """``ds:3`` per the Node library; Google renumbers blocks at times, so fall back to
    any block that has a label at the same position."""
    candidates = [datasets["ds:3"]] if "ds:3" in datasets else []
    candidates += [value for key, value in datasets.items() if key != "ds:3"]
    for candidate in candidates:
        label = lookup(candidate, LABEL_PATH)
        if isinstance(label, list):
            return label
    raise LabelParseError(f"no Data Safety label data at {list(LABEL_PATH)} in any ds:N block")


def _facts(entries: Any, section: LabelSection) -> list[Fact]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise LabelParseError(f"{section.value}: expected a list of categories")
    facts: list[Fact] = []
    for entry in entries:
        raw_category = lookup(entry, [0, 1])
        details = lookup(entry, [4])
        if not isinstance(raw_category, str) or not isinstance(details, list):
            raise LabelParseError(f"{section.value}: unexpected category entry shape")
        for detail in details:
            facts.append(_fact(section, raw_category, detail))
    return facts


def _fact(section: LabelSection, raw_category: str, detail: Sequence[Any]) -> Fact:
    raw_label = lookup(detail, [0])
    if not isinstance(raw_label, str) or not raw_label.strip():
        raise LabelParseError(f"{section.value}/{raw_category}: data type without a name")
    optional = bool(lookup(detail, [1]))
    raw_purposes = lookup(detail, [2])
    purposes = taxonomy.split_purposes(raw_purposes) if isinstance(raw_purposes, str) else None
    category, data_type = taxonomy.map_data_type(raw_category, raw_label)
    evidence = f"{SECTION_HEADINGS[section]} > {raw_category} > {raw_label}"
    if optional:
        evidence += " (Optional)"
    if isinstance(raw_purposes, str):
        evidence += f" | {raw_purposes}"
    return Fact(
        section=section,
        category=category,
        data_type=data_type if category is not None else None,
        raw_category=raw_category,
        raw_label=raw_label,
        optional=optional,
        purposes=purposes,
        evidence_text=evidence,
    )


def _practices(items: Any) -> tuple[list[Practice], list[str]]:
    if items is None:
        return [], []
    if not isinstance(items, list):
        raise LabelParseError("security practices: expected a list")
    found: dict[LabelPractice, Practice] = {}
    unmapped: list[str] = []
    for item in items:
        title = lookup(item, [1])
        if not isinstance(title, str):
            raise LabelParseError("security practice without a title")
        description = lookup(item, [2, 1])
        evidence = f"{title} | {description}" if isinstance(description, str) else title
        mapped = taxonomy.map_practice(title)
        if mapped is None:
            unmapped.append(evidence)
            continue
        practice, value = mapped
        if practice in found and found[practice].value != value:
            raise LabelParseError(f"contradictory statements about {practice.value}")
        found[practice] = Practice(practice, value, evidence)
    return list(found.values()), unmapped


def _capitalised(phrase: str) -> str:
    return phrase[:1].upper() + phrase[1:]
