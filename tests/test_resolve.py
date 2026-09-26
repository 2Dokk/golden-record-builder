from datetime import date

from mdm.config import FieldRule
from mdm.extract import Record
from mdm.resolve import AUTO_LATEST, MANUAL_REVIEW, match, resolve_group

RULES = {
    "company_name": FieldRule("company_name", "manual_review"),
    "contact_person": FieldRule("exact", "latest"),
    "phone": FieldRule("phone", "latest"),
    "address": FieldRule("exact", "latest"),
}
PRIORITY = {"A": 1, "B": 2}
BIZ = "6018102310"


def rec(source, as_of, **kw):
    return Record(source=source, row=1, biz_no=kw.pop("biz_no", BIZ), company_name=kw.pop("company_name", "대성해운"),
                  as_of=as_of, **kw)


def test_정상_매칭_충돌없음_필드는_서로_보완():
    a = rec("A", date(2025, 3, 10), contact_person="김민수", phone="051-600-1001")
    b = rec("B", date(2023, 5, 2), address="부산 중구", phone="051-600-1001")
    groups = match([a, b])
    assert list(groups) == [BIZ]

    g = resolve_group(BIZ, groups[BIZ], RULES, PRIORITY)
    assert g.conflicts == []
    assert g.status == "OK"
    assert g.sources == ["A", "B"]
    assert g.values["contact_person"] == "김민수"   # A에만 있는 필드
    assert g.values["address"] == "부산 중구"        # B에만 있는 필드
    assert g.lineage["address"] == "B"


def test_충돌시_기준일이_최신인_값_채택():
    a = rec("A", date(2023, 11, 20), phone="051-710-2200")
    b = rec("B", date(2025, 2, 3), phone="051-710-3300")
    g = resolve_group(BIZ, [a, b], RULES, PRIORITY)

    [c] = g.conflicts
    assert (c.field, c.resolution, c.chosen_source) == ("phone", AUTO_LATEST, "B")
    assert g.values["phone"] == "051-710-3300"
    assert g.status == "OK"


def test_기준일이_같으면_최신값_판단불가로_수동검토():
    a = rec("A", date(2024, 8, 14), phone="055-632-1100")
    b = rec("B", date(2024, 8, 14), phone="055-632-1800")
    g = resolve_group(BIZ, [a, b], RULES, PRIORITY)

    [c] = g.conflicts
    assert c.resolution == MANUAL_REVIEW
    assert g.values["phone"] == "055-632-1100"   # 잠정값은 우선순위 높은 A
    assert g.status == "NEEDS_REVIEW"


def test_회사명_표기차이는_충돌아님_실제_상호차이는_수동검토():
    same = resolve_group(BIZ, [rec("A", date(2025, 1, 1), company_name="(주)한빛정밀"),
                               rec("B", date(2024, 1, 1), company_name="한빛정밀 주식회사")], RULES, PRIORITY)
    assert same.conflicts == [] and same.format_only_diffs == ["company_name"]

    diff = resolve_group(BIZ, [rec("A", date(2025, 1, 1), company_name="대양기공"),
                               rec("B", date(2024, 1, 1), company_name="대양테크")], RULES, PRIORITY)
    [c] = diff.conflicts
    assert (c.field, c.resolution) == ("company_name", MANUAL_REVIEW)


def test_매칭되지_않는_신규_거래처는_단독_골든레코드():
    a = rec("A", date(2025, 1, 1), phone="051-000-0000")
    new = rec("B", date(2024, 6, 21), biz_no="6018303405", company_name="(주)코스모마린")
    groups = match([a, new])
    assert len(groups) == 2

    g = resolve_group("6018303405", groups["6018303405"], RULES, PRIORITY)
    assert g.sources == ["B"] and g.conflicts == [] and g.status == "OK"
