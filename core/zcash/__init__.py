"""Zcash private invoices: shielded ZEC payment requests confirmed watch-only.

VOOL never holds a Zcash spending key here and moves no money. The owner gives a Unified Full
Viewing Key; a pinned zcash-devtool binary syncs a view-only wallet with it; VOOL issues ZIP-321
payment requests whose memo names the invoice and confirms payment by reading received shielded
notes. Off by default (``zcash_invoices_enabled`` in Settings, ``VOOL_ZCASH_ENABLED`` to override).
"""
