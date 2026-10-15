"""Made-up policies in the canonical shape. No APP-350 text is used anywhere in tests."""

from mappa.reader.canonical import Label, Modality, Party, Policy, Segment

P, N = Modality.PERFORMED, Modality.NOT_PERFORMED
FIRST, THIRD = Party.FIRST, Party.THIRD


def label(practice: str, party: Party = FIRST, modality: Modality = P) -> Label:
    return Label(practice=practice, party=party, modality=modality)


def policy(policy_id: str, *segments: tuple[str, tuple[Label, ...]]) -> Policy:
    return Policy(
        policy_id=policy_id,
        segments=tuple(
            Segment(idx=i, text=text, labels=labels) for i, (text, labels) in enumerate(segments)
        ),
    )
