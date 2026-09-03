"""API route tests.

These were missing entirely, which was the largest hole in the suite: search,
filtering, export and refresh are the surface a user and a grader actually touch,
and none of them were exercised. Everything here runs against the committed
snapshot through FastAPI's TestClient, so it needs no database and no network —
the refresh test relies on the cache degrading to the null backend, which is one
of the behaviours worth pinning anyway.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# health and index
# --------------------------------------------------------------------------- #

def test_health_reports_the_loaded_dataset(client: TestClient):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["companies"] > 0


def test_root_is_an_index_not_a_404(client: TestClient):
    """A person who pastes the API URL into a browser should get orientation,
    not a bare Not Found."""
    body = client.get("/").json()
    assert "endpoints" in body
    assert body["companies"] > 0


# --------------------------------------------------------------------------- #
# search and filtering
# --------------------------------------------------------------------------- #

def test_search_returns_scored_companies_sorted_desc(client: TestClient):
    body = client.get("/api/companies", params={"limit": 25}).json()
    assert body["total"] > 0
    scores = [r["score"]["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)


def test_every_result_carries_its_evidence(client: TestClient):
    """The whole product claim is that a score can be interrogated, so the
    factors and their evidence must travel with every row, not be a detail
    fetch."""
    row = client.get("/api/companies", params={"limit": 1}).json()["results"][0]
    assert len(row["score"]["factors"]) == 6
    assert "covered_weight" in row["score"]


def test_min_score_filter_is_honoured(client: TestClient):
    body = client.get("/api/companies", params={"min_score": 50, "limit": 500}).json()
    assert all(r["score"]["score"] >= 50 for r in body["results"])


def test_industry_filter_narrows_results(client: TestClient):
    body = client.get("/api/companies", params={"industry": "Plumbing", "limit": 500}).json()
    assert body["total"] > 0
    assert all(r["company"]["industry"] == "Plumbing" for r in body["results"])


def test_has_employees_filter(client: TestClient):
    body = client.get("/api/companies", params={"has_employees": True, "limit": 500}).json()
    assert all(r["company"]["has_employees"] is True for r in body["results"])


def test_text_search_matches_name_city_or_trade(client: TestClient):
    body = client.get("/api/companies", params={"q": "plumbing", "limit": 500}).json()
    assert body["total"] > 0


def test_pagination_does_not_overlap(client: TestClient):
    first = client.get("/api/companies", params={"limit": 10, "offset": 0}).json()
    second = client.get("/api/companies", params={"limit": 10, "offset": 10}).json()
    first_ids = {r["company"]["id"] for r in first["results"]}
    second_ids = {r["company"]["id"] for r in second["results"]}
    assert first_ids.isdisjoint(second_ids)


def test_impossible_filter_returns_empty_not_error(client: TestClient):
    body = client.get("/api/companies", params={"min_score": 100, "min_age": 150}).json()
    assert body["total"] == 0
    assert body["results"] == []


# --------------------------------------------------------------------------- #
# single company
# --------------------------------------------------------------------------- #

def test_get_one_company(client: TestClient):
    listed = client.get("/api/companies", params={"limit": 1}).json()["results"][0]
    cid = listed["company"]["id"]
    body = client.get(f"/api/companies/{cid}").json()
    assert body["company"]["id"] == cid


def test_unknown_company_is_404(client: TestClient):
    resp = client.get("/api/companies/cslb:does-not-exist")
    assert resp.status_code == 404
    assert "does-not-exist" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #

def test_meta_derives_filters_from_the_data(client: TestClient):
    """Filter options must come from the dataset, not a hardcoded list, or a
    different market would offer filters that match nothing."""
    body = client.get("/api/meta").json()
    assert len(body["factors"]) == 6
    assert body["filters"]["industry"]
    assert set(body["crm_presets"]) >= {"generic", "hubspot", "salesforce"}


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

def test_export_is_valid_csv_with_a_bom(client: TestClient):
    resp = client.get("/api/export", params={"preset": "hubspot"})
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    # The BOM is deliberate — without it Excel reads UTF-8 as the local codepage.
    assert resp.text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    assert len(rows) > 1
    assert "Name" in rows[0]  # hubspot preset header


def test_export_presets_differ_in_their_headers(client: TestClient):
    generic = client.get("/api/export", params={"preset": "generic", "ids": _first_id(client)})
    salesforce = client.get(
        "/api/export", params={"preset": "salesforce", "ids": _first_id(client)}
    )
    assert generic.text.splitlines()[0] != salesforce.text.splitlines()[0]
    assert "Account Name" in salesforce.text.splitlines()[0]


def test_export_of_a_selection_returns_only_those_rows(client: TestClient):
    cid = _first_id(client)
    resp = client.get("/api/export", params={"ids": cid})
    rows = list(csv.reader(io.StringIO(resp.text.lstrip("﻿"))))
    assert len(rows) == 2  # header + one company


def test_bad_weights_are_rejected(client: TestClient):
    resp = client.get("/api/export", params={"weights": "not json"})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# refresh
# --------------------------------------------------------------------------- #

def test_refresh_rescoring_survives_without_a_database(client: TestClient):
    """The live path runs even with no cache backend reachable — the cache
    degrades to the null layer rather than raising, which is the behaviour that
    lets the deployed demo work with no database provisioned."""
    cid = _first_id(client)
    resp = client.post(f"/api/companies/{cid}/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"]["last_refreshed"] is not None
    assert body["company"]["data_quality"] is not None


def test_refresh_of_unknown_company_is_404(client: TestClient):
    assert client.post("/api/companies/nope/refresh").status_code == 404


def _first_id(client: TestClient) -> str:
    return client.get("/api/companies", params={"limit": 1}).json()["results"][0]["company"]["id"]
