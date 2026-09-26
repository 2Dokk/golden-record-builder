"""샘플 데이터 전체 실행 + 재실행 안전성 + 소스 추가 확장성."""
import json
import sqlite3
from pathlib import Path

import pytest

from mdm.pipeline import run

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.toml"


def golden_count(db):
    with sqlite3.connect(db) as c:
        return c.execute("SELECT COUNT(*), COUNT(DISTINCT biz_no) FROM golden_records").fetchone()


def test_샘플데이터_전체실행_검증항목_모두_통과(tmp_path):
    stats = run(CONFIG, tmp_path / "mdm.sqlite")
    assert all(stats["checks"].values())
    assert stats["golden_records"] == golden_count(tmp_path / "mdm.sqlite")[0]
    assert stats["conflicts_detected"] == stats["conflicts_auto_resolved"] + stats["conflicts_manual_review"]


def test_같은_입력을_다시_돌려도_중복_갱신_없음(tmp_path):
    db = tmp_path / "mdm.sqlite"
    first = run(CONFIG, db)
    second = run(CONFIG, db)

    assert first["db"]["inserted"] == first["golden_records"]
    assert second["db"] == {"inserted": 0, "updated": 0, "unchanged": first["golden_records"]}
    total, distinct = golden_count(db)
    assert total == distinct == first["golden_records"]


def _config_with_extra_source(tmp_path, extra_csv_rows: str) -> Path:
    """기존 설정을 복사하고 세 번째 소스(C: 구매시스템)를 설정만으로 추가한다."""
    csv_path = tmp_path / "system_c_purchase.csv"
    csv_path.write_text("업체명,사업자No,구매담당,갱신일자\n" + extra_csv_rows, encoding="utf-8")
    text = CONFIG.read_text(encoding="utf-8")
    # 상대경로를 절대경로로 바꿔 tmp 위치에서도 원본 데이터를 읽게 함
    text = text.replace('path = "data/', f'path = "{(ROOT / "data").as_posix()}/')
    text += f'''
[[sources]]
id = "C"
name = "구매시스템"
path = "{csv_path.as_posix()}"
format = "csv"
priority = 3
[sources.columns]
"업체명" = "company_name"
"사업자No" = "biz_no"
"구매담당" = "contact_person"
"갱신일자" = "as_of"
'''
    cfg = tmp_path / "config" / "sources.toml"
    cfg.parent.mkdir()
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_세번째_소스는_설정만_추가하면_기존_거래처와_매칭(tmp_path):
    base = run(CONFIG, tmp_path / "base.sqlite")
    # 대성해운(A·B 양쪽 존재)을 C에서도 담당자 변경된 최신값으로 보유
    cfg = _config_with_extra_source(tmp_path, "대성해운 주식회사,601-81-02310,김태오,2025-09-01\n")
    stats = run(cfg, tmp_path / "abc.sqlite")

    assert all(stats["checks"].values())
    assert stats["golden_records"] == base["golden_records"]          # 새 거래처가 아니라 기존과 매칭됨
    with sqlite3.connect(tmp_path / "abc.sqlite") as c:
        sources, contact, lineage = c.execute(
            "SELECT sources, contact_person, lineage FROM golden_records WHERE biz_no='6018102310'").fetchone()
    assert sources == "A,B,C"
    assert contact == "김태오" and json.loads(lineage)["contact_person"] == "C"


def test_입력이_바뀌면_해당_레코드만_변경으로_집계(tmp_path):
    db = tmp_path / "mdm.sqlite"
    run(CONFIG, db)
    cfg = _config_with_extra_source(tmp_path, "대성해운 주식회사,601-81-02310,김태오,2025-09-01\n")
    stats = run(cfg, db)
    assert stats["db"]["updated"] == 1
    assert stats["db"]["inserted"] == 0


@pytest.mark.parametrize("bad", ["업체X,601-81-0231,홍길동,2025-01-01\n", "업체Y,601-81-02311,홍길동,2025-01-01\n"],
                         ids=["자릿수부족", "검증번호불일치"])
def test_잘못된_사업자번호는_적재하지_않고_거부로_집계(tmp_path, bad):
    base = run(CONFIG, tmp_path / "base.sqlite")
    stats = run(_config_with_extra_source(tmp_path, bad), tmp_path / "x.sqlite")
    assert stats["sources"]["C"]["rejected"] == 1
    assert stats["golden_records"] == base["golden_records"]
