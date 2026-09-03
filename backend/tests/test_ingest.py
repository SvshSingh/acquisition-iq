"""Bring-your-own-list ingest: column mapping and type coercion.

The mapping is the risky part — a wrong guess silently mis-scores a real lead —
so most of these pin down that headers resolve the way a SaaSquatch or CRM export
actually names them, and that unrecognised columns are reported rather than
dropped on the floor.
"""

from __future__ import annotations

import pytest

from app.pipeline.ingest import build_mapping, ingest_csv


def test_common_headers_map_to_canonical_fields():
    mapping, _ = build_mapping(
        ["Company Name", "Website URL", "Email", "Phone Number", "City", "Estimated Revenue"]
    )
    assert set(mapping.values()) == {"name", "website", "email", "phone", "city", "revenue_usd"}


def test_header_matching_ignores_case_and_punctuation():
    mapping, _ = build_mapping(["company_name", "E-MAIL", "Annual Revenue"])
    assert mapping["company_name"] == "name"
    assert mapping["E-MAIL"] == "email"
    assert mapping["Annual Revenue"] == "revenue_usd"


def test_unrecognised_columns_are_reported_not_dropped():
    _, unmapped = build_mapping(["Company", "Favourite Colour", "Zodiac Sign"])
    assert "Favourite Colour" in unmapped
    assert "Zodiac Sign" in unmapped


def test_a_field_is_claimed_once_so_a_second_column_cannot_overwrite_it():
    """Two columns both aliasing 'name' — the first wins, the second is unmapped
    rather than silently clobbering."""
    mapping, unmapped = build_mapping(["Company", "Business Name"])
    assert list(mapping.values()).count("name") == 1
    assert len(unmapped) == 1


def test_saasquatch_shaped_export_lights_up_buy_box():
    """The synergy claim: an imported list carrying size data makes buy_box
    measurable, which the public-source seed cannot."""
    csv_text = (
        "Company Name,Website,Employees,Estimated Revenue,City\n"
        "Whitaker HVAC,whitakerhvac.com,42,$5.2M,Glendale\n"
    )
    result = ingest_csv(csv_text)
    assert result.row_count == 1
    c = result.companies[0]
    assert c.employee_count == 42
    assert c.revenue_usd == 5_200_000
    assert c.website == "https://whitakerhvac.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("$5.2M", 5_200_000), ("1,250,000", 1_250_000), ("900K", 900_000), ("2B", 2_000_000_000)],
)
def test_revenue_parsing_handles_the_formats_crms_actually_use(raw, expected):
    # Quoted, because a real CSV quotes any field containing a comma — that is
    # how "1,250,000" survives the delimiter in an Excel or CRM export.
    result = ingest_csv(f'Company,Revenue\nAcme,"{raw}"\n')
    assert result.companies[0].revenue_usd == expected


def test_employee_band_takes_the_upper_bound():
    """CRM size fields are often bands like '11-50'; the ceiling is what a
    buy-box check needs."""
    result = ingest_csv("Company,Company Size\nAcme,11-50\n")
    assert result.companies[0].employee_count == 50


def test_rows_without_a_name_are_skipped_not_fatal():
    result = ingest_csv("Company,City\n,Glendale\nRealCo,Burbank\n")
    assert result.row_count == 1
    assert result.skipped_rows == 1
    assert result.companies[0].name == "RealCo"


def test_empty_or_headerless_file_yields_nothing_gracefully():
    assert ingest_csv("").row_count == 0
    assert ingest_csv("just,some,headers\n").row_count == 0
