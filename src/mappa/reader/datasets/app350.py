"""APP-350 -> canonical format (task 02a, R1).

Reads the YAML annotation files straight out of ``APP-350_v1.1.zip``: nothing is
extracted or modified, so the raw download stays exactly as Alex placed it. Output:

- ``data-public/canonical/app350.jsonl``: one ``Policy`` per line;
- ``data-public/canonical/app350_split.json``: the official split.

Both live under the gitignored ``data-public/``: they contain policy text.

Label names: APP-350 writes a practice and its party as one name
(``Location_GPS_3rdParty``). We split it into ``(practice, party)``, because the
evaluation reports the two parties separately and the AI engine fills them in as
separate fields. ``SSO`` and ``Facebook_SSO`` carry no party in APP-350 (the README: data
is exchanged between the app developer and the sign-on provider). They are stored with
party ``first_party``, since the developer is the one receiving the data, and composing a
name back ignores the party for them.

The loader is strict: an unknown label, modality or split value stops the load rather
than being skipped. A silently dropped label would quietly change the answer key.
"""

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from mappa.reader.canonical import Label, Modality, Party, Policy, PracticeKey, Segment
from mappa.reader.datasets.split import Split

SOURCE_FILE = "APP-350_v1.1.zip"
CANONICAL_FILE = "app350.jsonl"
SPLIT_FILE = "app350_split.json"

DATA_TYPES: tuple[str, ...] = (
    "Contact",
    "Contact_Address_Book",
    "Contact_City",
    "Contact_E_Mail_Address",
    "Contact_Password",
    "Contact_Phone_Number",
    "Contact_Postal_Address",
    "Contact_ZIP",
    "Demographic",
    "Demographic_Age",
    "Demographic_Gender",
    "Identifier",
    "Identifier_Ad_ID",
    "Identifier_Cookie_or_similar_Tech",
    "Identifier_Device_ID",
    "Identifier_IMEI",
    "Identifier_IMSI",
    "Identifier_IP_Address",
    "Identifier_MAC",
    "Identifier_Mobile_Carrier",
    "Identifier_SIM_Serial",
    "Identifier_SSID_BSSID",
    "Location",
    "Location_Bluetooth",
    "Location_Cell_Tower",
    "Location_GPS",
    "Location_IP_Address",
    "Location_WiFi",
)
NO_PARTY: tuple[str, ...] = ("SSO", "Facebook_SSO")
PRACTICES: tuple[str, ...] = DATA_TYPES + NO_PARTY

# Practices with at least 10 positive (yes or conflict) test policies: the ones overall
# averages are computed over (DATA_APP350.md section 7). Fixed here, from R0's count,
# so that later milestones never need to read test labels to know what to score.
EVALUABLE_NAMES: tuple[str, ...] = (
    "Contact_1stParty",
    "Contact_Address_Book_1stParty",
    "Contact_City_1stParty",
    "Contact_E_Mail_Address_1stParty",
    "Contact_E_Mail_Address_3rdParty",
    "Contact_Password_1stParty",
    "Contact_Phone_Number_1stParty",
    "Contact_Postal_Address_1stParty",
    "Contact_ZIP_1stParty",
    "Demographic_1stParty",
    "Demographic_3rdParty",
    "Demographic_Age_1stParty",
    "Demographic_Age_3rdParty",
    "Demographic_Gender_1stParty",
    "Facebook_SSO",
    "Identifier_1stParty",
    "Identifier_Ad_ID_1stParty",
    "Identifier_Ad_ID_3rdParty",
    "Identifier_Cookie_or_similar_Tech_1stParty",
    "Identifier_Cookie_or_similar_Tech_3rdParty",
    "Identifier_Device_ID_1stParty",
    "Identifier_Device_ID_3rdParty",
    "Identifier_IMEI_1stParty",
    "Identifier_IP_Address_1stParty",
    "Identifier_IP_Address_3rdParty",
    "Identifier_MAC_1stParty",
    "Identifier_Mobile_Carrier_1stParty",
    "Location_1stParty",
    "Location_3rdParty",
    "Location_Bluetooth_1stParty",
    "Location_Cell_Tower_1stParty",
    "Location_GPS_1stParty",
    "Location_IP_Address_1stParty",
    "Location_WiFi_1stParty",
    "SSO",
)

_SUFFIX = {Party.FIRST: "_1stParty", Party.THIRD: "_3rdParty"}
_MODALITY = {"PERFORMED": Modality.PERFORMED, "NOT_PERFORMED": Modality.NOT_PERFORMED}
_SPLIT = {"TRAINING": "train", "VALIDATION": "validation", "TEST": "test"}


class App350Error(ValueError):
    """The raw data isn't in the shape DATA_APP350.md documents."""


def practice_keys() -> list[PracticeKey]:
    """All 58 APP-350 labels as (practice, party) keys, in a fixed order."""
    keys = [(practice, party) for practice in DATA_TYPES for party in (Party.FIRST, Party.THIRD)]
    return keys + [(practice, Party.FIRST) for practice in NO_PARTY]


def label_name(practice: str, party: Party) -> str:
    """(practice, party) -> the APP-350 name, e.g. ``Location_GPS_3rdParty``."""
    if practice in NO_PARTY:
        return practice
    if practice not in DATA_TYPES:
        raise App350Error(f"unknown APP-350 practice {practice!r}")
    return practice + _SUFFIX[party]


def parse_label_name(name: str) -> PracticeKey:
    """The APP-350 name -> (practice, party). Raises on anything not in the scheme."""
    if name in NO_PARTY:
        return (name, Party.FIRST)
    for party, suffix in _SUFFIX.items():
        if name.endswith(suffix) and name.removesuffix(suffix) in DATA_TYPES:
            return (name.removesuffix(suffix), party)
    raise App350Error(f"unknown APP-350 label {name!r}")


def evaluable_keys() -> list[PracticeKey]:
    return [parse_label_name(name) for name in EVALUABLE_NAMES]


def normalize_key(key: PracticeKey) -> PracticeKey:
    """Party carries no meaning for SSO practices: fold it to first_party."""
    practice, party = key
    return (practice, Party.FIRST) if practice in NO_PARTY else (practice, party)


def read_zip(zip_path: Path) -> tuple[list[Policy], Split]:
    """All policies and the official split from the APP-350 zip."""
    policies: list[Policy] = []
    splits: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    with zipfile.ZipFile(zip_path) as archive:
        for name, document in _annotation_documents(archive):
            policy, split_name = parse_document(document, source=name)
            policies.append(policy)
            splits[split_name].append(policy.policy_id)
    if not policies:
        raise App350Error(f"{zip_path}: no annotations/policy_*.yml files found")
    policies.sort(key=lambda p: int(p.policy_id))
    split = Split(
        source="official",
        seed=None,
        train=tuple(sorted(splits["train"], key=int)),
        validation=tuple(sorted(splits["validation"], key=int)),
        test=tuple(sorted(splits["test"], key=int)),
    )
    return policies, split


def read_zip_text(zip_path: Path, filename: str) -> str:
    """A top-level file of the dataset (e.g. ``features.yml``), read from the zip."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [
            n
            for n in archive.namelist()
            if "__MACOSX" not in n.split("/") and n.split("/")[-1] == filename
        ]
        if len(names) != 1:
            raise App350Error(f"{zip_path}: expected one {filename}, found {names}")
        return archive.read(names[0]).decode("utf-8")


def parse_document(document: dict[str, Any], *, source: str) -> tuple[Policy, str]:
    """One annotation file -> (Policy, split name)."""
    try:
        split = _SPLIT[document["policy_type"]]
        segments = tuple(_segment(raw, source) for raw in document["segments"])
        policy = Policy(
            policy_id=str(document["policy_id"]),
            segments=segments,
            contains_synthetic=bool(document["contains_synthetic"]),
        )
    except KeyError as exc:
        raise App350Error(f"{source}: missing or unknown value {exc}") from None
    return policy, split


def write_canonical(policies: list[Policy], split: Split, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    canonical, split_path = out_dir / CANONICAL_FILE, out_dir / SPLIT_FILE
    tmp = canonical.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for policy in policies:
            handle.write(policy.model_dump_json() + "\n")
    tmp.replace(canonical)
    split_path.write_text(json.dumps(split.model_dump(), indent=2) + "\n", encoding="utf-8")
    return canonical, split_path


def read_canonical(out_dir: Path) -> tuple[list[Policy], Split]:
    canonical, split_path = out_dir / CANONICAL_FILE, out_dir / SPLIT_FILE
    if not canonical.exists():
        raise App350Error(f"{canonical} not found: run `python -m mappa.reader load` first")
    with canonical.open(encoding="utf-8") as handle:
        policies = [Policy.model_validate_json(line) for line in handle if line.strip()]
    return policies, Split.model_validate_json(split_path.read_text(encoding="utf-8"))


def _annotation_documents(archive: zipfile.ZipFile) -> Iterator[tuple[str, dict[str, Any]]]:
    for name in sorted(archive.namelist()):
        parts = name.split("/")
        if "__MACOSX" in parts or len(parts) < 2 or parts[-2] != "annotations":
            continue
        if not (parts[-1].startswith("policy_") and parts[-1].endswith(".yml")):
            continue
        with archive.open(name) as raw:
            document = yaml.safe_load(io.TextIOWrapper(raw, encoding="utf-8"))
        if not isinstance(document, dict):
            raise App350Error(f"{name}: not a YAML mapping")
        yield name, document


def _segment(raw: dict[str, Any], source: str) -> Segment:
    labels: list[Label] = []
    for annotation in raw.get("annotations") or []:
        practice, party = parse_label_name(annotation["practice"])
        modality = _MODALITY.get(annotation["modality"])
        if modality is None:
            raise App350Error(f"{source}: unknown modality {annotation['modality']!r}")
        labels.append(Label(practice=practice, party=party, modality=modality))
    text = raw["segment_text"]
    if not isinstance(text, str):
        raise App350Error(f"{source}: segment {raw.get('segment_id')} has no text")
    return Segment(idx=int(raw["segment_id"]), text=text, labels=tuple(labels))
