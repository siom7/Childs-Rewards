"""Liste les textes à traduire et vérifie les catalogues app/locales/*.json.

Usage :
    python tools/i18n_extract.py          # affiche les clés manquantes / inutilisées par langue
    python tools/i18n_extract.py --keys   # affiche toutes les clés (JSON)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import i18n, seed  # noqa: E402

# _("...") ou _('...') dans le Python et les templates Jinja
PATTERN = re.compile(r"""\b_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*\)""")


def source_keys() -> set[str]:
    keys: set[str] = set()
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "app" / "templates").rglob("*.html")):
        for m in PATTERN.finditer(path.read_text(encoding="utf-8")):
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            keys.add(raw.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
                     if "\\" in raw else raw)
    # Textes passés à _() par variable
    keys.update(i18n.WEEKDAYS, i18n.MONTHS, seed.HABITS)
    keys.update(label for label, *_ in seed.RULES)
    keys.update(label for label, _p in seed.REWARDS)
    keys.discard("")
    return keys


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    keys = source_keys()
    if "--keys" in sys.argv:
        print(json.dumps(sorted(keys), ensure_ascii=False, indent=1))
        return 0
    status = 0
    for code in i18n.LANGUAGES:
        if code == i18n.DEFAULT_LANGUAGE:
            continue
        catalog = json.loads((ROOT / "app" / "locales" / f"{code}.json").read_text(encoding="utf-8"))
        missing = sorted(keys - catalog.keys())
        unused = sorted(catalog.keys() - keys)
        print(f"{code}: {len(catalog)} traductions, {len(missing)} manquantes, {len(unused)} inutilisées")
        for k in missing:
            print(f"   - manquante : {k}")
        for k in unused:
            print(f"   - inutilisée : {k}")
        status |= bool(missing)
    return status


if __name__ == "__main__":
    sys.exit(main())
