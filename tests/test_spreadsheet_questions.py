"""A totals question about a csv the owner names is worked out from the file itself.

Phone parity audit F17: "i downloaded my bank thing as a csv, how much did i spend in october?
not counting the salary" was answered with a guide-about-files reply while ~/Downloads held the
bank export. Three parts are tested here against the frozen contract in CLAIMS.md:

1. the classifier (core/execution/constants.py) claims the money asks that name a table thing,
   and leaves make/convert/send/find-by-name, size asks and the sibling machine lanes alone;
2. the engine (core/spreadsheet_questions.py) reads the file with the csv module and answers with
   exact decimal totals, the counted rows and the rule it used;
3. the lane (core/agent_runtime/fast_paths_machine.py) resolves the csv in Desktop/Downloads/
   Documents, picks the newest when several match, and answers on the PC and on a phone that
   holds the "computer tools" right -- a phone without it gets the stock refusal.

The engine and lane imports sit inside the tests so the classifier tests run the moment the
classifier lands; the engine and lane tests go green when their parts are wired.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.execution.constants import SpreadsheetQuestion, spreadsheet_question_intent

F17_ASK = "i downloaded my bank thing as a csv, how much did i spend in october? not counting the salary"


# =============================================================================================
# 1. The classifier
# =============================================================================================

@pytest.mark.parametrize(
    ("said", "expected"),
    [
        # The audit case F17, whole and exact.
        (
            F17_ASK,
            SpreadsheetQuestion(
                file_words=("bank",), month="october", year=None,
                date_from=None, date_to=None, direction="out", exclude=("salary",),
            ),
        ),
        # Month and a year the ask names.
        (
            "how much did i spend in october 2025 in my transactions csv?",
            SpreadsheetQuestion(
                file_words=("transactions",), month="october", year=2025,
                date_from=None, date_to=None, direction="out", exclude=(),
            ),
        ),
        # A date range written dd/mm/yyyy, and a money-in ask.
        (
            "in my bank csv, how much came in from 03/10/2026 to 20/10/2026?",
            SpreadsheetQuestion(
                file_words=("bank",), month=None, year=None,
                date_from="2026-10-03", date_to="2026-10-20", direction="in", exclude=(),
            ),
        ),
        # A range in ISO order anywhere in the ask.
        (
            "from 2026-10-03 to 2026-10-20 how much did i spend in my transactions csv",
            SpreadsheetQuestion(
                file_words=("transactions",), month=None, year=None,
                date_from="2026-10-03", date_to="2026-10-20", direction="out", exclude=(),
            ),
        ),
        # A bare total is both directions; the card's own words name the table thing.
        (
            "whats the total of my card statement csv",
            SpreadsheetQuestion(
                file_words=("card", "statement"), month=None, year=None,
                date_from=None, date_to=None, direction="both", exclude=(),
            ),
        ),
        # Exclusion words: every word of the excluded phrase, lightly stemmed.
        (
            "how much did i spend in october in my bank thing, leaving out the gym membership",
            SpreadsheetQuestion(
                file_words=("bank",), month="october", year=None,
                date_from=None, date_to=None, direction="out", exclude=("gym", "membership"),
            ),
        ),
        (
            "total in my transactions export, not counting the groceries",
            SpreadsheetQuestion(
                file_words=("transactions", "export"), month=None, year=None,
                date_from=None, date_to=None, direction="both", exclude=("grocerie",),
            ),
        ),
        # Money in by another wording.
        (
            "how much money came in in my bank csv in september",
            SpreadsheetQuestion(
                file_words=("bank",), month="september", year=None,
                date_from=None, date_to=None, direction="in", exclude=(),
            ),
        ),
    ],
)
def test_the_ask_is_claimed_with_its_parts(said, expected):
    assert spreadsheet_question_intent(said) == expected


@pytest.mark.parametrize("said", [
    # Making, converting, sending or finding the file keeps its own lane.
    "make me a csv of my spending",
    "convert my bank csv to excel",
    "send the transactions csv to someone",
    "find the csv named bank_statement",
    "read the bank csv",
    "open my transactions.csv",
    "summarize my bank csv please",
    # A money ask with no table thing named is not this lane's.
    "how much did i spend in october?",
    # "How much" about the FILE itself is a size ask, not a money ask.
    "how much space does my csv take",
    "how big is my transactions csv",
    "how many rows are in my bank csv",
    # The sibling machine lanes keep their own asks.
    "whats the biggest file in my downloads",
    "the pictures in my Downloads from the last two days",
    "how big is my downloads folder",
    "read notes.txt in my documents",
])
def test_the_ask_stays_with_its_own_lane(said):
    assert spreadsheet_question_intent(said) is None


# =============================================================================================
# 2. The engine (core/spreadsheet_questions.py, imported late: it lands after the classifier)
# =============================================================================================

# (a) comma separated, ISO dates, one signed Amount column.
CSV_COMMA_ISO = """Date,Description,Amount
2026-10-03,COFFEE SHOP,-4.20
2026-10-05,SALARY PAYROLL,2100.00
2026-10-07,GROCERY MARKET,-41.20
2026-10-12,RENT PAYMENT,-87.07
2026-11-02,BOOKSHOP,-15.50
"""
# (b) semicolon separated, decimal comma, dd/mm/yyyy dates, the file's own currency symbol.
CSV_SEMICOLON_EURO = """Datum;Omschrijving;Bedrag
03/10/2026;KOFFIE;-€ 5,50
05/10/2026;SALARIS;€ 2.100,00
07/10/2026;SUPERMARKT;-€ 40,25
12/10/2026;HUUR;-€ 88,00
02/11/2026;BOEKWINKEL;-€ 15,50
"""
# (c) a debit/credit pair instead of one signed column.
CSV_DEBIT_CREDIT = """Date,Description,Debit,Credit
2026-10-03,COFFEE SHOP,4.20,
2026-10-05,EMPLOYER SALARY,,2100.00
2026-10-07,GROCERY MARKET,41.20,
2026-10-12,RENT,87.07,
2026-11-02,BOOKSHOP,15.50,
"""
# (d) two preamble rows and odd column names before the data starts.
CSV_MESSY_HEADER = """ACME BANK account overview
made on 05/11/2026

Booking day;Narrative;Amount (EUR)
03.10.2026;COFFEE SHOP;-4,20
05.10.2026;SALARY PAYROLL;2.100,00
07.10.2026;GROCERY MARKET;-41,20
12.10.2026;RENT FLAT;-87,07
02.11.2026;BOOKSHOP;-15,50
"""


def _engine():
    from core import spreadsheet_questions

    return spreadsheet_questions


def _write(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _question(**fields) -> SpreadsheetQuestion:
    base = dict(
        file_words=(), month=None, year=None,
        date_from=None, date_to=None, direction="out", exclude=(),
    )
    base.update(fields)
    return SpreadsheetQuestion(**base)


def test_comma_csv_iso_dates_one_signed_column(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "transactions.csv", CSV_COMMA_ISO), _question(month="october")
    )
    # The exact sum, never rounded: 4.20 + 41.20 + 87.07 has two decimals and every digit shows.
    assert summary.ok, summary.reply
    assert summary.total_out_text == "132.47"
    assert summary.rows_read == 5
    assert summary.rows_counted == 3  # the salary row is money in; November is another month
    assert summary.date_column == "Date"
    assert summary.amount_column == "Amount"
    assert summary.needs_column_choice is False
    lines = " | ".join(summary.counted_lines)
    for row in ("COFFEE SHOP", "GROCERY MARKET", "RENT PAYMENT"):
        assert row in lines, summary.counted_lines
    assert len(summary.counted_lines) == 3
    assert "132.47" in summary.reply
    assert "GROCERY MARKET" in summary.reply  # the rows counted are shown, not just the total
    assert "Rule:" in summary.reply


def test_money_in_and_both_directions_on_a_signed_column(tmp_path):
    path = _write(tmp_path, "transactions.csv", CSV_COMMA_ISO)
    engine = _engine()
    incoming = engine.summarize(path, _question(month="october", direction="in"))
    assert incoming.ok, incoming.reply
    assert incoming.total_in_text == "2100.00"
    assert incoming.rows_counted == 1
    both = engine.summarize(path, _question(month="october", direction="both"))
    assert both.ok, both.reply
    assert both.total_out_text == "132.47"
    assert both.total_in_text == "2100.00"
    assert both.rows_counted == 4
    assert "132.47" in both.reply and "2100.00" in both.reply


def test_exclusion_words_drop_the_named_rows(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "transactions.csv", CSV_COMMA_ISO),
        _question(month="october", exclude=("rent",)),
    )
    assert summary.ok, summary.reply
    # 4.20 + 41.20: the rent row is gone from the sum and from the counted rows.
    assert summary.total_out_text == "45.40"
    assert summary.rows_counted == 2
    assert "RENT PAYMENT" not in " | ".join(summary.counted_lines)
    # The rule that was used names the exclusion.
    assert "rent" in summary.reply.lower()
    assert "Rule:" in summary.reply


def test_a_date_range_from_the_ask(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "transactions.csv", CSV_COMMA_ISO),
        _question(date_from="2026-10-05", date_to="2026-10-12"),
    )
    assert summary.ok, summary.reply
    assert summary.total_out_text == "128.27"  # GROCERY MARKET + RENT PAYMENT only
    assert summary.rows_counted == 2


def test_semicolon_decimal_comma_and_the_files_own_symbol(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "bank.csv", CSV_SEMICOLON_EURO), _question(month="october")
    )
    assert summary.ok, summary.reply
    # 5,50 + 40,25 + 88,00: the comma decimals and the euro sign of the file are kept,
    # and the total never appears with dot decimals.
    assert "133,75" in (summary.total_out_text or "")
    assert "133.75" not in (summary.total_out_text or "")
    assert "€" in (summary.total_out_text or "")
    assert "2.100,00" in (summary.total_in_text or "")  # the thousands dot of the file too
    assert summary.rows_counted == 3
    assert summary.date_column == "Datum"
    assert summary.amount_column == "Bedrag"


def test_debit_and_credit_columns_are_read_on_both_sides(tmp_path):
    path = _write(tmp_path, "bank.csv", CSV_DEBIT_CREDIT)
    engine = _engine()
    outgoing = engine.summarize(path, _question(month="october"))
    assert outgoing.ok, outgoing.reply
    assert outgoing.total_out_text == "132.47"  # the Debit side
    assert outgoing.rows_counted == 3
    incoming = engine.summarize(path, _question(month="october", direction="in"))
    assert incoming.ok, incoming.reply
    assert incoming.total_in_text == "2100.00"  # the Credit side
    assert incoming.rows_counted == 1


def test_preamble_rows_and_odd_column_names(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "export.csv", CSV_MESSY_HEADER), _question(month="october")
    )
    assert summary.ok, summary.reply
    assert "132,47" in (summary.total_out_text or "")
    assert summary.rows_read == 5  # the two preamble rows and the header are not data rows
    assert summary.rows_counted == 3
    assert summary.date_column == "Booking day"
    assert summary.amount_column == "Amount (EUR)"
    assert "Rule:" in summary.reply


def test_a_month_the_file_does_not_cover_says_the_range_it_does(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "transactions.csv", CSV_COMMA_ISO), _question(month="december")
    )
    assert summary.ok is False
    assert summary.rows_counted == 0
    # The file runs from 2026-10-03 to 2026-11-02; the reply says that range in plain words.
    assert "october" in summary.reply.lower()
    assert "november" in summary.reply.lower()


def test_two_of_the_same_month_use_the_latest_year_and_say_which(tmp_path):
    two_octobers = """Date,Description,Amount
2025-10-03,OLD COFFEE,-4.20
2026-10-03,COFFEE SHOP,-41.20
2026-10-07,GROCERY MARKET,-87.07
"""
    summary = _engine().summarize(
        _write(tmp_path, "transactions.csv", two_octobers), _question(month="october")
    )
    assert summary.ok, summary.reply
    assert summary.total_out_text == "128.27"  # 41.20 + 87.07 from 2026 only
    assert summary.rows_counted == 2
    assert "2026" in summary.reply
    assert "4.20" not in summary.reply  # the 2025 row is not part of the answer


def test_two_amount_columns_with_no_hint_ask_which_one_is_the_amount(tmp_path):
    no_hint = """Date,Description,Coffee,Books
2026-10-03,SHOP A,4.20,41.20
2026-10-07,SHOP B,1.10,2.20
"""
    summary = _engine().summarize(
        _write(tmp_path, "shop.csv", no_hint), _question(month="october")
    )
    assert summary.ok is False
    assert summary.needs_column_choice is True
    # Both candidate columns are named, in plain words, in the ask for the amount column.
    assert "Coffee" in summary.reply
    assert "Books" in summary.reply
    assert "amount" in summary.reply.lower()


def test_no_date_column_with_a_month_ask_names_the_columns_found(tmp_path):
    no_date = """Description,Amount
COFFEE SHOP,-4.20
GROCERY MARKET,-41.20
"""
    summary = _engine().summarize(
        _write(tmp_path, "shop.csv", no_date), _question(month="october")
    )
    assert summary.ok is False
    assert "Description" in summary.reply
    assert "Amount" in summary.reply


def test_a_file_with_no_rows_says_so_in_plain_words(tmp_path):
    summary = _engine().summarize(
        _write(tmp_path, "empty.csv", "Date,Description,Amount\n"), _question(month="october")
    )
    assert summary.ok is False
    assert summary.total_out_text is None and summary.total_in_text is None
    assert "row" in summary.reply.lower()


# =============================================================================================
# 3. The lane: the file is resolved and answered on the PC and on the phone
# =============================================================================================


class _FakeAgent:
    """The smallest agent the machine read lane needs (the pattern of the existing tests)."""

    def _emit_runtime_event(self, *args, **kwargs):
        return None

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "reason": kwargs.get("reason", "")}


def _fake_home(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "home"
    (root / "Downloads").mkdir(parents=True)
    (root / "Documents").mkdir(parents=True)
    (root / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    return root


@pytest.fixture
def pc_home(monkeypatch, tmp_path):
    """A fake home whose csv files are found by the real find_file walk (PC surface)."""
    import core.agent_runtime.fast_paths_machine as fpm
    from core.runtime_execution_tools import _machine_find_file

    root = _fake_home(monkeypatch, tmp_path)

    def execute(intent, args, source_context=None):
        if intent != "machine.find_file":
            raise AssertionError(f"the spreadsheet lane must not run {intent!r}")
        return _machine_find_file({"name": args["name"]}, workspace_root=root)

    monkeypatch.setattr(fpm, "execute_authorized_runtime_tool", execute)
    return root


def _ask(agent, text, *, source_context=None, surface="channel"):
    import core.agent_runtime.fast_paths_machine as fpm

    return fpm.maybe_handle_direct_machine_read_request(
        agent,
        text,
        session_id="s-spreadsheet",
        source_surface=surface,
        source_context=source_context,
    )


def test_the_pc_answers_the_october_spend_from_the_csv_it_found(pc_home):
    (pc_home / "Downloads" / "bank_export.csv").write_text(CSV_DEBIT_CREDIT, encoding="utf-8")
    result = _ask(_FakeAgent(), F17_ASK)
    assert result is not None, "the spreadsheet lane must own this ask"
    reply = result["response"]
    # The file it used is named, and the hand-computed October spend is shown exactly.
    assert "bank_export.csv" in reply, reply
    assert "132.47" in reply, reply
    # The rows that were counted are shown, the exclusion is named, and the rule is stated.
    assert "GROCERY MARKET" in reply, reply
    assert "salary" in reply.lower(), reply
    assert "Rule:" in reply, reply
    # The ask was about spending, so the incoming salary figure is not the answer.
    assert "2100" not in reply, reply


def test_the_newest_of_two_matching_csvs_is_named_and_used(pc_home):
    old = pc_home / "Downloads" / "bank_old.csv"
    new = pc_home / "Downloads" / "bank_new.csv"
    old.write_text("Date,Description,Amount\n2026-10-03,OLD SHOP,-10.00\n", encoding="utf-8")
    new.write_text("Date,Description,Amount\n2026-10-03,NEW SHOP,-20.00\n", encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 20000, now - 20000))
    os.utime(new, (now - 10000, now - 10000))
    result = _ask(_FakeAgent(), "how much did i spend in october in my bank csv?")
    assert result is not None
    reply = result["response"]
    assert "bank_new.csv" in reply, reply
    assert "bank_old.csv" in reply, reply  # the other match is named, not silently dropped
    assert "20.00" in reply, reply
    assert "10.00" not in reply, reply  # the older file's total is not the answer


def test_no_csv_anywhere_is_said_plainly(pc_home):
    result = _ask(_FakeAgent(), F17_ASK)
    assert result is not None, "the lane owns the ask even when it finds nothing"
    reply = result["response"]
    assert "csv" in reply.lower(), reply
    assert "132.47" not in reply


def _phone_store(monkeypatch, *, granted: bool):
    """A paired phone whose device record lives in the store the rights check reads live."""
    record = {
        "device_id": "phone:abc", "status": "active", "kind": "phone", "sealed_protocol": True,
        "rules": {"computer_tools": granted, "paid_services": False},
        "right_expiry": {"computer_tools": time.time() + 3600},
    }

    class _Store:
        def device(self, device_id):
            return dict(record) if device_id == record["device_id"] else None

    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=_Store()))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})


def _phone_turn():
    import core.request_trust as rt

    return {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False, rt.REMOTE_DEVICE_KEY: "phone:abc"}


def test_a_phone_with_the_computer_right_gets_the_same_grounded_answer(monkeypatch, tmp_path):
    _phone_store(monkeypatch, granted=True)
    root = _fake_home(monkeypatch, tmp_path)
    (root / "Downloads" / "bank_export.csv").write_text(CSV_DEBIT_CREDIT, encoding="utf-8")
    # No stubbing of the tool boundary: the rights fence and the file walk both run for real.
    result = _ask(_FakeAgent(), F17_ASK, source_context=_phone_turn())
    assert result is not None
    reply = result["response"]
    assert "bank_export.csv" in reply, reply
    assert "132.47" in reply, reply
    assert "Rule:" in reply, reply


def test_a_phone_without_the_right_gets_the_stock_refusal(monkeypatch, tmp_path):
    _phone_store(monkeypatch, granted=False)
    root = _fake_home(monkeypatch, tmp_path)
    (root / "Downloads" / "bank_export.csv").write_text(CSV_DEBIT_CREDIT, encoding="utf-8")
    result = _ask(_FakeAgent(), F17_ASK, source_context=_phone_turn())
    assert result is not None
    reply = result["response"]
    # The turn is told where the right is turned on; no file was read, so no total exists.
    assert "Use tools on my computer" in reply, reply
    assert "132.47" not in reply, reply
