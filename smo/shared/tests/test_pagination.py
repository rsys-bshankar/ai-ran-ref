"""smo_shared.pagination.paginate: a real LIMIT/OFFSET and a real COUNT, and the bounds every route's parameters declare."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

from smo_shared import pagination


class _Rows(DeclarativeBase):
    pass


class Item(_Rows):
    __tablename__ = "pagination_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String)


def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    _Rows.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([Item(id=i, label=f"item-{i}") for i in range(1, 8)])
    db.commit()
    return db


def test_a_page_is_a_slice_in_the_query_and_total_counts_the_whole_result():
    db = _session()
    page = pagination.paginate(db, select(Item).order_by(Item.id), limit=3, offset=2)
    assert [row.id for row in page["items"]] == [3, 4, 5]
    assert (page["total"], page["limit"], page["offset"]) == (7, 3, 2)


def test_total_follows_the_filter_not_the_page():
    db = _session()
    page = pagination.paginate(db, select(Item).where(Item.id > 4).order_by(Item.id), limit=1, offset=0)
    assert [row.id for row in page["items"]] == [5]
    assert page["total"] == 3


def test_an_offset_past_the_end_is_an_empty_page_with_the_real_total():
    page = pagination.paginate(_session(), select(Item).order_by(Item.id), limit=10, offset=50)
    assert page["items"] == [] and page["total"] == 7


def test_the_declared_bounds():
    assert (pagination.DEFAULT_LIMIT, pagination.MAX_LIMIT, pagination.MAX_OFFSET) == (100, 500, 2**31 - 1)
    assert pagination.PageLimit.dependency is pagination._page_limit and pagination.PageOffset.default == 0
    assert pagination._page_limit(limit=7, total=True) == 7


def test_an_unordered_statement_is_paged_in_primary_key_order():
    db = _session()
    sql = str(pagination._in_a_stable_order(select(Item)).compile())
    assert "ORDER BY pagination_item.id" in sql
    seen = []
    for offset in range(0, 7, 3):
        seen += [i.id for i in pagination.paginate(db, select(Item), limit=3, offset=offset)["items"]]
    assert seen == [1, 2, 3, 4, 5, 6, 7]                # every row on exactly one page


def test_a_statement_that_orders_itself_keeps_its_order():
    stmt = select(Item).order_by(Item.label.desc())
    assert pagination._in_a_stable_order(stmt) is stmt
    db = _session()
    assert [i.id for i in pagination.paginate(db, stmt, limit=2, offset=0)["items"]] == [7, 6]


def test_a_statement_that_selects_no_entity_is_left_alone():
    stmt = select(func.count()).select_from(Item)
    assert pagination._in_a_stable_order(stmt) is stmt


def _counting(db):
    statements: list[str] = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda conn, cur, stmt, *a: statements.append(stmt))
    return statements


def _no_total(limit):
    return pagination.PageSize(limit, with_total=False)


def test_total_false_issues_no_count_query_and_leaves_total_out():
    db = _session()
    statements = _counting(db)
    page = pagination.paginate(db, select(Item).order_by(Item.id), limit=_no_total(3), offset=2)
    assert [row.id for row in page["items"]] == [3, 4, 5]
    assert page == {"items": page["items"], "limit": 3, "offset": 2, "hasMore": True}
    assert len(statements) == 1 and "count(" not in statements[0].lower() and "LIMIT" in statements[0]
    pagination.paginate(db, select(Item), limit=3, offset=0)                # the default still counts
    assert any("count(" in s.lower() for s in statements[1:])


def test_total_false_fetches_one_extra_row_to_know_about_the_next_page():
    db = _session()
    assert pagination.paginate(db, select(Item), _no_total(7), 0)["hasMore"] is False       # exactly the last page
    assert pagination.paginate(db, select(Item), _no_total(6), 0)["hasMore"] is True
    last = pagination.paginate(db, select(Item), _no_total(3), 6)
    assert [i.id for i in last["items"]] == [7] and last["hasMore"] is False
    empty = pagination.paginate(db, select(Item), _no_total(3), 50)
    assert empty["items"] == [] and empty["hasMore"] is False


def test_total_false_pages_stay_in_primary_key_order_without_overlap():
    db = _session()
    seen, offset = [], 0
    while True:
        page = pagination.paginate(db, select(Item), _no_total(3), offset)
        seen += [i.id for i in page["items"]]
        if not page["hasMore"]:
            break
        offset += 3
    assert seen == [1, 2, 3, 4, 5, 6, 7]


def test_paginate_list_both_modes():
    rows = list(range(7))
    assert pagination.paginate_list(rows, 3, 2) == {"items": [2, 3, 4], "limit": 3, "offset": 2, "total": 7}
    assert pagination.paginate_list(rows, _no_total(3), 2) == {"items": [2, 3, 4], "limit": 3, "offset": 2, "hasMore": True}
    assert pagination.paginate_list(rows, _no_total(3), 4)["hasMore"] is False


def test_the_total_query_parameter_reaches_paginate_and_is_documented():
    db = _session()
    app = FastAPI()

    @app.get("/items")
    def items(limit: int = pagination.PageLimit, offset: int = pagination.PageOffset):
        page = pagination.paginate(db, select(Item).order_by(Item.id), limit, offset)
        return {**page, "items": [i.id for i in page["items"]]}

    client = TestClient(app)
    assert client.get("/items?limit=2").json() == {"items": [1, 2], "total": 7, "limit": 2, "offset": 0}
    assert client.get("/items?limit=2&total=true").json()["total"] == 7
    assert client.get("/items?limit=2&total=false").json() == {"items": [1, 2], "limit": 2, "offset": 0, "hasMore": True}
    assert client.get("/items?total=maybe").status_code == 422
    params = {p["name"]: p for p in app.openapi()["paths"]["/items"]["get"]["parameters"]}
    assert set(params) == {"limit", "total", "offset"} and params["total"]["schema"]["default"] is True and params["total"]["required"] is False
