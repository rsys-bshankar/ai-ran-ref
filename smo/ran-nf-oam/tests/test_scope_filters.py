"""PR-GUI-9.3 (OPEN_ITEMS.md GUI-9.3): the `region` and `site_cluster` filters of every list tied to managed elements, and the scope picker's
`GET /managed-entities/scopes`.

Covered, per list: a row tied to an element of the place is kept, one tied only to elements elsewhere (or to no registered element) is left out, the
two filters combine (both must match), the page `total` counts only what is kept, and the filter never widens the caller's scope claim (ADR 0005).
Rows tied to several elements (config jobs, approvals, decision records, software campaigns) match when ANY of their elements is in the place.
`/safeguard-refusals` takes no place filter (a refusal records no element): pinned so that a change to that is a deliberate one.

Fixtures: `client` and `db_session_factory` from `test_main.py` (a fresh in-memory SQLite per test, the app's session overridden); `places` below
registers four elements in known places and the rows of each list straight into the tables. The Postgres spelling of the JSON-list expansion
(`scoping.json_refs_in_place`) is not run here (SQLite only); its SQL is pinned by `test_json_list_expansion_compiles_for_postgres`.
Run with: `cd smo/ran-nf-oam && PYTHONPATH=.:../shared python -m pytest tests/test_scope_filters.py -q`.
"""

import datetime
import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from smo_shared.scope import SCOPE_HEADER

from app import scoping
from app.models import (Alarm, ElementOnboarding, ManagedEntity, O1AdaptorEndpoint, RAppActionApproval, RAppDecisionRecord, SafeguardRefusal,
                        SoftwareCampaign, WriteConfigJob, WriteConfigSubChange)

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

NOW = datetime.datetime.now(datetime.UTC)
EU_SCOPE = {"X-R1-Invoker-Id": "op", "X-R1-Role": "internal", SCOPE_HEADER: json.dumps({"regions": ["eu"]})}

# ME-1 eu/metro-a, ME-2 eu/metro-b, ME-3 us/metro-a, ME-4 no region, no cluster
PLACES = {"ME-1": ("eu", "metro-a"), "ME-2": ("eu", "metro-b"), "ME-3": ("us", "metro-a"), "ME-4": (None, None)}


def _guard(neighbours):
    """A cell guard with the given neighbour cell ids."""
    return {"cellClass": "CAPACITY", "neighbourRefs": neighbours}


@pytest.fixture
def places(db_session_factory):
    """The four elements of PLACES, each with an O1 endpoint, an onboarding row and one cell (`c1` .. `c4`; c1 lists c3 and c2 lists c1 as
    neighbours, c4 lists an unknown cell), plus for each multi-element list one row per element set named in its `ids` key."""
    ids: dict[str, str] = {}
    with db_session_factory() as db:
        for n, (ref, (region, cluster)) in enumerate(PLACES.items(), start=1):
            neighbours = {"ME-1": ["c3"], "ME-2": ["c1"], "ME-4": ["c-unknown"]}.get(ref, [])
            db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", region=region, site_cluster=cluster,
                                 cell_guards={f"c{n}": _guard(neighbours)}))
        db.flush()                    # the elements first: the rows below refer to them (a foreign key on Postgres; the models declare no relationship to order by)
        for n, ref in enumerate(PLACES, start=1):
            db.add(O1AdaptorEndpoint(managed_element_ref=ref, adaptor_uri=f"http://adaptor-{n}", protocol_support=["NETCONF"]))
            db.add(ElementOnboarding(managed_element_ref=ref, status="DISCOVERED"))
        db.flush()
        # config jobs: one on ME-1 and ME-3, one on ME-3 only, one on an unregistered element
        for name, refs in (("job-eu-us", ["ME-1", "ME-3"]), ("job-us", ["ME-3"]), ("job-ghost", ["ME-9"])):
            job = WriteConfigJob(job_id=uuid.uuid4(), requested_by="op", scope="managed-element", status="COMPLETED")
            db.add(job)
            db.flush()
            for ref in refs:
                db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=ref, attribute_changes={"a": 1}))
            ids[name] = str(job.job_id)
        # approvals and decision records: [ME-2], [ME-3, ME-4], [] (an action with no element)
        for n, (name, refs) in enumerate((("eu-b", ["ME-2"]), ("us", ["ME-3", "ME-4"]), ("none", []))):
            approval = RAppActionApproval(approval_id=uuid.uuid4(), invoker_id="rapp-1", requested_by="rapp-1", status="PENDING", request={},
                                          managed_elements=refs, change_count=len(refs), created_at=NOW - datetime.timedelta(minutes=n),
                                          expires_at=NOW + datetime.timedelta(hours=1))
            record = RAppDecisionRecord(decision_id=uuid.uuid4(), invoker_id="rapp-1", requested_by="rapp-1", disposition="DIRECT",
                                        occurred_at=NOW - datetime.timedelta(minutes=n), managed_elements=refs, change_count=len(refs),
                                        content_hash="0" * 64)
            db.add_all([approval, record])
            ids[f"approval-{name}"], ids[f"decision-{name}"] = str(approval.approval_id), str(record.decision_id)
        # software campaigns: named [ME-1], named [ME-3], selector region eu whose element has since moved to us (ME-3)
        for name, refs, selector in (("camp-eu", ["ME-1"], None), ("camp-us", ["ME-3"], None), ("camp-selector-eu", ["ME-3"], {"region": "eu"})):
            camp = SoftwareCampaign(campaign_id=uuid.uuid4(), name=name, requested_by="op", status="COMPLETED", elements=refs, selector=selector)
            db.add(camp)
            ids[name] = str(camp.campaign_id)
        db.add(SafeguardRefusal(invoker_id="rapp-1", code="RAPP_KILLED"))
        for ref, severity in (("ME-1", "critical"), ("ME-2", "major"), ("ME-3", "critical"), ("ME-4", "minor")):
            db.add(Alarm(alarm_id=uuid.uuid4(), source_alarm_id=f"s-{ref}", managed_element_ref=ref, severity=severity, raised_at=NOW,
                         ack_state="UNACKNOWLEDGED"))
        db.commit()
    return ids


def _get(client, path, headers=None, **params):
    """GET `path` with `params`, asserting 200; the JSON answer."""
    resp = client.get(path, params=params, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- the scope picker

def test_scopes_groups_regions_and_clusters_with_null_last(client, places):
    """The picker sees every region with its clusters and counts, names sorted and the unset (`null`) group last, in one answer."""
    body = _get(client, "/managed-entities/scopes")
    assert body == {"regions": [
        {"region": "eu", "elements": 2, "siteClusters": [{"siteCluster": "metro-a", "elements": 1}, {"siteCluster": "metro-b", "elements": 1}]},
        {"region": "us", "elements": 1, "siteClusters": [{"siteCluster": "metro-a", "elements": 1}]},
        {"region": None, "elements": 1, "siteClusters": [{"siteCluster": None, "elements": 1}]}]}


def test_scopes_follow_the_callers_scope_claim(client, places):
    """A scoped caller's picker offers only the places it may see: a region outside its claim must not even be named."""
    body = _get(client, "/managed-entities/scopes", headers=EU_SCOPE)
    assert [r["region"] for r in body["regions"]] == ["eu"]


def test_scopes_route_is_not_taken_for_an_element_reference(client, places, db_session_factory):
    """`scopes` is matched before `GET /managed-entities/{ref}`: an element literally named `scopes` must not shadow the picker."""
    with db_session_factory() as db:
        db.add(ManagedEntity(managed_element_ref="scopes", entity_type="O-DU", o1_protocol="NETCONF", region="eu"))
        db.commit()
    assert _get(client, "/managed-entities/scopes")["regions"][0]["elements"] == 3


# ---------------------------------------------------------------- one-element lists

# (path, the field naming the row's element)
SINGLE_ELEMENT_LISTS = [("/o1-adaptor-endpoints", "managedElementRef"), ("/element-onboarding", "managedElementRef"),
                        ("/cell-guards", "managedElementRef")]


@pytest.mark.parametrize(("path", "field"), SINGLE_ELEMENT_LISTS)
def test_single_element_lists_filter_by_region_and_cluster(client, places, path, field):
    """Each list keeps exactly the rows of the elements in the place; region and cluster combine, and `total` counts only what is kept."""
    eu = _get(client, path, region="eu")
    assert sorted(i[field] for i in eu["items"]) == ["ME-1", "ME-2"] and eu["total"] == 2
    metro_a = _get(client, path, site_cluster="metro-a")
    assert sorted(i[field] for i in metro_a["items"]) == ["ME-1", "ME-3"]
    both = _get(client, path, region="eu", site_cluster="metro-a")
    assert [i[field] for i in both["items"]] == ["ME-1"] and both["total"] == 1
    assert _get(client, path, region="nowhere")["items"] == []


@pytest.mark.parametrize(("path", "field"), SINGLE_ELEMENT_LISTS)
def test_single_element_lists_never_widen_the_scope_claim(client, places, path, field):
    """A filter naming a region outside the caller's claim answers an empty list, not the rows of that region."""
    assert _get(client, path, headers=EU_SCOPE, region="us")["items"] == []
    assert sorted(i[field] for i in _get(client, path, headers=EU_SCOPE, site_cluster="metro-a")["items"]) == ["ME-1"]


def test_place_filter_values_are_bounded(client, places):
    """An empty or over-long value is a 422, not a filter that silently matches nothing or everything."""
    assert client.get("/o1-adaptor-endpoints", params={"region": ""}).status_code == 422
    assert client.get("/o1-adaptor-endpoints", params={"site_cluster": "x" * 101}).status_code == 422


# ---------------------------------------------------------------- lists whose rows touch several elements

def test_config_jobs_match_when_any_target_element_is_in_the_place(client, places):
    """A job is in a place when any of its sub-changes targets an element there; a job on an unregistered element is in none."""
    eu = {i["jobId"] for i in _get(client, "/config-jobs", region="eu")["items"]}
    assert eu == {places["job-eu-us"]}
    us = {i["jobId"] for i in _get(client, "/config-jobs", region="us")["items"]}
    assert us == {places["job-eu-us"], places["job-us"]}
    assert _get(client, "/config-jobs", region="us", site_cluster="metro-b")["items"] == []
    assert _get(client, "/config-jobs")["total"] == 3


def test_config_jobs_place_filter_combines_with_the_scope_claim(client, places):
    """The scope rule (every element inside the claim) still applies: a job touching eu and us is hidden from an eu caller even with region=eu."""
    assert _get(client, "/config-jobs", headers=EU_SCOPE, region="eu")["items"] == []


@pytest.mark.parametrize("path", ["/rapp-approvals", "/decision-records"])
def test_approvals_and_decisions_match_any_element_of_the_change(client, places, path):
    """An approval or a decision record is in a place when any element it recorded is there; one with no element is in no place."""
    kind = "approval" if "approvals" in path else "decision"
    key = "approvalId" if kind == "approval" else "decisionId"
    assert [i[key] for i in _get(client, path, region="eu")["items"]] == [places[f"{kind}-eu-b"]]
    assert [i[key] for i in _get(client, path, region="us")["items"]] == [places[f"{kind}-us"]]
    assert _get(client, path, region="eu", site_cluster="metro-a")["total"] == 0
    assert _get(client, path, site_cluster="metro-b")["total"] == 1
    assert _get(client, path)["total"] == 3


def test_decision_records_keyset_paging_keeps_the_place_filter(client, places):
    """The keyset form (`after`, what the export jobs page with) applies the filter on every page, not only the first."""
    body = _get(client, "/decision-records", after="", limit=1, site_cluster="metro-a")
    assert [i["decisionId"] for i in body["items"]] == [places["decision-us"]] and body["hasMore"] is False


def test_decision_export_takes_the_place_filter(client, places):
    """The CSV export narrows like the list, so an export of one region holds only its records."""
    resp = client.get("/decision-records/export.csv", params={"since": (NOW - datetime.timedelta(days=1)).isoformat(), "region": "us"})
    assert resp.status_code == 200
    assert places["decision-us"] in resp.text and places["decision-eu-b"] not in resp.text


def test_software_campaigns_match_elements_or_the_selector_region(client, places):
    """A campaign is in a place when any of its elements is; with `region` alone a campaign whose selector chose that region is too."""
    eu = {i["campaignId"] for i in _get(client, "/software-campaigns", region="eu")["items"]}
    assert eu == {places["camp-eu"], places["camp-selector-eu"]}
    us = {i["campaignId"] for i in _get(client, "/software-campaigns", region="us")["items"]}
    assert us == {places["camp-us"], places["camp-selector-eu"]}
    # with a cluster the selector's region cannot vouch for the cluster: elements only
    eu_a = {i["campaignId"] for i in _get(client, "/software-campaigns", region="eu", site_cluster="metro-a")["items"]}
    assert eu_a == {places["camp-eu"]}


def test_safeguard_refusals_take_no_place_filter(client, places):
    """A refusal records no element, so it cannot be placed: the list is fleet-wide and does not advertise the filter (GUI-9.3)."""
    params = {p["name"] for p in client.get("/openapi.json").json()["paths"]["/safeguard-refusals"]["get"]["parameters"]}
    assert "region" not in params and "site_cluster" not in params


# ---------------------------------------------------------------- topology and the alarm / fleet aggregates

def test_topology_links_keep_links_with_either_end_in_the_place(client, places):
    """A neighbour relation is in a place when its a-side or its b-side element is there; the counts agree with the list."""
    links = _get(client, "/topology/links", region="us")["items"]
    assert {(link["aElement"], link["bElement"]) for link in links} == {("ME-1", "ME-3")}
    eu_links = _get(client, "/topology/links", region="eu")["items"]
    assert {(link["aElement"], link["bElement"]) for link in eu_links} == {("ME-1", "ME-3"), ("ME-2", "ME-1")}
    counts = _get(client, "/topology/links/counts", region="eu")
    assert counts["total"] == 2 and counts["interElement"] == 2
    assert _get(client, "/topology/links/counts")["total"] == 3
    assert _get(client, "/topology/links/counts", site_cluster="metro-b")["total"] == 1


def test_alarm_reads_take_site_cluster(client, places):
    """`/alarms`, `/alarms/counts` and `/alarms/stats` narrow by site cluster, alone or with the region."""
    assert sorted(a["managedElementRef"] for a in _get(client, "/alarms", site_cluster="metro-a")["items"]) == ["ME-1", "ME-3"]
    assert [a["managedElementRef"] for a in _get(client, "/alarms", site_cluster="metro-a", region="us")["items"]] == ["ME-3"]
    groups = _get(client, "/alarms/counts", group_by="severity", site_cluster="metro-a")["groups"]
    assert groups == [{"key": "critical", "count": 2}]
    assert _get(client, "/alarms/stats", site_cluster="metro-b")["open"] == 1
    assert _get(client, "/alarms/stats", region="eu", site_cluster="metro-a")["open"] == 1


def test_fleet_health_and_worst_take_site_cluster(client, places):
    """The health map and the worst-element ranking cover only the elements of the cluster asked for."""
    health = _get(client, "/managed-entities/health", site_cluster="metro-a")
    assert sum(g["elements"] for g in health["groups"]) == 2 and health["healthScore"] == 0.0
    worst = _get(client, "/managed-entities/worst", site_cluster="metro-b")
    assert [w["managedElementRef"] for w in worst] == ["ME-2"]


def test_json_list_expansion_compiles_for_postgres():
    """The Postgres spelling expands a JSON (not JSONB) list with a derived column name; SQLite's `json_each` would not exist there."""
    class _Bind:
        # stands in for `db.get_bind()`: only the dialect name is read
        dialect = postgresql.dialect()

    class _Db:
        # stands in for a Session on Postgres
        def get_bind(self):
            return _Bind()

    condition = scoping.json_refs_in_place(_Db(), RAppDecisionRecord.managed_elements, "eu", None)
    sql = str(select(RAppDecisionRecord.decision_id).where(condition).compile(dialect=postgresql.dialect()))
    assert "json_array_elements_text(rapp_decision_record.managed_elements) AS place_ref(value)" in sql
    assert scoping.json_refs_in_place(_Db(), RAppDecisionRecord.managed_elements, None, None) is None
