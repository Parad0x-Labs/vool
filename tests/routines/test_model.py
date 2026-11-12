"""The routine record: what validate keeps, what it refuses, and how rules become names and folders."""
from __future__ import annotations

import pytest

from core.routines import model
from core.routines.model import RoutineError, clean_value, render, render_folder, render_name, uses_receipts, validate

BASE = {"name": "Sort scans", "folder": "/somewhere", "types": [".pdf"], "rename": [{"token": "name"}]}


def refused(raw, code, **kwargs):
    with pytest.raises(RoutineError) as caught:
        validate(raw, **kwargs)
    assert caught.value.code == code, (caught.value.code, caught.value.message)
    return caught.value


def test_a_small_routine_is_kept_in_one_clean_shape():
    routine = validate({"name": "  Sort   scans ", "folder": "/somewhere", "types": [".PDF", ".pdf", " .Jpg "],
                        "rename": [{"text": "Scan "}, {"text": "of "}, {"text": ""}, {"token": "date"}],
                        "into": [{"token": "year"}, {"text": "/"}, {"token": "month"}]})
    assert model.valid_id(routine["id"])
    assert routine["name"] == "Sort scans"
    assert routine["types"] == [".jpg", ".pdf"]
    # Next to each other, fixed texts become one piece; an empty one is dropped.
    assert routine["rename"] == [{"text": "Scan of "}, {"token": "date"}]
    assert routine["into"] == [{"token": "year"}, {"text": "/"}, {"token": "month"}]
    assert routine["log"] is None
    assert routine["max_files"] == model.DEFAULT_MAX_FILES
    assert routine["schedule"] == {"every": "", "time": "09:00", "weekday": 0}
    assert routine["schema"] == model.SCHEMA
    # Validating what was kept gives the same routine back.
    assert validate(routine) == routine


def test_names_follow_what_a_skill_name_can_be():
    assert validate(dict(BASE, name="Kvitton’s scans - 2026"))["name"] == "Kvitton’s scans - 2026"
    assert validate(dict(BASE, name="Квитанции"))["name"] == "Квитанции"
    assert validate(dict(BASE, name="x" * model.NAME_LIMIT))["name"] == "x" * model.NAME_LIMIT
    for name in ("", "   ", "x" * (model.NAME_LIMIT + 1), "a/b", "_hidden", "-dash", "Scans: work", "a.b", "[Email 1]"):
        refused(dict(BASE, name=name), "bad_name")


def test_unknown_parts_newer_routines_and_bad_ids_are_refused_not_carried():
    refused(dict(BASE, run_command="rm -rf"), "bad_routine")
    refused(dict(BASE, schema="vool.routine/2"), "bad_routine")
    refused(dict(BASE, id="rt-XYZ"), "bad_routine")
    refused(dict(BASE, id="../rt-0123456789"), "bad_routine")
    refused(["not", "a", "routine"], "bad_routine")
    assert validate(dict(BASE, id="rt-0123456789"))["id"] == "rt-0123456789"


def test_a_folder_is_needed_except_for_a_routine_brought_in():
    refused(dict(BASE, folder=""), "no_folder")
    assert validate(dict(BASE, folder=""), require_folder=False)["folder"] == ""


def test_file_kinds_are_plain_extensions_and_never_csv():
    for types in ([], "pdf", [".pdf"] * 0 + [f".t{index}" for index in range(13)], ["pdf"], [".toolongext"],
                  [".p df"], [".csv"], [".pdf", ".CSV"]):
        refused(dict(BASE, types=types), "bad_types")


def test_rules_only_take_known_values_and_safe_text():
    refused(dict(BASE, rename=[{"token": "new_name"}]), "bad_rule")  # a result value only fits a list column
    refused(dict(BASE, rename=[{"token": "date", "text": "x"}]), "bad_rule")
    refused(dict(BASE, rename=[{"shell": "echo"}]), "bad_rule")
    refused(dict(BASE, rename="date"), "bad_rule")
    refused(dict(BASE, rename=[{"text": "x"}, {"token": "name"}] * 7), "bad_rule")
    for text in ("a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b", "a\x00b", "a\nb", "a\x7fb"):
        refused(dict(BASE, rename=[{"text": text}]), "bad_text")
    refused(dict(BASE, rename=[{"text": "x" * 61}]), "text_too_long")
    # A slash is how a folder rule names a folder inside a folder.
    assert validate(dict(BASE, into=[{"text": "Paid/2026"}]))["into"] == [{"text": "Paid/2026"}]
    refused(dict(BASE, into=[{"text": "Paid:2026"}]), "bad_text")


def test_a_routine_always_renames_or_moves():
    refused(dict(BASE, rename=[], into=[], log={"file": "list.csv", "columns": [[{"token": "name"}]]}), "no_steps")
    refused(dict(BASE, rename=None), "no_steps")


def test_the_list_file_stays_inside_the_folder_and_rows_are_bounded():
    good = {"file": "Paid/list.csv", "columns": [[{"token": "date"}], [{"token": "new_name"}], [{"token": "folder"}]]}
    assert validate(dict(BASE, log=good))["log"] == good
    for name in ("list.txt", "/list.csv", "../list.csv", "a/../list.csv", "./list.csv", "a//list.csv", "x" * 120 + ".csv",
                 "li:st.csv", ""):
        refused(dict(BASE, log=dict(good, file=name)), "bad_log_file")
    refused(dict(BASE, log=dict(good, columns=[])), "bad_columns")
    refused(dict(BASE, log=dict(good, columns=[[]])), "bad_columns")
    refused(dict(BASE, log=dict(good, columns=[[{"token": "date"}]] * 13)), "bad_columns")
    refused(dict(BASE, log=dict(good, columns=[[{"text": "x"}, {"token": "date"}] * 5])), "bad_rule")
    refused(dict(BASE, log=dict(good, delimiter=";")), "bad_rule")
    refused(dict(BASE, log="list.csv"), "bad_rule")


def test_how_many_files_one_run_may_handle():
    assert validate(dict(BASE, max_files=model.MAX_FILES_LIMIT))["max_files"] == model.MAX_FILES_LIMIT
    for value in (0, model.MAX_FILES_LIMIT + 1, "5", 2.0, True, None):
        refused(dict(BASE, max_files=value), "bad_max_files")


def test_schedules_are_a_day_and_a_time():
    weekly = {"every": "weekly", "time": "07:30", "weekday": 4}
    assert validate(dict(BASE, schedule=weekly))["schedule"] == weekly
    for schedule in ({"every": "hourly"}, {"every": "daily", "time": "24:00"}, {"every": "daily", "time": "9:00"},
                     {"every": "weekly", "weekday": 7}, {"every": "weekly", "weekday": True}, {"every": "daily", "at": "x"},
                     "daily"):
        refused(dict(BASE, schedule=schedule), "bad_schedule")


def test_rendering_names_and_folders():
    values = {"date": "2026-11-03", "year": "2026", "month": "11", "name": "scan_001", "doc_shop": ""}
    assert render_name([{"token": "date"}, {"text": "  "}, {"token": "name"}, {"text": ". "}], values, ".pdf") == \
        "2026-11-03 scan_001.pdf"
    assert render_folder([{"token": "year"}, {"text": "/ "}, {"token": "month"}], values) == ["2026", "11"]
    assert render_folder([], values) == []
    with pytest.raises(RoutineError) as caught:
        render([{"token": "doc_shop"}], values)
    assert caught.value.code == "missing_value:doc_shop"
    with pytest.raises(RoutineError) as caught:
        render_name([{"token": "name"}], {"name": "a:b"}, ".pdf")  # a name read from a file can still hold one
    assert caught.value.code == "bad_new_name"
    for pieces in ([{"text": "a/b/c/d/e"}], [{"text": "a/../b"}], [{"text": "a//b"}], [{"text": "./b"}]):
        with pytest.raises(RoutineError) as caught:
            render_folder(pieces, values)
        assert caught.value.code == "bad_new_folder", pieces


def test_values_read_from_files_are_made_safe_for_one_name():
    assert clean_value('  Café "Nord": 12/3  ') == "Café -Nord-- 12-3"
    assert clean_value("a   b \u00a0 c") == "a b c"
    assert clean_value("a\tb\nc") == "a-b-c"  # control characters never reach a name
    assert clean_value(". -x- .") == "x"
    assert clean_value("x" * 80, 10) == "x" * 10
    assert clean_value(None) == ""


def test_knowing_when_a_routine_needs_a_receipt_read():
    assert not uses_receipts(validate(BASE))
    assert uses_receipts(validate(dict(BASE, into=[{"token": "doc_year"}])))
    assert uses_receipts(validate(dict(BASE, log={"file": "l.csv", "columns": [[{"token": "doc_amount"}]]})))
