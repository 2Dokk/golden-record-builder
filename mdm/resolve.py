"""엔티티 매칭(사업자번호 기준 그룹핑)과 필드 단위 충돌 탐지·해결."""
from collections import defaultdict
from dataclasses import dataclass, field

from .config import FieldRule
from .extract import Record
from .normalize import company_name_key

AUTO_LATEST = "AUTO_LATEST"        # 기준일이 더 최근인 값 자동 채택
MANUAL_REVIEW = "MANUAL_REVIEW"    # 사람이 확인해야 함 (잠정값은 채워 둠)

_COMPARE = {
    "exact": lambda v: v,
    "phone": lambda v: v,   # 추출 단계에서 이미 형식 정규화됨
    "company_name": company_name_key,
}


@dataclass
class Conflict:
    biz_no: str
    field: str
    resolution: str
    chosen_value: str
    chosen_source: str
    reason: str
    candidates: list[dict]   # [{source, value, as_of}]


@dataclass
class GoldenRecord:
    biz_no: str
    values: dict[str, str | None]
    lineage: dict[str, str]          # 필드 → 값을 가져온 소스 id
    sources: list[str]
    as_of: object
    conflicts: list[Conflict] = field(default_factory=list)
    format_only_diffs: list[str] = field(default_factory=list)   # 표기만 달라 자동 흡수된 필드

    @property
    def status(self) -> str:
        return "NEEDS_REVIEW" if any(c.resolution == MANUAL_REVIEW for c in self.conflicts) else "OK"


def match(records: list[Record]) -> dict[str, list[Record]]:
    """정규화된 사업자번호가 같으면 같은 거래처로 본다."""
    groups: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        groups[r.biz_no].append(r)
    return dict(groups)


def resolve_group(biz_no: str, records: list[Record], rules: dict[str, FieldRule],
                  priority: dict[str, int]) -> GoldenRecord:
    # 기준일 최신 → 소스 우선순위 순으로 정렬. 맨 앞이 기본 채택 후보.
    ordered = sorted(records, key=lambda r: (-r.as_of.toordinal(), priority.get(r.source, 100)))
    golden = GoldenRecord(
        biz_no=biz_no, values={}, lineage={},
        sources=sorted({r.source for r in records}, key=lambda s: priority.get(s, 100)),
        as_of=ordered[0].as_of,
    )

    for fname, rule in rules.items():
        present = [r for r in ordered if r.get(fname)]
        if not present:
            golden.values[fname] = None
            continue
        chosen = present[0]
        golden.values[fname] = chosen.get(fname)
        golden.lineage[fname] = chosen.source

        key = _COMPARE[rule.compare]
        distinct_keys = {key(r.get(fname)) for r in present}
        if len(distinct_keys) == 1:
            if len({r.get(fname) for r in present}) > 1:
                golden.format_only_diffs.append(fname)
            continue

        candidates = [{"source": r.source, "value": r.get(fname), "as_of": r.as_of.isoformat()} for r in present]
        top = [r for r in present if r.as_of == chosen.as_of]
        if rule.on_conflict == "manual_review":
            resolution, reason = MANUAL_REVIEW, "정규화 후에도 값 불일치 - 자동 덮어쓰기 금지 필드"
        elif len({key(r.get(fname)) for r in top}) > 1:
            resolution, reason = MANUAL_REVIEW, "값 불일치 + 기준일 동일 - 최신값 판단 불가"
        else:
            resolution, reason = AUTO_LATEST, f"기준일 최신({chosen.as_of.isoformat()}) 소스 {chosen.source} 값 채택"
        golden.conflicts.append(Conflict(biz_no, fname, resolution, chosen.get(fname), chosen.source,
                                         reason, candidates))
    return golden
