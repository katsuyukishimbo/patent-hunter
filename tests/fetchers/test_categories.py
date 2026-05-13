from patent_hunter.fetchers.categories import (
    CATEGORY_CPC_PREFIXES,
    all_prefixes,
    category_of,
)


def test_each_category_has_prefixes():
    assert set(CATEGORY_CPC_PREFIXES) == {
        "kitchen",
        "pet_products",
        "cable_management",
        "household",
    }
    for prefixes in CATEGORY_CPC_PREFIXES.values():
        assert prefixes  # non-empty
        for p in prefixes:
            assert p == p.upper()  # CPC codes are uppercase
            assert 3 <= len(p) <= 4


def test_all_prefixes_dedupes():
    out = all_prefixes()
    assert len(out) == len(set(out))


def test_category_of_known_codes():
    assert category_of("A47J 27/00") == "kitchen"
    assert category_of("A01K-15/02") == "pet_products"
    assert category_of("H02G 3/04") == "cable_management"
    assert category_of("A47L 9/00") == "household"


def test_category_of_unknown_code():
    assert category_of("H04L 12/00") is None  # network comms, out of scope
    assert category_of("") is None
    assert category_of("XYZ") is None
