from datetime import date

import pytest

from mdm.normalize import company_name_key, is_valid_biz_no, normalize_biz_no, normalize_phone, parse_date


@pytest.mark.parametrize("raw", ["601-81-02310", "6018102310", " 601 81 02310 "])
def test_사업자번호_하이픈_공백_유무와_무관하게_같은_키(raw):
    assert normalize_biz_no(raw) == "6018102310"


def test_사업자번호_자릿수_오류는_None():
    assert normalize_biz_no("60183166") is None


def test_사업자번호_검증번호():
    assert is_valid_biz_no("6018102310")
    assert not is_valid_biz_no("6018102311")


@pytest.mark.parametrize("raw", ["2024-03-05", "2024.03.05", "2024/3/5", "20240305"])
def test_날짜_형식_통일(raw):
    assert parse_date(raw) == date(2024, 3, 5)


def test_전화번호_형식_통일():
    assert normalize_phone("0511234567") == normalize_phone("051-123-4567") == "051-123-4567"
    assert normalize_phone("0234567890") == "02-3456-7890"
    assert normalize_phone("") is None


def test_회사명_법인표기_차이는_같은_키():
    assert company_name_key("(주)한빛정밀") == company_name_key("한빛정밀 주식회사") == company_name_key("㈜ 한빛정밀")
    assert company_name_key("대양기공") != company_name_key("대양테크")
