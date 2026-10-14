"""Configuration, lue depuis les variables d'environnement (préfixe CR_)."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CR_DATA_DIR", BASE_DIR.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("CR_DATABASE_URL", f"sqlite:///{DATA_DIR / 'childs_rewards.db'}")
TIMEZONE = os.environ.get("CR_TIMEZONE", "Europe/Paris")
# Attribut « Secure » du cookie de session :
#   auto (défaut) = seulement quand la page est servie en HTTPS (directement ou via le reverse proxy),
#   1 = toujours (le site ne marche alors qu'en HTTPS), 0 = jamais.
COOKIE_SECURE = os.environ.get("CR_COOKIE_SECURE", "auto").strip().lower()
if COOKIE_SECURE not in ("auto", "1", "0"):
    COOKIE_SECURE = "auto"


def _secret_key() -> str:
    """Clé de signature des cookies : variable d'env, sinon générée une fois dans data/."""
    if os.environ.get("CR_SECRET_KEY"):
        return os.environ["CR_SECRET_KEY"]
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
    return key_file.read_text().strip()


SECRET_KEY = _secret_key()
