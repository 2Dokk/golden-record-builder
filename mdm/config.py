"""TOML 설정 로드. 소스 매핑과 필드 규칙을 코드와 분리해 둔다."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

CANONICAL_FIELDS = ("biz_no", "company_name", "contact_person", "phone", "address", "as_of")
COMPARATORS = ("exact", "company_name", "phone")
CONFLICT_POLICIES = ("latest", "manual_review")


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    path: Path
    format: str
    columns: dict[str, str]
    priority: int
    records_path: str | None = None


@dataclass(frozen=True)
class FieldRule:
    compare: str
    on_conflict: str


@dataclass(frozen=True)
class Config:
    sources: list[Source]
    fields: dict[str, FieldRule]


def load_config(path: str | Path) -> Config:
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    base = path.parent.parent  # config/ 의 상위 = 프로젝트 루트 기준 상대경로

    sources = []
    for s in raw["sources"]:
        unknown = set(s["columns"].values()) - set(CANONICAL_FIELDS)
        if unknown:
            raise ValueError(f"소스 {s['id']}: 공통 스키마에 없는 필드 {sorted(unknown)}")
        for required in ("biz_no", "company_name", "as_of"):
            if required not in s["columns"].values():
                raise ValueError(f"소스 {s['id']}: 필수 필드 '{required}' 매핑 누락")
        if s["format"] not in ("csv", "json"):
            raise ValueError(f"소스 {s['id']}: 지원하지 않는 형식 {s['format']}")
        src_path = Path(s["path"])
        sources.append(Source(
            id=s["id"], name=s.get("name", s["id"]),
            path=src_path if src_path.is_absolute() else base / src_path,
            format=s["format"], columns=dict(s["columns"]),
            priority=int(s.get("priority", 100)), records_path=s.get("records_path"),
        ))
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("소스 id 중복")

    fields = {}
    for name, rule in raw["fields"].items():
        if rule["compare"] not in COMPARATORS or rule["on_conflict"] not in CONFLICT_POLICIES:
            raise ValueError(f"필드 규칙 오류: {name} = {rule}")
        fields[name] = FieldRule(rule["compare"], rule["on_conflict"])
    return Config(sources=sources, fields=fields)
