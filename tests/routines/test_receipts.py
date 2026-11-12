"""A receipt's date, shop and amount: plain strict rules, and an empty value rather than a guess."""
from __future__ import annotations

from core.routines.receipts import read_amount, read_date, read_shop, read_values, values_from_text


def test_dates_are_read_only_when_they_cannot_be_misread():
    assert read_date("Paid 2026-11-03 at 10:14") == "2026-11-03"
    assert read_date("2026.11.3") == "2026-11-03"
    assert read_date("Datum: 03.11.2026") == "2026-11-03"  # dots are always day first
    assert read_date("13/11/2026") == "2026-11-13"
    assert read_date("11/13/2026") == "2026-11-13"
    assert read_date("05/05/2026") == "2026-05-05"
    assert read_date("03/11/2026") == ""  # 3 November or 11 March: left for the person
    assert read_date("2026-02-30") == ""
    assert read_date("no date here 12345") == ""
    assert read_date("printed 2026-11-04, sold 2026-11-03") == "2026-11-04"  # the first one on the page


def test_the_amount_is_the_total_line_else_the_largest():
    assert read_amount("Bread 2.10\nMilk 1.05\nSubtotal 3.15\nTOTAL 3.15 EUR\nCash 5.00") == "3.15"
    assert read_amount("Summe 1.234,56 €") == "1234.56"
    assert read_amount("Total: 1,234.56") == "1234.56"
    assert read_amount("Итого 99,90") == "99.90"
    assert read_amount("Coffee 3.20\nCake 4.80\nTip 1.00") == "4.80"
    assert read_amount("no prices") == ""


def test_the_shop_is_the_first_plain_line():
    assert read_shop("\n  Bakery Nord  \n2026-11-03\nTotal 4.20") == "Bakery Nord"
    assert read_shop("2026-11-03\nTotal 4.20\n12.00\nCorner Shop: Main St") == "Corner Shop- Main St"
    assert read_shop("2026-11-03\n4.20") == ""


def test_values_from_one_text():
    assert values_from_text("Bakery Nord\n03.11.2026\nTotal 4,20") == {
        "doc_date": "2026-11-03", "doc_year": "2026", "doc_month": "11", "doc_shop": "Bakery Nord", "doc_amount": "4.20"}
    assert values_from_text("") == {"doc_date": "", "doc_year": "", "doc_month": "", "doc_shop": "", "doc_amount": ""}


def test_a_text_receipt_is_read_and_other_kinds_are_left_empty(tmp_path):
    receipt = tmp_path / "r.txt"
    receipt.write_text("Corner Shop\n2026-11-03\nTotal 9.90\n", encoding="utf-8")
    assert read_values(receipt)["doc_shop"] == "Corner Shop" and read_values(receipt)["doc_amount"] == "9.90"
    other = tmp_path / "r.docx"
    other.write_bytes(b"PK not read")
    assert set(read_values(other).values()) == {""}
    assert set(read_values(tmp_path / "missing.txt").values()) == {""}
