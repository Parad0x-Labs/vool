"""Stay safe: how people lose their crypto, in words anyone can act on, one picture per card.

One source of truth for the Settings panel (`/api/wallet/safety`) and for anything else that wants to teach the
same lessons. Written for someone who has never used a wallet: short lines, no jargon, the same three questions
every time -- what happens, what they want, what you do. The illustrations are small inline SVG line drawings so
the panel works offline and needs no downloads.
"""
from __future__ import annotations

GOLDEN_RULE = (
    "Nobody real ever needs your secret phrase or private key. Not support, not an admin, not an airdrop, not a "
    "migration. The 12 words ARE the wallet: whoever has them owns everything in it."
)

PRINCIPLE = "VOOL tells you what a signature does before you sign. If VOOL says red, it is red. Stop."

_HEAD = '<svg viewBox="0 0 64 64" width="64" height="64" role="img" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
_END = "</svg>"
_RED = "#f87171"
_GREEN = "#34d399"


def _svg(*parts: str) -> str:
    return _HEAD + "".join(parts) + _END


def _slash() -> str:
    return f'<line x1="10" y1="54" x2="54" y2="10" stroke="{_RED}" stroke-width="3.5"/>'


_CARDS: tuple[dict[str, str], ...] = (
    {
        "id": "secret_phrase", "emoji": "🔑",
        "title": "Anyone asking for your secret phrase or private key is a thief",
        "what_happens": "A message, a website or a 'support agent' asks you to type or paste your 12 words or your private key.",
        "they_want": "Your whole wallet. With those words they move everything out in seconds and it cannot be undone.",
        "you_do": "Never type them anywhere except your own wallet app when restoring it. Close the chat. Block the sender.",
        "svg": _svg('<rect x="8" y="12" width="36" height="24" rx="6"/>', '<path d="M18 36 L14 46 L26 36"/>',
                    '<text x="26" y="29" font-size="12" text-anchor="middle" fill="currentColor" stroke="none">12?</text>', _slash()),
    },
    {
        "id": "fake_support", "emoji": "🎧",
        "title": "Fake support: 'your wallet is locked, we will repair it'",
        "what_happens": "Someone in a chat or a reply says your wallet is locked, hacked or needs repair, and offers to fix it.",
        "they_want": "Your secret phrase, or a 'sync' or 'validate' form where you type it. That is the theft.",
        "you_do": "Real wallets never lock and never repair by phrase. Ignore the offer. Ask in the official help page you typed yourself.",
        "svg": _svg('<path d="M14 34 v-6 a18 18 0 0 1 36 0 v6"/>', '<rect x="10" y="32" width="8" height="12" rx="2"/>', '<rect x="46" y="32" width="8" height="12" rx="2"/>',
                    '<rect x="24" y="40" width="16" height="14" rx="3"/>', '<path d="M28 40 v-4 a4 4 0 0 1 8 0 v4"/>',
                    '<text x="32" y="52" font-size="10" text-anchor="middle" fill="currentColor" stroke="none">?</text>'),
    },
    {
        "id": "fake_airdrop", "emoji": "🎁",
        "title": "Fake airdrop: 'you won tokens, sign to claim'",
        "what_happens": "A site says free coins are waiting and asks you to sign one thing to claim them.",
        "they_want": "That signature is often a permission to take your tokens, not a claim. Nothing free is waiting.",
        "you_do": "Read what VOOL says the signature does. If it says permission, approve or standing authority, walk away.",
        "svg": _svg('<rect x="12" y="26" width="40" height="28" rx="3"/>', '<path d="M12 34 h40"/>', '<path d="M32 26 v28"/>',
                    '<path d="M32 26 c-8 -12 -20 -4 -8 0 M32 26 c8 -12 20 -4 8 0"/>', f'<path d="M44 14 c6 0 6 8 0 8 c-4 0 -4 -4 0 -4" stroke="{_RED}"/>'),
    },
    {
        "id": "fake_migration", "emoji": "🔁",
        "title": "Fake migration: 'upgrade or verify your wallet now'",
        "what_happens": "A message says your wallet must migrate, upgrade, verify or re-sync, with a link and a deadline.",
        "they_want": "You on a copy of a real wallet site, typing your phrase, or signing a permission on your real wallet.",
        "you_do": "Nothing ever needs migrating by link. Type the wallet address yourself. Deadlines are pressure, not truth.",
        "svg": _svg('<rect x="6" y="22" width="20" height="20" rx="3"/>', '<rect x="38" y="22" width="20" height="20" rx="3"/>', '<path d="M28 32 h8 m-3 -3 l3 3 l-3 3"/>',
                    f'<path d="M48 8 l8 14 h-16 z" stroke="{_RED}"/>', f'<path d="M48 13 v5 m0 2 v1" stroke="{_RED}"/>'),
    },
    {
        "id": "unlimited_approval", "emoji": "♾️",
        "title": "Unlimited approval: one click lets a site take that coin forever",
        "what_happens": "A trading or claim site asks you to 'approve' a token. The amount is set to unlimited.",
        "they_want": "Standing permission. They can take that token from your wallet later, again and again, until you revoke it.",
        "you_do": "Approve only the exact amount you are using now. Revoke old approvals. VOOL shows this in red for a reason.",
        "svg": _svg('<path d="M20 32 c0 -8 12 -8 12 0 c0 8 12 8 12 0 c0 -8 -12 -8 -12 0 c0 8 -12 8 -12 0"/>',
                    f'<circle cx="16" cy="52" r="4" stroke="{_RED}"/>', f'<circle cx="32" cy="54" r="4" stroke="{_RED}"/>', f'<circle cx="48" cy="52" r="4" stroke="{_RED}"/>'),
    },
    {
        "id": "lookalike_address", "emoji": "🔍",
        "title": "Lookalike address: the first and last letters match, the middle does not",
        "what_happens": "A tiny payment or a message shows an address that looks exactly like one you used before.",
        "they_want": "You to copy their address from your history and send real money to it.",
        "you_do": "Check the first four, the last four and two letters in the middle, every time. Never copy an address from a message.",
        "svg": _svg('<circle cx="26" cy="26" r="14"/>', '<path d="M36 36 l14 14"/>',
                    '<text x="26" y="30" font-size="9" text-anchor="middle" fill="currentColor" stroke="none">9xQe</text>',
                    f'<text x="32" y="60" font-size="9" text-anchor="middle" fill="{_RED}" stroke="none">9xQe…VFin ≠ 9xQe…VFin</text>'),
    },
    {
        "id": "investment_friend", "emoji": "💔",
        "title": "A new friend or love interest teaches you to invest",
        "what_happens": "Someone kind online shows you a platform that pays every day. Small withdrawals work at first.",
        "they_want": "Bigger deposits. When you try to take real money out, there is a 'tax', a 'fee', then nothing.",
        "you_do": "If a stranger teaches you to invest, the platform is fake. Do not deposit. Tell someone you trust offline.",
        "svg": _svg('<path d="M32 50 c-14 -10 -20 -18 -14 -26 c4 -5 11 -4 14 2 c3 -6 10 -7 14 -2 c6 8 0 16 -14 26 z"/>',
                    f'<path d="M12 60 l10 -10 l8 6 l12 -14 l10 4" stroke="{_GREEN}"/>', f'<path d="M50 44 l4 12 l-10 -4" stroke="{_RED}"/>'),
    },
    {
        "id": "fake_app", "emoji": "📱",
        "title": "Fake wallet apps and browser extensions",
        "what_happens": "A search ad or a store listing offers a wallet app that looks like the real one, with a slightly different name.",
        "they_want": "Your phrase when you 'restore' into it, or every transaction you sign in it.",
        "you_do": "Install only from the link on the wallet's own website, typed by you. Check the publisher name and the reviews' dates.",
        "svg": _svg('<rect x="18" y="6" width="28" height="52" rx="5"/>', '<path d="M28 12 h8"/>', f'<rect x="24" y="22" width="16" height="16" rx="3" stroke="{_GREEN}"/>',
                    f'<rect x="24" y="40" width="16" height="12" rx="3" stroke="{_RED}"/>', f'<path d="M28 44 l8 6 m0 -6 l-8 6" stroke="{_RED}"/>'),
    },
    {
        "id": "giveaway", "emoji": "🪙",
        "title": "Giveaway: 'send 1 coin, get 2 back'",
        "what_happens": "A famous name or a copied account promises to double whatever you send during a live event.",
        "they_want": "Your coin. Nothing comes back. The stream is a recording, the account is a copy.",
        "you_do": "Nobody doubles your money. Sending first is the whole trick. Do not send.",
        "svg": _svg('<circle cx="22" cy="32" r="10"/>', '<circle cx="42" cy="32" r="10"/>', '<path d="M30 20 l4 -8 m-2 0 l6 0"/>', _slash()),
    },
    {
        "id": "spam_tokens", "emoji": "✉️",
        "title": "Strange tokens appear in your wallet with a link in the name",
        "what_happens": "Coins you never bought show up, named like a website or a claim page.",
        "they_want": "You to visit the link and sign a 'claim' that is really a permission or a transfer.",
        "you_do": "Leave them alone. Do not visit the link, do not claim, do not sell them on a site the token points to.",
        "svg": _svg('<rect x="8" y="16" width="48" height="32" rx="4"/>', '<path d="M8 20 l24 18 l24 -18"/>',
                    f'<text x="32" y="44" font-size="8" text-anchor="middle" fill="{_RED}" stroke="none">claim-here.xyz</text>'),
    },
    {
        "id": "screen_share", "emoji": "🖥️",
        "title": "Screen sharing or remote help while your wallet is open",
        "what_happens": "A helper asks you to share your screen or install a remote-control app to fix a problem.",
        "they_want": "To see your phrase when you open it, or to click approve for you.",
        "you_do": "Never share your screen with a wallet open. Never install remote control for a stranger.",
        "svg": _svg('<rect x="8" y="12" width="48" height="32" rx="3"/>', '<path d="M24 52 h16 m-8 -8 v8"/>',
                    '<path d="M18 28 c8 -10 20 -10 28 0 c-8 10 -20 10 -28 0 z"/>', '<circle cx="32" cy="28" r="3.5"/>'),
    },
    {
        "id": "signature_is_money", "emoji": "✍️",
        "title": "A signature can be a payment or a permission, even when it says 'sign in'",
        "what_happens": "A site asks you to 'sign in with wallet' or 'verify ownership' by signing a message.",
        "they_want": "Some of those messages move money or grant permission. The button text means nothing; the content does.",
        "you_do": "Read the sentence VOOL shows you first. Green: exactly this, once. Red: standing power over your funds.",
        "svg": _svg('<rect x="12" y="8" width="40" height="48" rx="3"/>', '<path d="M20 20 h24 M20 28 h24 M20 36 h14"/>',
                    f'<circle cx="24" cy="48" r="4" fill="{_GREEN}" stroke="none"/>', f'<circle cx="40" cy="48" r="4" fill="{_RED}" stroke="none"/>', '<path d="M46 12 l8 -6"/>'),
    },
)


def cards() -> list[dict[str, str]]:
    return [dict(card) for card in _CARDS]


def panel() -> dict[str, object]:
    return {"golden_rule": GOLDEN_RULE, "principle": PRINCIPLE, "cards": cards()}


__all__ = ["GOLDEN_RULE", "PRINCIPLE", "cards", "panel"]
