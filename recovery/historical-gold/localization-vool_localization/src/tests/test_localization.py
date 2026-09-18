import datetime as dt
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT))

from vool_localization import formatting
from vool_localization.catalog import Catalog

REGISTRY = PKG_ROOT / "registry"


@pytest.fixture(scope="module")
def cat() -> Catalog:
    return Catalog.load(REGISTRY)


# ---- registry integrity -------------------------------------------------

def test_registry_loads_with_baseline(cat):
    assert "en" in cat.strings
    assert len(cat.keys()) >= 40


def test_generated_locales_labeled_unreviewed(cat):
    for locale in ("pt-BR", "pt-PT", "qya-pseudo", "ar-TORTURE"):
        meta = cat.metas[locale]
        assert meta.generated is True
        assert meta.human_reviewed is False


def test_pt_br_distinct_from_pt_pt(cat):
    # pt-BR keeps its own vocabulary; pt-PT differs where it has an entry.
    assert cat.t("common.loading", "pt-BR") == "Carregando…"
    assert cat.t("common.loading", "pt-PT") == "A carregar…"


# ---- fallback chain -----------------------------------------------------

def test_fallback_pt_pt_to_pt_to_en(cat):
    # key absent from pt-PT AND pt: falls to en
    assert cat.origin("desktop.cli.bye", "pt-PT") == "en"
    # key absent from pt-PT but present in pt-BR? no — chain is tag->lang->en,
    # so pt-BR must NEVER serve pt-PT (that would erase the BR/PT distinction)
    assert cat.origin("common.cancel", "pt-PT") == "en"
    assert cat.origin("common.copy", "pt-PT") == "pt-PT"


def test_missing_key_is_visible_and_recorded(cat):
    out = cat.t("nope.does_not_exist", "pt-BR")
    assert "[missing: nope.does_not_exist (pt-BR)]" == out
    assert ("pt-BR", "nope.does_not_exist") in cat.missing


def test_strict_mode_raises(cat):
    strict = Catalog.load(REGISTRY, strict=True)
    with pytest.raises(KeyError):
        strict.t("nope.does_not_exist", "en")


# ---- message format ------------------------------------------------------

def test_variables_and_plurals_en(cat):
    assert cat.t("desktop.cli.agent_ready", "en", name="Alex") == "Alex is ready."
    assert cat.t("notifications.tasks_completed", "en", count=1) == "Task completed"
    assert cat.t("notifications.tasks_completed", "en", count=5) == "5 tasks completed"


def test_plural_ar_six_categories(cat):
    ar = "ar-TORTURE"
    assert "لا مهام" in cat.t("notifications.tasks_completed", ar, count=0)
    assert cat.t("notifications.tasks_completed", ar, count=1).startswith("مهمة واحدة")
    assert cat.t("notifications.tasks_completed", ar, count=2).startswith("مهمتان")
    assert cat.t("notifications.tasks_completed", ar, count=7).startswith("7 مهام")
    assert cat.t("notifications.tasks_completed", ar, count=15).startswith("15 مهمة")
    assert cat.t("notifications.tasks_completed", ar, count=100).startswith("100")


def test_nested_braces_do_not_crash(cat):
    # literal braces inside translations stay balanced through the parser
    assert "{" not in cat.t("receipts.cost_line", "en", amount="$12.50").replace("$12.50", "")


# ---- dates / numbers / currencies ---------------------------------------

def test_number_grouping_differs_by_locale():
    assert formatting.format_number(1234.5, "en") == "1,234.5"
    assert formatting.format_number(1234.5, "pt-BR") == "1.234,5"
    assert formatting.format_number(1234.56, "pt-PT", decimals=2) == "1 234,56"


def test_currency_pt_br_vs_pt_pt():
    assert formatting.format_currency(99.0, "BRL", "pt-BR") == "R$ 99,00"
    assert formatting.format_currency(99.0, "EUR", "pt-PT") == "99,00 €"
    assert formatting.format_currency(99.0, "USD", "en") == "$99.00"


def test_dates_differ_by_locale():
    d = dt.date(2026, 8, 26)
    assert formatting.format_date(d, "en") == "Aug 26, 2026"
    assert formatting.format_date(d, "pt-BR") == "26/08/2026"
    assert "ago" in formatting.format_date(d, "pt-BR", kind="date") or "/" in formatting.format_date(d, "pt-BR")


# ---- pseudo & RTL --------------------------------------------------------

def test_pseudo_expands_and_marks_but_keeps_placeholders():
    from vool_localization.pseudo import pseudolocalize
    out = pseudolocalize("{count, plural, one {Task completed} other {# tasks completed}}")
    assert out.startswith("[!! ") and out.endswith(" !!]")
    assert "{count, plural," in out          # placeholder untouched
    assert len(out) > 40                      # expanded


def test_rtl_direction_and_isolation():
    from vool_localization import bidi
    assert bidi.direction("ar-TORTURE") == "rtl"
    assert bidi.direction("pt-BR") == "ltr"
    wrapped = bidi.isolate(bidi.RTL_TORTURE_FRAGMENTS[0], "rtl")
    assert wrapped.startswith("\u2068")
    assert bidi.strip_controls(wrapped) == bidi.RTL_TORTURE_FRAGMENTS[0]


def test_rtl_torture_strings_render_through_catalog(cat):
    ar = "ar-TORTURE"
    for key in ("receipts.status.blocked_false_claim", "notifications.task_failed"):
        text = cat.t(key, ar)
        assert "[missing:" not in text
