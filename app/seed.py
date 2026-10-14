"""Règles d'exemple proposées à l'installation (reprises des fiches papier d'origine)."""
from sqlalchemy.orm import Session

from .i18n import _
from .models import Habit, Reward, Rule

# Couleurs proposées pour les enfants, dans l'ordre
CHILD_COLORS = ["#f472b6", "#60a5fa", "#34d399", "#fbbf24", "#a78bfa", "#fb923c", "#2dd4bf", "#f87171"]

HABITS = [
    "Ranger mon cartable / sac de sport et mes chaussures",
    "Goûter avec un fruit puis débarrasser",
    "Faire mes devoirs à table / au bureau",
    "Prendre ma douche sans râler",
    "Ne rien laisser traîner",
    "Bien manger et débarrasser",
    "Lire 15 min après avoir brossé mes dents",
    "Me coucher à l'heure",
]

# (libellé, points retirés, maximum par jour)
RULES = [
    ("Répondre ou réagir impoliment", 1, 3),
    ("Laisser traîner mes affaires", 2, None),
    ("Réclamer des écrans", 2, None),
    ("Se disputer fort", 3, None),
    ("Désobéir", 3, None),
]

REWARDS = [
    ("Manger une friandise", 5),
    ("Choisir le repas", 8),
    ("10 min de temps de détente", 10),
    ("15 min d'écran", 15),
    ("Se coucher 20 min plus tard", 20),
    ("Cadeau surprise", 30),
    ("5 € à dépenser en magasin", 40),
    ("Sortie ou activité spéciale", 50),
]


def seed_examples(db: Session) -> None:
    """Ajoute les habitudes, règles et récompenses d'exemple, traduites dans la langue choisie
    (modifiables ensuite dans les réglages)."""
    db.add_all(Habit(label=_(l), points=1, sort=i) for i, l in enumerate(HABITS))
    db.add_all(Rule(label=_(l), points=p, max_per_day=m, sort=i) for i, (l, p, m) in enumerate(RULES))
    db.add_all(Reward(label=_(l), points=p, sort=i) for i, (l, p) in enumerate(REWARDS))
