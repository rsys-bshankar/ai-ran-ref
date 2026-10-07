"""smo_shared.retention: the configured days, and a purge that deletes only the rows past the cutoff."""

import datetime

import pytest
from sqlalchemy import DateTime, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from smo_shared import retention

NOW = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)


class Base(DeclarativeBase):
    pass


class Row(Base):
    __tablename__ = "row"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String, default="a")


class Pair(Base):
    __tablename__ = "pair"
    a: Mapped[int] = mapped_column(Integer, primary_key=True)
    b: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for i, days in enumerate((400, 100, 31, 29, 1)):
            session.add(Row(id=i, at=NOW - datetime.timedelta(days=days), kind="a" if i % 2 else "b"))
        session.add(Row(id=99, at=None))
        session.commit()
        yield session


def ids(db):
    return sorted(r.id for r in db.scalars(select(Row)))


@pytest.mark.parametrize("value, days", [(None, 0), ("", 0), ("0", 0), ("30", 30), ("-5", 0), ("soon", 0), (" 7 ", 7)])
def test_the_configured_days(monkeypatch, value, days):
    monkeypatch.delenv("SMO_RETENTION_PM_FILES_DAYS", raising=False)
    if value is not None:
        monkeypatch.setenv("SMO_RETENTION_PM_FILES_DAYS", value)
    assert retention.retention_days("SMO_RETENTION_PM_FILES_DAYS") == days


def test_only_rows_older_than_the_cutoff_go_and_a_null_date_stays(db):
    assert retention.purge(db, Row, Row.at, 30, now=NOW) == 3
    assert ids(db) == [3, 4, 99]


def test_extra_conditions_narrow_the_purge(db):
    assert retention.purge(db, Row, Row.at, 30, where=[Row.kind == "a"], now=NOW) == 1
    assert ids(db) == [0, 2, 3, 4, 99]


@pytest.mark.parametrize("days", [0, -1])
def test_a_purge_with_no_age_is_refused(db, days):
    with pytest.raises(ValueError):
        retention.purge(db, Row, Row.at, days, now=NOW)
    assert len(ids(db)) == 6


def test_a_purge_larger_than_a_batch_removes_everything_due(db, monkeypatch):
    monkeypatch.setattr(retention, "BATCH", 2)
    assert retention.purge(db, Row, Row.at, 30, now=NOW) == 3
    assert ids(db) == [3, 4, 99]


def test_a_second_run_finds_nothing(db):
    retention.purge(db, Row, Row.at, 30, now=NOW)
    assert retention.purge(db, Row, Row.at, 30, now=NOW) == 0


def test_a_table_without_a_single_column_key_is_refused(db):
    db.add(Pair(a=1, b=1, at=NOW - datetime.timedelta(days=99)))
    db.commit()
    with pytest.raises(ValueError, match="single-column primary key"):
        retention.purge(db, Pair, Pair.at, 30, now=NOW)
