"""Configuration, lue depuis les variables d'environnement (préfixe CR_)."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CR_DATA_DIR", BASE_DIR.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.environ.get("CR_DATABASE_URL", f"sqlite:///{DATA_DIR / 'childs_rewards.db'}")
TIMEZONE = os.environ.get("CR_TIMEZONE", "Europe/Paris")
# À mettre à 1 en production (derrière NGINX en HTTPS) : cookie envoyé uniquement en HTTPS.
COOKIE_SECURE = os.environ.get("CR_COOKIE_SECURE", "0") == "1"


def _secret_key() -> str:
    """Clé de signature des cookies : variable d'env, sinon générée une fois dans data/."""
    if os.environ.get("CR_SECRET_KEY"):
        return os.environ["CR_SECRET_KEY"]
    key_file = DATA_DIR / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(48))
    return key_file.read_text().strip()


SECRET_KEY = _secret_key()
