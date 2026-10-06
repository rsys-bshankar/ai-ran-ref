"""smo_shared.pagination.paginate: a real LIMIT/OFFSET and a real COUNT, and the bounds every route's parameters declare."""

from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from smo_shared import pagination


class _Rows(DeclarativeBase):
    pass


class Item(_Rows):
    __tablename__ = "pagination_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String)


def _session():
    engine = create_engine("sqlite://")
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
    assert pagination.PageLimit.default == 100 and pagination.PageOffset.default == 0
