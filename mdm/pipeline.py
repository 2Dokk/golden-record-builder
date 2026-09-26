"""전체 흐름: 추출·정규화 → 소스 내 중복 제거 → 매칭 → 충돌 해결 → 검증 → 적재 → 리포트."""
import csv
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from .config import load_config
from .extract import dedupe_within_source, extract
from .normalize import format_biz_no
from .resolve import AUTO_LATEST, MANUAL_REVIEW, match, resolve_group
from .store import connect, save

log = logging.getLogger("mdm")


class ReconciliationError(RuntimeError):
    """건수 대사(검증) 실패. 적재하지 않고 중단한다."""


def _input_hash(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def run(config_path: str | Path, db_path: str | Path, out_dir: str | Path | None = None) -> dict:
    cfg = load_config(config_path)
    started_at = datetime.now().isoformat(timespec="seconds")
    priority = {s.id: s.priority for s in cfg.sources}

    # 1) 추출 + 스키마 매핑 + 정규화
    per_source, all_valid, all_rejected, all_dropped = {}, [], [], []
    for src in cfg.sources:
        valid, rejected, n_rows = extract(src)
        deduped, dropped = dedupe_within_source(valid)
        per_source[src.id] = {
            "name": src.name, "input_rows": n_rows, "rejected": len(rejected),
            "intra_source_duplicates": len(dropped), "valid_unique": len(deduped),
        }
        log.info("[추출] %s(%s): 입력 %d행 → 거부 %d, 소스 내 중복 제거 %d → 유효 %d",
                 src.id, src.name, n_rows, len(rejected), len(dropped), len(deduped))
        for r in rejected:
            log.warning("  거부 %s#%d: %s", r.source, r.row, r.reason)
        all_valid += deduped
        all_rejected += rejected
        all_dropped += dropped

    # 2) 엔티티 매칭
    groups = match(all_valid)
    multi = {k: v for k, v in groups.items() if len({r.source for r in v}) > 1}
    single_by_source = {s.id: 0 for s in cfg.sources}
    for recs in groups.values():
        if len(recs) == 1:
            single_by_source[recs[0].source] += 1
    log.info("[매칭] 유효 레코드 %d건 → 고유 거래처 %d곳 (복수 소스 매칭 %d곳, 단일 소스 %s)",
             len(all_valid), len(groups), len(multi), single_by_source)

    # 3) 충돌 탐지·해결
    goldens = [resolve_group(k, v, cfg.fields, priority) for k, v in sorted(groups.items())]
    conflicts = [c for g in goldens for c in g.conflicts]
    auto = [c for c in conflicts if c.resolution == AUTO_LATEST]
    manual = [c for c in conflicts if c.resolution == MANUAL_REVIEW]
    format_only = sum(len(g.format_only_diffs) for g in goldens)
    matched_with_conflict = sum(1 for g in goldens if g.conflicts)
    needs_review = sum(1 for g in goldens if g.status == "NEEDS_REVIEW")
    log.info("[충돌] 표기 차이만 있는 필드 %d건(정규화로 흡수) / 실제 값 충돌 %d건 → 자동 해결 %d, 수동 검토 %d",
             format_only, len(conflicts), len(auto), len(manual))
    for c in conflicts:
        log.info("  %s %s.%s: %s", c.resolution, format_biz_no(c.biz_no), c.field,
                 " | ".join(f"{x['source']}={x['value']}({x['as_of']})" for x in c.candidates))

    # 4) 검증 (건수 대사) - 하나라도 어긋나면 적재 중단
    total_input = sum(s["input_rows"] for s in per_source.values())
    checks = {
        "입력 = 유효 + 거부 + 소스내중복": total_input == len(all_valid) + len(all_rejected) + len(all_dropped),
        "골든 레코드 수 = 고유 사업자번호 수": len(goldens) == len({r.biz_no for r in all_valid}),
        "골든 = 유효 - (매칭으로 합쳐진 레코드 - 매칭 그룹)":
            len(goldens) == len(all_valid) - (sum(len(v) for v in multi.values()) - len(multi)),
        "충돌 = 자동 해결 + 수동 검토": len(conflicts) == len(auto) + len(manual),
        "골든 레코드 필수값(사업자번호·회사명) 누락 없음": all(g.biz_no and g.values.get("company_name") for g in goldens),
    }
    for name, ok in checks.items():
        log.info("[검증] %s %s", "PASS" if ok else "FAIL", name)
    if not all(checks.values()):
        raise ReconciliationError([k for k, ok in checks.items() if not ok])

    stats = {
        "sources": per_source,
        "input_rows_total": total_input,
        "rejected_total": len(all_rejected),
        "intra_source_duplicates_total": len(all_dropped),
        "valid_records": len(all_valid),
        "golden_records": len(goldens),
        "matched_multi_source": len(multi),
        "single_source": single_by_source,
        "format_only_diffs": format_only,
        "conflicts_detected": len(conflicts),
        "conflicts_auto_resolved": len(auto),
        "conflicts_manual_review": len(manual),
        "matched_records_with_conflict": matched_with_conflict,
        "golden_records_needs_review": needs_review,
        "checks": checks,
    }

    # 5) 적재 (멱등)
    conn = connect(db_path)
    try:
        stats = save(conn, goldens, started_at, _input_hash(s.path for s in cfg.sources), stats)
        db_total = conn.execute("SELECT COUNT(*) FROM golden_records").fetchone()[0]
    finally:
        conn.close()
    stats["db_golden_total"] = db_total
    log.info("[적재] run_id=%d 신규 %d, 변경 %d, 변경없음 %d → DB 골든 레코드 총 %d건",
             stats["run_id"], stats["db"]["inserted"], stats["db"]["updated"], stats["db"]["unchanged"], db_total)

    if out_dir:
        _write_reports(Path(out_dir), goldens, conflicts, all_rejected, stats)
    return stats


def _write_reports(out: Path, goldens, conflicts, rejected, stats):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "golden_records.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["사업자등록번호", "회사명", "담당자", "전화번호", "주소", "기준일", "출처", "상태", "필드별출처"])
        for g in goldens:
            v = g.values
            w.writerow([format_biz_no(g.biz_no), v.get("company_name"), v.get("contact_person"), v.get("phone"),
                        v.get("address"), g.as_of.isoformat(), "+".join(g.sources), g.status,
                        json.dumps(g.lineage, ensure_ascii=False)])
    with open(out / "conflicts.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["사업자등록번호", "필드", "처리", "채택값", "채택소스", "사유", "후보값"])
        for c in conflicts:
            w.writerow([format_biz_no(c.biz_no), c.field, c.resolution, c.chosen_value, c.chosen_source, c.reason,
                        " | ".join(f"{x['source']}={x['value']}({x['as_of']})" for x in c.candidates)])
    with open(out / "rejected.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["소스", "행", "사유", "원본"])
        for r in rejected:
            w.writerow([r.source, r.row, r.reason, json.dumps(r.raw, ensure_ascii=False)])
    with open(out / "run_report.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
