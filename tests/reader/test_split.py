import pytest
from pydantic import ValidationError

from mappa.reader.datasets.split import Split


def _split() -> Split:
    return Split(source="official", seed=None, train=("1", "2"), validation=("3",), test=("4",))


def test_cli_names_and_lookup() -> None:
    split = _split()
    assert split.ids("val") == split.ids("validation") == ("3",)
    assert split.of("4") == "test"
    with pytest.raises(KeyError):
        split.of("99")


def test_a_policy_in_two_splits_is_refused() -> None:
    with pytest.raises(ValidationError, match="more than one split"):
        Split(source="official", seed=None, train=("1",), validation=("1",), test=())
