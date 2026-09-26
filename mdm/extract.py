"""소스 파일 읽기 → 공통 스키마로 변환 → 정규화·검증."""
import csv
import json
from dataclasses import dataclass, field

from .config import Source
from .normalize import is_valid_biz_no, normalize_biz_no, normalize_phone, normalize_text, parse_date


@dataclass
class Record:
    source: str
    row: int          # 소스 파일 내 행 번호 (1부터, 추적용)
    biz_no: str
    company_name: str
    as_of: object     # datetime.date
    contact_person: str | None = None
    phone: str | None = None
    address: str | None = None

    def get(self, name):
        return getattr(self, name, None)


@dataclass
class Rejected:
    source: str
    row: int
    reason: str
    raw: dict = field(default_factory=dict)


def read_rows(src: Source) -> list[dict]:
    if src.format == "csv":
        with open(src.path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    with open(src.path, encoding="utf-8") as f:
        data = json.load(f)
    return data[src.records_path] if src.records_path else data


def canonicalize(raw: dict, src: Source, row: int) -> Record | Rejected:
    mapped = {canon: raw.get(col) for col, canon in src.columns.items()}

    digits = normalize_biz_no(mapped.get("biz_no"))
    if digits is None:
        return Rejected(src.id, row, "사업자번호 자릿수 오류(10자리 아님)", raw)
    if not is_valid_biz_no(digits):
        return Rejected(src.id, row, "사업자번호 검증번호 불일치", raw)

    name = normalize_text(mapped.get("company_name"))
    if name is None:
        return Rejected(src.id, row, "회사명 누락", raw)

    as_of = parse_date(mapped.get("as_of"))
    if as_of is None:
        return Rejected(src.id, row, f"기준일 파싱 실패: {mapped.get('as_of')!r}", raw)

    return Record(
        source=src.id, row=row, biz_no=digits, company_name=name, as_of=as_of,
        contact_person=normalize_text(mapped.get("contact_person")),
        phone=normalize_phone(mapped.get("phone")),
        address=normalize_text(mapped.get("address")),
    )


def extract(src: Source) -> tuple[list[Record], list[Rejected], int]:
    """소스 하나를 읽어 (유효 레코드, 거부 레코드, 원본 행 수) 반환."""
    rows = read_rows(src)
    valid, rejected = [], []
    for i, raw in enumerate(rows, start=1):
        rec = canonicalize(raw, src, i)
        (rejected if isinstance(rec, Rejected) else valid).append(rec)
    return valid, rejected, len(rows)


def dedupe_within_source(records: list[Record]) -> tuple[list[Record], list[Record]]:
    """같은 소스 안에 같은 사업자번호가 여러 번 있으면 기준일이 가장 최근인 행만 남긴다."""
    latest: dict[str, Record] = {}
    dropped = []
    for r in records:
        cur = latest.get(r.biz_no)
        if cur is None:
            latest[r.biz_no] = r
        elif r.as_of > cur.as_of:
            dropped.append(cur)
            latest[r.biz_no] = r
        else:
            dropped.append(r)
    return list(latest.values()), dropped
