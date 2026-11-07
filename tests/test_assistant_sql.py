"""Ассистент руководителя: валидатор SQL модели и песочница только для чтения (cqa/features/assistant.py).
Без сети: модели здесь не вызываются."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cqa.features.assistant import (
    SQLFailed,
    SQLRejected,
    check_sql,
    open_sandbox,
    run_sql,
    validate_sql,
)
from cqa.ingest.service import ingest_records
from cqa.settings import get_settings

FIXTURE = Path(__file__).parent / "fixtures" / "ingest_mini.json"


@pytest.mark.parametrize("sql", [
    "DELETE FROM v_dialogs",
    "UPDATE v_dialogs SET final_total = 100",
    "INSERT INTO v_dialogs(uid) VALUES ('x')",
    "REPLACE INTO v_dialogs(uid) VALUES ('x')",
    "DROP TABLE v_dialogs",
    "CREATE TABLE x(a)",
    "PRAGMA query_only=OFF",
    "ATTACH DATABASE 'x.db' AS x",
    "VACUUM",
    "SELECT 1; SELECT 2",
    "SELECT * FROM v_dialogs; DELETE FROM conversations",
    "SELECT * FROM v_dialogs;; DROP TABLE conversations",
    "WITH x AS (SELECT 1) DELETE FROM v_dialogs",
    "WITH x AS (SELECT 1) SELECT * FROM x WHERE 1 IN (SELECT 1) UNION SELECT 1 FROM x; PRAGMA writable_schema=1",
    "SELECT 1 FROM v_dialogs WHERE (SELECT 1 FROM pragma_table_info('conversations'))",
    "SELECT * FROM conversations",
    "SELECT * FROM v_dialogs d JOIN messages m ON m.uid = d.uid",
    "SELECT * FROM v_dialogs, evaluations",
    "SELECT * FROM v_dialogs, v_criteria, criterion_results",
    "SELECT (SELECT COUNT(*) FROM criterion_results) AS n",
    "WITH x AS (SELECT * FROM signals) SELECT * FROM x",
    "SELECT * FROM main.v_dialogs",
    "SELECT * FROM temp.v_dialogs",
    "SELECT * FROM sqlite_master",
    "SELECT name FROM sqlite_temp_master",
    "SELECT load_extension('evil')",
    "SELECT * FROM v_dialogs WHERE uid = ?",
    "SELECT * FROM v_dialogs WHERE uid = :uid",
    "SELECT 'незакрытая строка",
    "SELECT * FROM v_dialogs /* незакрытый комментарий",
    "",
    "   ;  ",
    "BEGIN; SELECT 1",
    "SELECT * INTO backup FROM v_dialogs",
])
def test_validator_rejects(sql):
    with pytest.raises(SQLRejected):
        validate_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM v_dialogs",
    "select uid, final_total from v_dialogs where gross = 1 order by final_total limit 5;",
    "WITH t AS (SELECT uid FROM v_criteria WHERE criterion_id = '03' AND failed = 1) SELECT COUNT(*) FROM t",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n LIMIT 3) SELECT x FROM n",
    "WITH a(u) AS MATERIALIZED (SELECT uid FROM v_dialogs), b AS (SELECT * FROM a) SELECT * FROM b",
    "SELECT j.value AS category, COUNT(*) FROM v_dialogs d, json_each(d.gross_categories) j GROUP BY 1",
    "SELECT d.operator, q.text FROM v_dialogs AS d LEFT JOIN v_questions AS q ON q.uid = d.uid",
    "SELECT * FROM (SELECT uid FROM v_dialogs) x, v_appeals a WHERE a.uid = x.uid",
    "SELECT 'DELETE; DROP TABLE conversations' AS s, replace(uid, ':', '-') FROM v_dialogs",
    "SELECT uid FROM v_dialogs WHERE operator IS NOT DISTINCT FROM NULL",
    "SELECT \"uid\" FROM \"v_dialogs\" -- комментарий; DROP\n",
    "SELECT COUNT(*) AS appeals FROM v_appeals",
])
def test_validator_accepts(sql):
    out = validate_sql(sql)
    assert out and not out.endswith(";")


def test_check_sql_returns_cte_names():
    _sql, ctes = check_sql("WITH a AS (SELECT 1), \"B\"(x) AS (SELECT 2) SELECT * FROM a, b")
    assert ctes == {"a", "b"}


# ---------------------------------------------------------------- песочница

@pytest.fixture
def db(tmp_db):
    """MINI (3 диалога) + копия в AUTOTEST с parent_uid (мутанты)."""
    con = tmp_db
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["records"]
    ingest_records(con, "MINI", records, "file")
    ingest_records(con, "AUTOTEST", records, "autotest")
    con.execute("UPDATE conversations SET parent_uid = 'MINI:' || external_id WHERE group_id = 'AUTOTEST'")
    return con


def _counts(con) -> dict[str, int]:
    return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("conversations", "messages", "evaluations", "llm_calls")}


def test_sandbox_is_read_only(db):
    before = _counts(db)
    sb = open_sandbox(get_settings().db_path)
    try:
        assert {r[0] for r in sb.execute("SELECT uid FROM v_dialogs")} == {
            "MINI:mini-ru-chat", "MINI:mini-kz-chat", "MINI:mini-ru-call"}
        # даже в обход валидатора: запись, DDL, PRAGMA, ATTACH, основные таблицы — отказ SQLite
        for raw in ("DELETE FROM conversations", "UPDATE conversations SET language = 'XX'",
                    ("INSERT INTO llm_calls(ts, purpose, profile, provider, model, request_json, status)"
                     " VALUES ('t', 'p', 'p', 'p', 'm', '{}', 'ok')"),
                    "DROP TABLE messages", "CREATE TEMP TABLE z(a)", "DELETE FROM v_dialogs",
                    "PRAGMA query_only=OFF", "ATTACH DATABASE ':memory:' AS x",
                    "SELECT * FROM conversations", "SELECT COUNT(*) FROM messages",
                    "SELECT * FROM sqlite_master",
                    "WITH x AS (SELECT * FROM evaluations) SELECT * FROM x",
                    "WITH v_dialogs AS (SELECT * FROM conversations) SELECT * FROM v_dialogs"):
            with pytest.raises(sqlite3.DatabaseError):
                sb.execute(raw).fetchall()
        with pytest.raises(SQLRejected):
            run_sql(sb, "DELETE FROM conversations")
        assert run_sql(sb, "SELECT COUNT(*) AS n FROM v_dialogs")["rows"] == [{"n": 3}]
    finally:
        sb.close()
    assert _counts(db) == before


def test_sandbox_connection_is_mode_ro(db):
    """Даже без авторизатора соединение открыто только на чтение: основная БД не пишется."""
    sb = open_sandbox(get_settings().db_path)
    try:
        sb.set_authorizer(None)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            sb.execute("DELETE FROM conversations")
        assert sb.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        sb.close()


def test_run_sql_limit_and_timeout(db):
    sb = open_sandbox(get_settings().db_path)
    try:
        res = run_sql(sb, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n LIMIT 500) SELECT x FROM n")
        assert len(res["rows"]) == 200 and res["truncated"] is True and res["columns"] == ["x"]
        with pytest.raises(SQLFailed, match="остановлен"):
            run_sql(sb, "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n",
                    timeout=0.2)
        with pytest.raises(SQLFailed, match="no such column"):
            run_sql(sb, "SELECT nope FROM v_dialogs")
        # после таймаута соединение работает
        assert run_sql(sb, "SELECT COUNT(*) AS n FROM v_dialogs")["rows"][0]["n"] == 3
    finally:
        sb.close()


def test_sandbox_group_filter(db):
    sb = open_sandbox(get_settings().db_path, group="AUTOTEST")
    try:
        assert sb.execute("SELECT COUNT(*) FROM v_dialogs").fetchone()[0] == 0
    finally:
        sb.close()
