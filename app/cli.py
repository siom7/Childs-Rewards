"""Commandes d'administration, à lancer sur le serveur (sans e-mail : l'accès au serveur fait foi).

    python -m app.cli list-users
    python -m app.cli reset-password <identifiant>     # mot de passe d'un parent
    python -m app.cli reset-pin                        # code PIN des enfants
    python -m app.cli backup <dossier>                 # copie de sauvegarde de la base (garde 30 copies)

Le nouveau mot de passe / code est demandé de façon masquée (rien n'apparaît dans l'historique du shell).
Avec Docker : docker compose exec app python -m app.cli reset-password <identifiant>
"""
import argparse
import getpass
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from . import config
from .db import SessionLocal, upgrade_schema
from .main import KIDS_USERNAME, PIN_RE
from .models import KIDS, PARENT, User
from .security import hash_password


def _ask(prompt: str, check, error: str) -> str:
    for _attempt in range(3):
        value = getpass.getpass(prompt)
        if not check(value):
            print(error)
            continue
        if getpass.getpass("Confirmer / Confirm : ") != value:
            print("Les deux saisies ne correspondent pas. / Entries do not match.")
            continue
        return value
    sys.exit(1)


def list_users() -> int:
    with SessionLocal() as db:
        for u in db.scalars(select(User).order_by(User.role.desc(), User.id)):
            role = "parent" if u.role == PARENT else "enfants / children (PIN)"
            print(f"{u.username:<30} {u.display_name:<25} {role}")
    return 0


def reset_password(username: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username.strip().lower(), User.role == PARENT))
        if user is None:
            print(f"Parent « {username} » introuvable. / Parent not found. Voir / see: list-users")
            return 1
        password = _ask("Nouveau mot de passe / New password : ", lambda v: len(v) >= 8,
                        "8 caractères minimum. / At least 8 characters.")
        user.password_hash = hash_password(password)
        db.commit()
    print("Mot de passe modifié. / Password changed.")
    return 0


def reset_pin() -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.role == KIDS, User.username == KIDS_USERNAME))
        if user is None:
            print("Compte enfants introuvable : l'installation n'est pas terminée. / Setup not completed.")
            return 1
        pin = _ask("Nouveau code PIN / New PIN : ", lambda v: re.fullmatch(PIN_RE, v) is not None,
                   "4 à 8 chiffres. / 4 to 8 digits.")
        user.password_hash = hash_password(pin)
        db.commit()
    print("Code PIN modifié. / PIN changed.")
    return 0


def backup(target_dir: str, keep: int = 30) -> int:
    """Copie cohérente de la base SQLite (même pendant que l'application tourne)."""
    source = config.DATA_DIR / "childs_rewards.db"
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"childs_rewards-{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
    for old in sorted(target.glob("childs_rewards-*.db"))[:-keep]:
        old.unlink()
    print(f"Sauvegarde / backup : {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administration de Points Famille")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-users", help="Lister les comptes / list accounts")
    p = sub.add_parser("reset-password", help="Réinitialiser le mot de passe d'un parent / reset a parent password")
    p.add_argument("username")
    sub.add_parser("reset-pin", help="Réinitialiser le code PIN des enfants / reset the children's PIN")
    p = sub.add_parser("backup", help="Sauvegarder la base / back up the database")
    p.add_argument("target_dir")
    args = parser.parse_args(argv)

    upgrade_schema()
    if args.command == "list-users":
        return list_users()
    if args.command == "reset-password":
        return reset_password(args.username)
    if args.command == "backup":
        return backup(args.target_dir)
    return reset_pin()


if __name__ == "__main__":
    sys.exit(main())
