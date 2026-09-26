"""SQLite 적재. 사업자번호 PK 기반 UPSERT + 내용 해시 비교로 재실행해도 중복·불필요한 갱신이 생기지 않는다."""
import hashlib
import json
import sqlite3
from pathlib import Path

from .normalize import format_biz_no
from .resolve import GoldenRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS golden_records (
    biz_no          TEXT PRIMARY KEY,
    biz_no_display  TEXT NOT NULL,
    company_name    TEXT NOT NULL,
    contact_person  TEXT,
    phone           TEXT,
    address         TEXT,
    as_of           TEXT NOT NULL,
    sources         TEXT NOT NULL,
    status          TEXT NOT NULL,
    lineage         TEXT NOT NULL,
    record_hash     TEXT NOT NULL,
    first_run_id    INTEGER NOT NULL,
    last_changed_run_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conflicts (
    biz_no        TEXT NOT NULL,
    field         TEXT NOT NULL,
    resolution    TEXT NOT NULL,
    chosen_value  TEXT,
    chosen_source TEXT,
    reason        TEXT NOT NULL,
    candidates    TEXT NOT NULL,
    PRIMARY KEY (biz_no, field)
);
CREATE TABLE IF NOT EXISTS run_log (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    input_hash  TEXT NOT NULL,
    stats       TEXT NOT NULL
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def _row(g: GoldenRecord) -> dict:
    return {
        "biz_no": g.biz_no,
        "biz_no_display": format_biz_no(g.biz_no),
        "company_name": g.values.get("company_name"),
        "contact_person": g.values.get("contact_person"),
        "phone": g.values.get("phone"),
        "address": g.values.get("address"),
        "as_of": g.as_of.isoformat(),
        "sources": ",".join(g.sources),
        "status": g.status,
        "lineage": json.dumps(g.lineage, ensure_ascii=False, sort_keys=True),
    }


def record_hash(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def save(conn: sqlite3.Connection, goldens: list[GoldenRecord], run_started_at: str,
         input_hash: str, stats: dict) -> dict:
    """한 트랜잭션으로 적재. 실패하면 전부 롤백되어 반쯤 적재된 상태가 남지 않는다."""
    counts = {"inserted": 0, "updated": 0, "unchanged": 0}
    with conn:
        cur = conn.execute("INSERT INTO run_log (started_at, input_hash, stats) VALUES (?, ?, '{}')",
                           (run_started_at, input_hash))
        run_id = cur.lastrowid
        existing = dict(conn.execute("SELECT biz_no, record_hash FROM golden_records"))

        for g in goldens:
            row = _row(g)
            h = record_hash(row)
            old = existing.get(g.biz_no)
            if old == h:
                counts["unchanged"] += 1
                continue
            counts["inserted" if old is None else "updated"] += 1
            conn.execute(
                """INSERT INTO golden_records (biz_no, biz_no_display, company_name, contact_person, phone,
                       address, as_of, sources, status, lineage, record_hash, first_run_id, last_changed_run_id)
                   VALUES (:biz_no, :biz_no_display, :company_name, :contact_person, :phone,
                       :address, :as_of, :sources, :status, :lineage, :h, :run_id, :run_id)
                   ON CONFLICT(biz_no) DO UPDATE SET
                       biz_no_display=excluded.biz_no_display, company_name=excluded.company_name,
                       contact_person=excluded.contact_person, phone=excluded.phone,
                       address=excluded.address, as_of=excluded.as_of, sources=excluded.sources,
                       status=excluded.status, lineage=excluded.lineage, record_hash=excluded.record_hash,
                       last_changed_run_id=excluded.last_changed_run_id""",
                {**row, "h": h, "run_id": run_id},
            )

        # 충돌 테이블은 "현재 입력 기준 상태"를 나타내므로 이번 실행 결과로 통째로 교체
        conn.execute("DELETE FROM conflicts")
        conn.executemany(
            "INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(c.biz_no, c.field, c.resolution, c.chosen_value, c.chosen_source, c.reason,
              json.dumps(c.candidates, ensure_ascii=False)) for g in goldens for c in g.conflicts],
        )
        final_stats = {**stats, "db": counts, "run_id": run_id}
        conn.execute("UPDATE run_log SET stats = ? WHERE run_id = ?",
                     (json.dumps(final_stats, ensure_ascii=False), run_id))
    return final_stats
