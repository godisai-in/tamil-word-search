"""Shared text normalisation. Used by ingest AND by query-time lookup, so both
sides produce identical `norm` keys (and therefore identical document ids)."""
import hashlib
import re
import unicodedata

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_TAMIL = re.compile(r"[஀-௿]")
_LATIN = re.compile(r"[A-Za-z]")
_PAREN = re.compile(r"\([^)]*\)")

MAX_TOKENS = 8          # headwords
MAX_TARGET_TOKENS = 4   # words split out of a meaning field: longer ones are descriptions
MAX_CHARS = 80


def norm(s: str) -> str:
    """NFC, drop zero-width chars, collapse whitespace, strip bullets/punctuation
    at the ends, lowercase (no-op for Tamil)."""
    s = unicodedata.normalize("NFC", s).translate(_INVISIBLE).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # NOTE: do not use \W / \w here: Python treats Tamil vowel signs and the
    # virama (category Mn/Mc) as non-word chars and would strip real letters.
    i, j = 0, len(s)
    while i < j and not s[i].isalpha():          # leading numbering, bullets, punctuation
        i += 1
    while j > i and unicodedata.category(s[j - 1])[0] in "PSZC":   # trailing punctuation only
        j -= 1
    return s[i:j].lower()


def lang_of(s: str):
    if _TAMIL.search(s):
        return "ta"
    if _LATIN.search(s):
        return "en"
    return None


def usable(n: str, headword: bool = True) -> bool:
    """A normalised string we are willing to treat as a word/phrase."""
    if not n or len(n) > MAX_CHARS or not any(c.isalpha() for c in n):
        return False
    t = len(n.split())
    if t > (MAX_TOKENS if headword else MAX_TARGET_TOKENS):
        return False
    # Tamil + English letters in a 4+ token string is a gloss/description, not a word.
    if t >= 4 and _TAMIL.search(n) and _LATIN.search(n):
        return False
    return True


def split_meaning(m: str):
    """Split a dictionary meaning field into candidate words. Heuristic: the
    '(மறுவ.)' marker ('also called') and ';' ',' '/' separate items;
    parenthetical notes are dropped."""
    m = m.replace("(மறுவ.)", ";")
    m = _PAREN.sub("", m)
    return [p for p in re.split(r"[;,/]", m) if p.strip()]


def word_id(lang: str, n: str) -> str:
    """Deterministic document id: same word => same id on every run / machine."""
    return hashlib.sha1(f"{lang}\x1f{n}".encode()).hexdigest()[:20]
