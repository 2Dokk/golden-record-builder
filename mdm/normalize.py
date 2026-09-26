"""필드 정규화·검증 함수. 소스마다 다른 표기를 공통 형식으로 맞춘다."""
import re
from datetime import date, datetime

_BIZ_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)
_DATE_FORMATS = ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d")
_LEGAL_FORMS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사")


def normalize_biz_no(raw) -> str | None:
    """'601-81-02316', '6018102316', '601 81 02316' → '6018102316'. 10자리가 아니면 None."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits if len(digits) == 10 else None


def is_valid_biz_no(digits: str) -> bool:
    """사업자등록번호 검증번호(10번째 자리) 확인. 가중치 1,3,7,1,3,7,1,3,5."""
    if not digits or len(digits) != 10 or not digits.isdigit():
        return False
    d = [int(c) for c in digits]
    total = sum(x * w for x, w in zip(d, _BIZ_WEIGHTS)) + (d[8] * 5) // 10
    return (10 - total % 10) % 10 == d[9]


def format_biz_no(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def parse_date(raw) -> date | None:
    """'2025-03-10', '2025.03.10', '2024/3/5', '20250310' → date. 실패하면 None."""
    if raw is None:
        return None
    s = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_phone(raw) -> str | None:
    """'0511234567', '051-123-4567', '051 123 4567' → '051-123-4567'. 비었거나 자릿수 이상이면 None."""
    digits = re.sub(r"\D", "", str(raw or ""))
    if not 9 <= len(digits) <= 11:
        return None
    head_len = 2 if digits.startswith("02") else 3
    head, rest = digits[:head_len], digits[head_len:]
    return f"{head}-{rest[:-4]}-{rest[-4:]}"


def normalize_text(raw) -> str | None:
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    return s or None


def company_name_key(name: str) -> str:
    """비교용 키: 법인 표기((주)/㈜/주식회사 등)·공백·구두점을 제거. '(주)한빛정밀' == '한빛정밀 주식회사'."""
    return re.sub(r"[\s.\-·]", "", _LEGAL_FORMS.sub("", name)).lower()
