"""The three safe machine folders, what people call them, and where they are on this disk.

VOOL reads and writes only inside the owner's Desktop, Downloads and Documents. Every lane used to
spell those three as English words of its own ("desktop", "downloads", "documents", "docs"), so an
ask in any other app language named no folder at all: "Schreibtisch", "Bureau", "Pulpit" or "桌面"
was a stranger, the admission proof refused the model's `machine.list_directory` call, and on a
Linux box set up in German the folder on disk (`~/Schreibtisch`, from `xdg-user-dirs`) was not even
one of the roots. This module is the one table those lanes read instead.

Two separate facts, never mixed:

- **Spoken names** (`root_mentions`, `root_key_for_alias`): the words a person uses for each folder in
  the 17 app languages (`core/i18n/catalogs`). They only say WHICH of the three roots is meant.
- **Disk folders** (`safe_root_paths`): on macOS and Windows the folders stay English on disk (Finder
  and Explorer only translate the label). On Linux `~/.config/user-dirs.dirs` says where each one is
  (`XDG_DESKTOP_DIR`, `XDG_DOWNLOAD_DIR`, `XDG_DOCUMENTS_DIR`); a missing file, a missing key or an
  entry that would widen the roots falls back to the English name.

The roots never grow: at most these three directories, each strictly inside the home folder. An
XDG entry that points at the home folder itself, outside it, through a symlink out of it, or into a
hidden folder is ignored for the English name.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: The three roots, in the order every lane lists them.
ROOT_KEYS = ("desktop", "downloads", "documents")

#: The folder names on a disk that does not localize them (macOS, Windows, Linux without XDG).
ENGLISH_DIR_NAMES = {"desktop": "Desktop", "downloads": "Downloads", "documents": "Documents"}

_XDG_KEYS = {"desktop": "XDG_DESKTOP_DIR", "downloads": "XDG_DOWNLOAD_DIR", "documents": "XDG_DOCUMENTS_DIR"}

# What each root is called, per app language. Each entry is a regex fragment matched
# case-insensitively. Spaced scripts are matched as whole words (``_BOUNDED``); Chinese, Japanese
# and Korean, which do not put spaces between words and attach particles, are matched as they
# stand. Inflected forms are listed where the language bends the noun ("na pulpicie", "в
# загрузках", "masaüstümde"), and an unaccented spelling where people commonly type one.
_SPOKEN: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "desktop": ("desktop",),
        "downloads": ("downloads",),
        "documents": ("documents", "docs"),
    },
    "de": {
        "desktop": ("schreibtisch",),
        "downloads": ("download-ordner", "downloadordner"),
        "documents": ("dokumente", "dokumenten", "dokumentenordner"),
    },
    "es": {
        "desktop": ("escritorio",),
        "downloads": ("descargas",),
        "documents": ("documentos",),
    },
    "fr": {
        "desktop": ("bureau",),
        "downloads": ("t[ée]l[ée]chargements",),
        "documents": (),  # "documents", as in English
    },
    "pt": {
        "desktop": ("[áa]rea de trabalho", "ambiente de trabalho"),
        "downloads": ("transfer[êe]ncias",),
        "documents": (),  # "documentos", as in Spanish
    },
    "pl": {
        "desktop": ("pulpit", "pulpicie", "pulpitu"),
        "downloads": ("pobrane", "pobranych", "pobranym"),
        "documents": ("dokumenty", "dokumentach", "dokument[óo]w", "dokumentami"),
    },
    "lt": {
        "desktop": ("darbalaukis", "darbalaukyje", "darbalaukio", "darbalauk[įi]"),
        "downloads": ("atsisiuntimai", "atsisiuntimuose", "atsisiuntim[ųu]", "atsisiuntimus"),
        "documents": ("dokumentai", "dokumentuose", "dokument[ųu]", "dokumentus"),
    },
    "tr": {
        "desktop": (r"masa[üu]st[üu]\w*",),
        "downloads": (r"[iİı]ndirilenler\w*",),
        "documents": (r"belgeler\w*",),
    },
    "vi": {
        "desktop": ("m[àa]n h[ìi]nh n[ềe]n",),
        "downloads": ("t[ảa]i v[ềe]", "t[ảa]i xu[ốo]ng"),
        "documents": ("t[àa]i li[ệe]u",),
    },
    "ru": {
        "desktop": (r"рабоч(?:ий|ем|его|ему|им)\s+стол(?:е|а|у|ом)?",),
        "downloads": ("загрузки", "загрузках", "загрузок", "загрузками"),
        "documents": ("документы", "документах", "документов", "документами"),
    },
    "uk": {
        "desktop": (r"робоч(?:ий|ому|ого|им)\s+ст(?:іл|олі|олу|олом)",),
        "downloads": ("завантаження", "завантаженнях", "завантажень"),
        "documents": ("документи", "документах", "документів"),
    },
    "ar": {
        "desktop": ("سطح المكتب",),
        "downloads": ("التنزيلات", "التحميلات", "تنزيلات"),
        "documents": ("المستندات", "مستندات"),
    },
    "he": {
        "desktop": ("שולחן העבודה",),
        "downloads": ("הורדות", "בהורדות", "ההורדות"),
        "documents": ("מסמכים", "במסמכים", "המסמכים"),
    },
    "hi": {
        "desktop": ("डेस्कटॉप",),
        "downloads": ("डाउनलोड्स", "डाउनलोड"),
        "documents": ("दस्तावेज़", "दस्तावेज", "डॉक्यूमेंट्स"),
    },
}
# Unspaced scripts: matched without word edges.
_UNBOUNDED: dict[str, dict[str, tuple[str, ...]]] = {
    "ja": {
        "desktop": ("デスクトップ",),
        "downloads": ("ダウンロード",),
        "documents": ("書類", "ドキュメント"),
    },
    "ko": {
        "desktop": (r"바탕\s?화면", "데스크톱", "데스크탑"),
        "downloads": ("다운로드",),
        "documents": ("문서",),
    },
    "zh-Hans": {
        "desktop": ("桌面",),
        "downloads": ("下载",),
        "documents": ("文稿", "文档"),
    },
}

#: The app languages this table speaks (the 17 UI catalogs).
LANGUAGES = tuple(sorted({*_SPOKEN, *_UNBOUNDED}))


def _alternation(table: dict[str, dict[str, tuple[str, ...]]], key: str) -> list[str]:
    return [fragment for words in table.values() for fragment in words.get(key, ())]


def _key_pattern(key: str) -> str:
    bounded = sorted(_alternation(_SPOKEN, key), key=len, reverse=True)
    unbounded = sorted(_alternation(_UNBOUNDED, key), key=len, reverse=True)
    parts = []
    if bounded:
        parts.append(r"(?<![\w-])(?:" + "|".join(bounded) + r")(?![\w-])")
    if unbounded:
        parts.append("(?:" + "|".join(unbounded) + ")")
    return "|".join(parts)


_KEY_RES = {key: re.compile(_key_pattern(key), re.IGNORECASE) for key in ROOT_KEYS}
#: One pattern for "any of the three", named groups per root.
ANY_ROOT_RE = re.compile(
    "|".join(f"(?P<{key}>{_key_pattern(key)})" for key in ROOT_KEYS), re.IGNORECASE
)


@dataclass(frozen=True)
class RootMention:
    key: str  # "desktop" / "downloads" / "documents"
    said: str  # the words as the person wrote them ("Schreibtisch", "pulpicie", "桌面")
    start: int
    end: int

    @property
    def english(self) -> bool:
        return re.fullmatch(_english_pattern(self.key), self.said, re.IGNORECASE) is not None


def _english_pattern(key: str) -> str:
    return "(?:" + "|".join(_SPOKEN["en"][key]) + ")"


#: Spoken names that are also everyday English words ("credit bureau", "the pulpit"). Lanes that
#: read English grammar leave them out; the model-call admission and the card match keep them.
_ENGLISH_HOMOGRAPHS = frozenset({"bureau", "pulpit"})


def root_mentions(text: str, *, english_homographs: bool = True) -> tuple[RootMention, ...]:
    """Every place the text names one of the three roots, in any app language, in text order."""

    found: list[RootMention] = []
    for match in ANY_ROOT_RE.finditer(str(text or "")):
        if not english_homographs and match.group(0).lower() in _ENGLISH_HOMOGRAPHS:
            continue
        key = next(k for k in ROOT_KEYS if match.group(k))
        found.append(RootMention(key=key, said=match.group(0), start=match.start(), end=match.end()))
    return tuple(found)


def names_a_root(text: str, *, english_homographs: bool = True) -> bool:
    return bool(root_mentions(text, english_homographs=english_homographs))


# A file or folder noun, per app language: with a root's name, it makes the sentence about what is
# IN that folder ("die Dateien auf dem Schreibtisch") rather than about a desk or an office.
_CONTENT_NOUN_RE = re.compile(
    r"(?<![\w-])(?:files?|folders?|dateien|datei|ordner|archivos?|carpetas?|fichiers?|dossiers?"
    r"|arquivos?|ficheiros?|pastas?|pliki|plik[óo]w|plik|foldery?|failai|fail[ųu]|failus|aplank\w*"
    r"|dosya\w*|klas[öo]r\w*|t[ệe]p|t[ậa]p tin|th[ưu] m[ụu]c|файл\w*|папк\w*|тек(?:а|и|у|ці)"
    r"|ملفات|ملف|مجلد\w*|קבצים|קובץ|תיקי\w*|फ़ाइल\w*|फाइल\w*|फ़ोल्डर)(?![\w-])"
    r"|ファイル|フォルダ|파일|폴더|文件",
    re.IGNORECASE,
)
_QUESTION_MARK_RE = re.compile(r"[?？؟]")


# A homograph is the folder when the sentence around it is not English: "sur mon bureau", "na
# pulpit", or a French/Polish file noun beside it.
_HOMOGRAPH_IN_ITS_LANGUAGE_RE = re.compile(
    r"(?<![\w-])(?:mon|ton|le|du|au|na|m[óo]j|moim|w)\s+(?:bureau|pulpit)(?![\w-])", re.IGNORECASE
)
_ENGLISH_NOUNS = frozenset({"file", "files", "folder", "folders"})


def asks_about_a_root(text: str, *, need_noun: bool = False) -> bool:
    """The words name one of the three roots AND ask about it: a question, or a file/folder noun.

    "Schreibtisch" alone is also a desk and "bureau" an office; a question about it, or the files
    on it, is the folder. ``need_noun`` asks for the file/folder noun (a question mark alone is not
    enough). Any app language, read only: this decides nothing beyond "the turn named a root as
    its target". A word that is English too ("credit bureau") counts only inside its own language.
    """

    value = str(text or "")
    mentions = root_mentions(value)
    if not mentions:
        return False
    nouns = [match.group(0).lower() for match in _CONTENT_NOUN_RE.finditer(value)]
    if all(m.said.lower() in _ENGLISH_HOMOGRAPHS for m in mentions):
        foreign_noun = any(noun not in _ENGLISH_NOUNS for noun in nouns)
        if not (foreign_noun or _HOMOGRAPH_IN_ITS_LANGUAGE_RE.search(value)):
            return False
    if need_noun:
        return bool(nouns)
    return bool(nouns or _QUESTION_MARK_RE.search(value))


def root_key_for_alias(raw: str) -> str:
    """The root a bare folder argument means ("Schreibtisch", "my Desktop", "~/Pobrane"), or ""."""

    value = " ".join(str(raw or "").strip().split()).rstrip("/\\")
    value = re.sub(r"^~[/\\]", "", value)
    value = re.sub(r"^(?:my|mein(?:e|em|en)?|mon|ma|mes|mi|mis|meu|minha|moj[ea]?|mano)\s+", "", value, flags=re.IGNORECASE)
    for key, pattern in _KEY_RES.items():
        if pattern.fullmatch(value):
            return key
    return ""


def canonical_folder_words(text: str) -> str:
    """The text with each spoken root name replaced by its English word (for word matching only)."""

    def swap(match: re.Match[str]) -> str:
        key = next(k for k in ROOT_KEYS if match.group(k))
        return f" {key} "

    return ANY_ROOT_RE.sub(swap, str(text or ""))


# -- where the folders are on this disk -----------------------------------------------------------


def _platform() -> str:
    return sys.platform


def _home() -> Path:
    try:
        return Path.home()
    except Exception:
        value = str(os.environ.get("HOME") or os.environ.get("USERPROFILE") or "").strip()
        return Path(value or "~").expanduser()


def _xdg_entries(home: Path) -> dict[str, str]:
    config = str(os.environ.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(config) if config and Path(config).is_absolute() else home / ".config"
    try:
        raw = (base / "user-dirs.dirs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    entries: dict[str, str] = {}
    for line in raw.splitlines():
        match = re.match(r'^\s*(XDG_[A-Z]+_DIR)\s*=\s*"([^"]*)"\s*$', line)
        if match:
            entries[match.group(1)] = match.group(2)
    return entries


def _xdg_dir(value: str, home: Path) -> Path | None:
    """A user-dirs.dirs value as a directory strictly inside home, or None when it would widen."""

    if value.startswith("$HOME/"):
        candidate = home / value[len("$HOME/") :]
    elif value.startswith("/"):
        candidate = Path(value)
    else:
        return None  # "$HOME" alone, a relative path, another variable: not a root
    try:
        home_resolved = home.resolve()
        resolved = candidate.resolve()
        relative = resolved.relative_to(home_resolved)
    except (OSError, ValueError):
        return None  # outside home, directly or through a symlink
    parts = relative.parts
    if not parts or any(part.startswith(".") for part in parts):
        return None  # the home folder itself, or a hidden folder
    return resolved


def safe_root_dir(key: str, home: Path | None = None) -> Path:
    """Where root ``key`` lives on this disk (resolved)."""

    home = home if home is not None else _home()
    if _platform().startswith("linux"):
        value = _xdg_entries(home).get(_XDG_KEYS[key], "")
        if value:
            located = _xdg_dir(value, home)
            if located is not None:
                return located
    return (home / ENGLISH_DIR_NAMES[key]).resolve()


def safe_root_paths(home: Path | None = None) -> tuple[Path, ...]:
    """The three safe roots on this disk, Desktop/Downloads/Documents order, never more."""

    home = home if home is not None else _home()
    roots: list[Path] = []
    for key in ROOT_KEYS:
        root = safe_root_dir(key, home)
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def root_key_of_path(path: Path, home: Path | None = None) -> str:
    """Which root ``path`` is (exactly), or ""."""

    home = home if home is not None else _home()
    for key in ROOT_KEYS:
        if safe_root_dir(key, home) == path:
            return key
    return ""


__all__ = [
    "ANY_ROOT_RE",
    "ENGLISH_DIR_NAMES",
    "LANGUAGES",
    "ROOT_KEYS",
    "RootMention",
    "asks_about_a_root",
    "canonical_folder_words",
    "names_a_root",
    "root_key_for_alias",
    "root_key_of_path",
    "root_mentions",
    "safe_root_dir",
    "safe_root_paths",
]
