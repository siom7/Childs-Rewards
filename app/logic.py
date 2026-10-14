"""Calculs de points : soldes, résumés de semaine, calendrier."""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import config
from .models import APPROVED, BONUS, HABIT, PENALTY, PENDING, REWARD, AppSetting, Child, Entry

def now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


def today() -> date:
    return now().date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def kid_can_edit(d: date) -> bool:
    """Les enfants ne modifient que la journée en cours ; les jours passés sont en lecture seule pour eux."""
    return d == today()


def children(db: Session, include_inactive: bool = False) -> list[Child]:
    q = select(Child).order_by(Child.sort, Child.id)
    if not include_inactive:
        q = q.where(Child.active)
    return list(db.scalars(q))


def get_setting(db: Session, key: str, default: str = "") -> str:
    return db.scalar(select(AppSetting.value).where(AppSetting.key == key)) or default


def set_setting(db: Session, key: str, value: str) -> None:
    setting = db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value=value))
    else:
        setting.value = value


def balance(db: Session, child: Child, before: date | None = None) -> int:
    """Solde validé (avant la date `before` si elle est fournie)."""
    q = select(func.coalesce(func.sum(Entry.points), 0)).where(
        Entry.child_id == child.id, Entry.status == APPROVED)
    if before is not None:
        q = q.where(Entry.day < before)
    return child.starting_balance + child.archived_points + db.scalar(q)


def pending_reward_cost(db: Session, child_id: int) -> int:
    """Points réservés par des demandes de récompense en attente."""
    return -db.scalar(select(func.coalesce(func.sum(Entry.points), 0)).where(
        Entry.child_id == child_id, Entry.kind == REWARD, Entry.status == PENDING))


def pending_count(db: Session, child_id: int | None = None) -> int:
    q = select(func.count(Entry.id)).where(Entry.status == PENDING)
    if child_id is not None:
        q = q.where(Entry.child_id == child_id)
    return db.scalar(q)


def day_entries(db: Session, child_id: int, day: date) -> list[Entry]:
    return list(db.scalars(select(Entry).where(Entry.child_id == child_id, Entry.day == day)
                           .order_by(Entry.created_at, Entry.id)))


def day_net(db: Session, child_id: int, day: date) -> int:
    return db.scalar(select(func.coalesce(func.sum(Entry.points), 0)).where(
        Entry.child_id == child_id, Entry.day == day, Entry.status == APPROVED))


def _sums_by_day(db: Session, child_id: int, first: date, last: date) -> dict[date, dict[str, int]]:
    rows = db.execute(
        select(Entry.day, Entry.kind, func.sum(Entry.points))
        .where(Entry.child_id == child_id, Entry.status == APPROVED, Entry.day.between(first, last))
        .group_by(Entry.day, Entry.kind))
    sums: dict[date, dict[str, int]] = {}
    for day, kind, total in rows:
        sums.setdefault(day, {})[kind] = total
    return sums


def week_summary(db: Session, child: Child, start: date) -> dict:
    """Reproduit la fiche papier : une ligne par jour, gagnés / retirés / bonus / récompenses / total."""
    sums = _sums_by_day(db, child.id, start, start + timedelta(days=6))
    opening = running = balance(db, child, before=start)
    t = today()
    rows = []
    for i in range(7):
        d = start + timedelta(days=i)
        s = sums.get(d, {})
        earned, removed = s.get(HABIT, 0), -s.get(PENALTY, 0)
        bonus, used = s.get(BONUS, 0), -s.get(REWARD, 0)
        running += earned - removed + bonus - used
        rows.append({"day": d, "earned": earned, "removed": removed, "bonus": bonus,
                     "used": used, "total": running if d <= t else None, "future": d > t})
    return {"child": child, "opening": opening, "closing": running, "rows": rows}


def month_weeks(year: int, month: int) -> list[list[date]]:
    return calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)


def month_activity(db: Session, child_id: int, first: date, last: date) -> tuple[dict[date, int], set[date]]:
    """Total validé par jour, et jours ayant des saisies en attente."""
    nets = dict(db.execute(
        select(Entry.day, func.sum(Entry.points))
        .where(Entry.child_id == child_id, Entry.status == APPROVED, Entry.day.between(first, last))
        .group_by(Entry.day)).all())
    pending = set(db.scalars(
        select(Entry.day).where(Entry.child_id == child_id, Entry.status == PENDING,
                                Entry.day.between(first, last)).distinct()))
    return nets, pending


# --- Rétention de l'historique ---------------------------------------------------------------

RETENTION_CHOICES = (6, 12, 24, 0)  # en mois ; 0 = illimité
DEFAULT_RETENTION_MONTHS = 12


def retention_months(db: Session) -> int:
    try:
        months = int(get_setting(db, "retention_months", str(DEFAULT_RETENTION_MONTHS)))
    except ValueError:
        return DEFAULT_RETENTION_MONTHS
    return months if months in RETENTION_CHOICES else DEFAULT_RETENTION_MONTHS


def retention_cutoff(months: int, ref: date | None = None) -> date | None:
    """Premier jour conservé (les saisies antérieures sont effacées), None si illimité."""
    if not months:
        return None
    ref = ref or today()
    year, month = divmod(ref.year * 12 + ref.month - 1 - months, 12)
    return date(year, month + 1, min(ref.day, calendar.monthrange(year, month + 1)[1]))


def purge_old_entries(db: Session, ref: date | None = None) -> int:
    """Efface les saisies plus anciennes que la durée de conservation.

    Le total des saisies validées effacées est reporté dans `Child.archived_points` : le solde est inchangé.
    Retourne le nombre de saisies effacées.
    """
    ref = ref or today()
    set_setting(db, "last_purge", ref.isoformat())
    cutoff = retention_cutoff(retention_months(db), ref)
    if cutoff is None:
        db.commit()
        return 0
    totals = db.execute(select(Entry.child_id, func.sum(Entry.points))
                        .where(Entry.day < cutoff, Entry.status == APPROVED).group_by(Entry.child_id)).all()
    for child_id, total in totals:
        db.get(Child, child_id).archived_points += total
    count = db.execute(delete(Entry).where(Entry.day < cutoff)).rowcount
    db.commit()
    return count


def purge_if_due(db: Session) -> None:
    """Nettoyage au plus une fois par jour, déclenché par l'ouverture de l'accueil."""
    if get_setting(db, "last_purge") != today().isoformat():
        purge_old_entries(db)
