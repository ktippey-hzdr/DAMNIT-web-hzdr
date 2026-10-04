from pathlib import Path

import pytest

from damnit_api.metadata.hzdr_paths import map_path, parse_path_map

MOUNT = "/home/tippey/mnt/bigdata"


def test_empty_spec_is_no_rules():
    assert parse_path_map("") == []
    assert parse_path_map("  ") == []


def test_parse_splits_rules_in_order():
    rules = parse_path_map(f"/bigdata={MOUNT}, Z:/bigdata={MOUNT}")
    assert rules == [("/bigdata", MOUNT), ("Z:/bigdata", MOUNT)]


@pytest.mark.parametrize("spec", ["nonsense", "=/x", "/x=", "/a=/b,oops"])
def test_parse_rejects_malformed_entries(spec):
    with pytest.raises(ValueError, match="path map"):
        parse_path_map(spec)


@pytest.mark.parametrize(
    "raw",
    [
        "/bigdata/HPLexp/nexus/c.nxs",
        "Z:/bigdata/HPLexp/nexus/c.nxs",
        "Z:\\bigdata\\HPLexp\\nexus\\c.nxs",
        "z:/bigdata/HPLexp/nexus/c.nxs",
    ],
)
def test_published_and_windows_forms_map_to_the_mount(raw):
    rules = parse_path_map(f"/bigdata={MOUNT},Z:/bigdata={MOUNT}")
    assert map_path(raw, rules) == Path(MOUNT) / "HPLexp/nexus/c.nxs"


def test_unmatched_path_is_unchanged():
    rules = parse_path_map(f"/bigdata={MOUNT}")
    assert map_path("/data/damnit/x.h5", rules) == Path("/data/damnit/x.h5")


def test_prefix_must_end_at_a_path_boundary():
    rules = parse_path_map(f"/bigdata={MOUNT}")
    assert map_path("/bigdata2/x.h5", rules) == Path("/bigdata2/x.h5")


def test_longest_prefix_wins():
    rules = parse_path_map("/bigdata=/a,/bigdata/HPLexp=/b")
    assert map_path("/bigdata/HPLexp/x.h5", rules) == Path("/b/x.h5")


def test_exact_prefix_maps_to_the_mount_itself():
    rules = parse_path_map(f"/bigdata={MOUNT}")
    assert map_path("/bigdata", rules) == Path(MOUNT)


def test_none_and_no_rules_pass_through():
    assert map_path(None, parse_path_map(f"/bigdata={MOUNT}")) is None
    assert map_path("/bigdata/x", []) == Path("/bigdata/x")
