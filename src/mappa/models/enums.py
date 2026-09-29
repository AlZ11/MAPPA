"""Closed vocabularies used in the database.

Each is a ``StrEnum`` so values are plain strings in SQL and JSON (easy to query from
DuckDB), and each column that uses one gets a CHECK constraint, so a value outside the
list cannot be stored even by a hand-written INSERT.
"""

from enum import StrEnum


class Status(StrEnum):
    """Outcome of trying to obtain one piece of evidence.

    Principle 4 (unknown != absent): each value means something different, and a
    failure is never recorded as an empty result.
    """

    OK = "ok"  # obtained
    NOT_PROVIDED = "not_provided"  # the developer gave nothing: a finding, not an error
    NOT_FOUND = "not_found"  # 404 / app removed from the store
    BLOCKED = "blocked"  # 403 / 429 / captcha: we back off and report, never bypass
    FAILED = "failed"  # any other error
    SKIPPED = "skipped"  # deliberately not attempted (e.g. no AndroZoo key yet)


class DiscoverySource(StrEnum):
    """How an app entered the candidate list (principle 3: record how it got in)."""

    SEARCH = "search"
    CHART = "chart"
    SEED_FILE = "seed_file"


class FetchKind(StrEnum):
    """What a logged request was for.

    ``search`` and ``chart`` are additions to the task's list so discovery requests are
    logged, resumable and backed by a blob like every other request.
    """

    SEARCH = "search"
    CHART = "chart"
    METADATA = "metadata"
    POLICY = "policy"
    DATASAFETY = "datasafety"
    APK = "apk"


class PolicyFormat(StrEnum):
    HTML = "html"
    PDF = "pdf"
    GDOC = "gdoc"
    OTHER = "other"


class LabelSection(StrEnum):
    """The two lists on a Google Play Data Safety page."""

    COLLECTED = "collected"
    SHARED = "shared"


class LabelPractice(StrEnum):
    """Yes/no statements on a Data Safety page outside the collected/shared lists."""

    ENCRYPTED_IN_TRANSIT = "encrypted_in_transit"
    DELETION_REQUEST = "deletion_request"
    FAMILIES_POLICY = "families_policy"
    INDEPENDENT_REVIEW = "independent_review"
    NO_DATA_COLLECTED = "no_data_collected"
    NO_DATA_SHARED = "no_data_shared"
