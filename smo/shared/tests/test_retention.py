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


# --- retention off: the row estimate, the gauge and the daily warning (DB-3.10) ---------------------------------------------------------

def _gauge(table):
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value("smo_retention_off_rows", {"table": table})


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(retention, "_warned", {})
    monkeypatch.delenv("SMO_RETENTION_WARN_ROWS", raising=False)


@pytest.mark.parametrize("value, limit", [(None, 1_000_000), ("", 1_000_000), ("0", 0), ("250", 250), ("-3", 0), ("many", 1_000_000)])
def test_the_warning_threshold(monkeypatch, value, limit):
    monkeypatch.delenv("SMO_RETENTION_WARN_ROWS", raising=False)
    if value is not None:
        monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", value)
    assert retention.warn_rows() == limit


def test_the_estimate_counts_rows_on_sqlite(db):
    assert retention.estimate_rows(db, Row) == 6


def test_the_postgres_estimate_reads_reltuples_without_scanning():
    seen = []
    value = [12345.0]

    class Bind:
        class dialect:                                                       # noqa: N801
            name = "postgresql"

    class Result:
        def scalar(self):
            return value[0]

    class Fake:
        def get_bind(self):
            return Bind

        def execute(self, statement, params=None):
            seen.append((str(statement), params))
            return Result()

    assert retention.estimate_rows(Fake(), Row) == 12345
    assert "pg_class" in seen[0][0] and "count" not in seen[0][0].lower()
    assert seen[0][1] == {"name": '"row"'}
    value[0] = -1.0                                                          # never analysed
    assert retention.estimate_rows(Fake(), Row) == 0


def test_a_large_table_with_retention_off_sets_the_gauge_and_warns_once_a_day(db, fresh, monkeypatch, caplog):
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "5")
    day = datetime.date(2026, 10, 1)
    with caplog.at_level("WARNING", logger="smo_shared.retention"):
        retention.report_retention_off(db, "row", Row, 0, today=day)
        retention.report_retention_off(db, "row", Row, 0, today=day)
        assert len(caplog.records) == 1 and "row" in caplog.text and "SMO_RETENTION_WARN_ROWS=5" in caplog.text
        retention.report_retention_off(db, "row", Row, 0, today=day + datetime.timedelta(days=1))
        assert len(caplog.records) == 2
    assert _gauge("row") == 6


def test_a_table_at_or_under_the_limit_does_not_warn(db, fresh, monkeypatch, caplog):
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "6")
    with caplog.at_level("WARNING", logger="smo_shared.retention"):
        retention.report_retention_off(db, "row_small", Row, 0)
    assert not caplog.records and _gauge("row_small") == 6


def test_a_limit_of_zero_never_warns(db, fresh, monkeypatch, caplog):
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "0")
    with caplog.at_level("WARNING", logger="smo_shared.retention"):
        retention.report_retention_off(db, "row_never", Row, 0)
    assert not caplog.records and _gauge("row_never") == 6


def test_with_retention_on_the_series_goes_and_nothing_is_warned(db, fresh, monkeypatch, caplog):
    monkeypatch.setenv("SMO_RETENTION_WARN_ROWS", "1")
    retention.report_retention_off(db, "row_on", Row, 0)
    assert _gauge("row_on") == 6
    caplog.clear()
    with caplog.at_level("WARNING", logger="smo_shared.retention"):
        retention.report_retention_off(db, "row_on", Row, 30)
    assert _gauge("row_on") is None and not caplog.records


def test_a_failed_estimate_never_raises(db, fresh, monkeypatch):
    def broken(*_):
        raise RuntimeError("down")
    monkeypatch.setattr(retention, "estimate_rows", broken)
    retention.report_retention_off(db, "row_err", Row, 0)
    assert _gauge("row_err") is None
