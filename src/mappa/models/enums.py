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
    """How an app entered the candidate list (principle 3: record how it got in).

    ``dev_file`` marks the 20-app dev sample, so a dev run can never be mistaken for
    the search-based sample.
    """

    SEARCH = "search"
    CHART = "chart"
    SEED_FILE = "seed_file"
    DEV_FILE = "dev_file"


class StorePurpose(StrEnum):
    """What a data directory holds. Fixed when the directory is created, checked on every
    open, so synthetic test data and real evidence can never end up in the same store."""

    REAL = "real"
    SYNTHETIC = "synthetic"


class SampleMode(StrEnum):
    """Whether a snapshot is the full search-based sample or the small dev sample."""

    FULL = "full"
    DEV = "dev"


class InclusionBasis(StrEnum):
    """Why an included app is in the sample. The two random strata have different
    selection probabilities, so analysis has to know which stratum each app came from."""

    TOP_INSTALLS = "top_installs"
    RANDOM_LONG_TAIL = "random_long_tail"
    SEED = "seed"  # forced in by seed_apps.csv although the rules would not select it
    DEV_SAMPLE = "dev_sample"


class RunStatus(StrEnum):
    """Outcome of one CLI command run (the ``runs`` table)."""

    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


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
