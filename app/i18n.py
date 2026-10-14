"""Traductions.

Le français est la langue source : les textes sont écrits en français dans le code et les templates,
entourés de `_()`. Les fichiers `app/locales/<code>.json` associent chaque texte français à sa traduction.
Un texte absent d'un catalogue reste affiché en français.
"""
import json
from contextvars import ContextVar
from datetime import date
from pathlib import Path

LANGUAGES = {
    "fr": "Français",
    "en": "English",
    "es": "Español",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
}
DEFAULT_LANGUAGE = "fr"

_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
_CATALOGS: dict[str, dict[str, str]] = {
    code: json.loads((_LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
    for code in LANGUAGES if code != DEFAULT_LANGUAGE
}

_current: ContextVar[str] = ContextVar("language", default=DEFAULT_LANGUAGE)

WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]
# Ordre jour / mois propre à chaque langue
_DATE_FORMATS = {
    "en": "{weekday}, {month} {day}",
    "de": "{weekday}, {day}. {month}",
    "pt": "{weekday}, {day} de {month}",
    "es": "{weekday} {day} de {month}",
}


def set_language(code: str) -> None:
    _current.set(code if code in LANGUAGES else DEFAULT_LANGUAGE)


def get_language() -> str:
    return _current.get()


def gettext(message: str) -> str:
    lang = _current.get()
    if lang == DEFAULT_LANGUAGE:
        return message
    return _CATALOGS[lang].get(message) or message


_ = gettext


def best_match(accept_language: str | None) -> str:
    """Langue proposée par défaut à l'installation, d'après le navigateur."""
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def format_date(d: date, year: bool = False) -> str:
    fmt = _DATE_FORMATS.get(_current.get(), "{weekday} {day} {month}")
    text = fmt.format(weekday=_(WEEKDAYS[d.weekday()]), day=d.day, month=_(MONTHS[d.month - 1]))
    return f"{text} {d.year}" if year else text


def format_month(year: int, month: int) -> str:
    return f"{_(MONTHS[month - 1])} {year}"


def weekday_initials() -> list[str]:
    return [_(w)[:1].upper() for w in WEEKDAYS]


def weekday_short(d: date) -> str:
    return _(WEEKDAYS[d.weekday()])[:3]
