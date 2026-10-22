"""Beta tags must never break weekly stable numbering or move beta users backwards."""
import pytest

from desktop.release_version import check_beta, next_tag, parse


@pytest.mark.parametrize("tag", ["main", "v1.2", "v01.2.3", "v1.2.3-beta.0", "v1.2.3-beta.01",
                                 "v1.2.3-rc.1", "v1.2.3-beta.1/other", "v1.2.3\n"])
def test_release_tags_are_strict(tag):
    with pytest.raises(ValueError):
        parse(tag)


def test_stable_numbering_ignores_betas_and_reuses_a_retry_tag():
    tags = ["v0.1.9", "v0.1.21", "v0.1.22-beta.9", "v0.2.0-beta.1", "other"]
    assert next_tag(tags, "stable") == "v0.1.22"
    assert next_tag(tags, "stable", ["v0.1.22-beta.9"]) == "v0.1.22"
    assert next_tag(tags + ["v0.1.22"], "stable", ["v0.1.22"]) == "v0.1.22"


def test_beta_numbering_increments_numerically_and_respects_future_versions():
    tags = ["v0.1.21", "v0.1.22-beta.9", "v0.1.22-beta.10"]
    assert next_tag(tags, "beta") == "v0.1.22-beta.11"
    assert next_tag(tags + ["v0.2.0-beta.1"], "beta") == "v0.2.0-beta.2"
    assert next_tag(tags + ["v0.1.22"], "beta") == "v0.1.23-beta.1"


def test_manual_beta_cannot_publish_stable_or_move_backwards():
    tags = ["v0.1.21", "v0.1.22-beta.10"]
    for tag in ("v0.1.22", "v0.1.21-beta.11", "v0.1.22-beta.9"):
        with pytest.raises(ValueError):
            check_beta(tag, tags)
    assert check_beta("v0.1.22-beta.10", tags) == "v0.1.22-beta.10"  # retry
    assert check_beta("v0.1.22-beta.11", tags) == "v0.1.22-beta.11"
