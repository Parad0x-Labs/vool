"""TIER 3 (operation equivalence) — consensus block A, review-20260820-141451.

The operation the kernel runs must match the operation the ask names: "vowel != char"
(t18: "how many vowels in etcd" shipped "4 characters"). The count-op vocabulary was
incomplete (no count_vowels/count_consonants), forcing approximation; and the fast
path admitted a count op whose unit contradicted the ask. Both are closed here.
"""
from core.kernel import repl


def test_machine_str_counts_each_unit_correctly():
    assert "1 vowels" in repl._machine_str("count_vowels", "etcd")            # only 'e'
    assert "4 consonants" in repl._machine_str("count_consonants", "FORMULA")  # F R M L
    assert "3 vowels" in repl._machine_str("count_vowels", "FORMULA")          # O U A
    assert "4 characters" in repl._machine_str("count_chars", "etcd")
    assert "4 letters" in repl._machine_str("count_letters", "etcd")
    # the vowel count is NOT the char count for the same word — the t18 defect
    assert repl._machine_str("count_vowels", "etcd") != repl._machine_str("count_chars", "etcd")


def test_operation_equivalence_refuses_vowel_ask_served_by_char_op():
    reason = repl._str_count_unit_mismatch("count_chars", "How many vowels are in etcd?")
    assert reason and "vowel" in reason           # t18: refuse, do not ship "4 characters"
    assert repl._str_count_unit_mismatch("count_letters", "How many vowels in etcd?")


def test_operation_equivalence_admits_the_matching_op():
    assert repl._str_count_unit_mismatch("count_vowels", "How many vowels in etcd?") is None
    assert repl._str_count_unit_mismatch("count_chars", "How many characters in etcd?") is None
    assert repl._str_count_unit_mismatch("count_consonants", "Count the consonants in FORMULA") is None
    assert repl._str_count_unit_mismatch("count_letters", "How many letters in etcd?") is None


def test_operation_equivalence_silent_when_ask_names_no_supported_unit():
    # No supported count-noun in the ask -> the chosen op stands (no false refusal).
    assert repl._str_count_unit_mismatch("count_chars", "reverse this word") is None
    # Not a count op at all -> never refused on unit grounds.
    assert repl._str_count_unit_mismatch("reverse", "how many vowels in etcd") is None
