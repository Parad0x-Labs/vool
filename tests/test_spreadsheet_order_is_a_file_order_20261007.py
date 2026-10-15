"""Wanting a spreadsheet or workbook is ordering a file.

Measured live 2026-10-06 (pinned gpt-4.1-mini): "I need a spreadsheet of my monthly subscriptions:
Netflix 15.49, Spotify 11.99, iCloud 2.99, gym 39.00. Excel please." never reached the file tool; the
model answered "working on it" with nothing running, which the honesty guard replaced with "Nothing is
running right now". No file.
"""
from __future__ import annotations

import pytest

from core.deliverables import requested_formats
from core.tool_demand_signals import asks_for_a_file


@pytest.mark.parametrize(
    "text",
    [
        "I need a spreadsheet of my monthly subscriptions: Netflix 15.49, Spotify 11.99, iCloud 2.99, gym 39.00. "
        "Excel please.",
        "Can you make me a simple spreadsheet tracking my running miles this week?",
        "build a workbook with one sheet per quarter for the team budget",
        "I'd like... no wait, I want a spreadsheet listing every plant in the garden",
        "put together a small spreadsheet of the conference speakers and their talks",
    ],
)
def test_wanting_a_spreadsheet_orders_an_xlsx(text):
    assert asks_for_a_file(text)
    assert requested_formats(text) == ("xlsx",)


@pytest.mark.parametrize(
    "text",
    [
        "I have a spreadsheet of my expenses, what should I cut?",
        "Why does my spreadsheet show #REF errors?",
        "how do I make a spreadsheet formula that sums a column?",
        "my workbook keeps crashing when I open it",
    ],
)
def test_talking_about_a_spreadsheet_orders_nothing(text):
    assert not asks_for_a_file(text)
