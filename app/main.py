import json
import re
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import config, i18n, logic
from .db import SessionLocal, get_db, upgrade_schema
from .i18n import _
from .models import (APPROVED, BONUS, HABIT, KIDS, PARENT, PENALTY, PENDING, REJECTED, REWARD,
                     Child, Entry, Habit, Reward, Rule, User)
from .security import LoginThrottle, hash_password, verify_password
from .seed import CHILD_COLORS, seed_examples

KIDS_USERNAME = "enfants"
USERNAME_RE = r"[a-z0-9._-]{3,30}"
COLOR_RE = r"#[0-9a-fA-F]{6}"
PIN_RE = r"\d{4,8}"
MAX_CHILDREN = 10
STATIC_DIR = config.BASE_DIR / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    upgrade_schema()
    yield


# --- Langue ------------------------------------------------------------------------------------

_language_cache: dict[str, str] = {}


def family_language() -> str:
    """Langue choisie à l'installation ("" tant que l'application n'est pas installée)."""
    if "lang" not in _language_cache:
        with SessionLocal() as db:
            _language_cache["lang"] = logic.get_setting(db, "language")
    return _language_cache["lang"]


def clear_language_cache() -> None:
    _language_cache.clear()


class LanguageMiddleware:
    """Fixe la langue de la requête : celle de la famille, sinon (installation) ?lang= ou le navigateur."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            lang = family_language()
            if not lang:
                request = Request(scope)
                lang = request.query_params.get("lang") or i18n.best_match(request.headers.get("accept-language"))
            i18n.set_language(lang)
        await self.app(scope, receive, send)


SESSION_COOKIE = "cr_session"


class SecureCookieMiddleware:
    """Mode CR_COOKIE_SECURE=auto : ajoute « Secure » au cookie de session uniquement pour les requêtes HTTPS.

    Ainsi l'installation fonctionne en http://IP:8000 (avant la mise en place du HTTPS), et le cookie
    est protégé dès que l'accès passe par le reverse proxy HTTPS (uvicorn --proxy-headers).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("scheme") != "https":
            return await self.app(scope, receive, send)

        async def send_secure(message):
            if message["type"] == "http.response.start":
                headers = []
                for name, value in message.get("headers", []):
                    if (name.lower() == b"set-cookie" and value.startswith(SESSION_COOKIE.encode() + b"=")
                            and b"secure" not in value.lower()):
                        value += b"; secure"
                    headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_secure)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LanguageMiddleware)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie=SESSION_COOKIE,
                   max_age=90 * 24 * 3600, same_site="lax", https_only=config.COOKIE_SECURE == "1")
if config.COOKIE_SECURE == "auto":
    app.add_middleware(SecureCookieMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=config.BASE_DIR / "templates")
templates.env.globals["_"] = _
templates.env.globals["get_language"] = i18n.get_language
templates.env.globals["weekday_initials"] = i18n.weekday_initials
templates.env.filters["date"] = i18n.format_date
templates.env.filters["weekday_short"] = i18n.weekday_short
templates.env.filters["signed"] = lambda n: f"+{n}" if n > 0 else str(n)

throttle = LoginThrottle()


# --- Authentification, CSRF, rendu -------------------------------------------------------------

class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
async def _login_required(_request: Request, _exc: LoginRequired):
    with SessionLocal() as db:
        target = "/login" if _has_users(db) else "/setup"
    return RedirectResponse(target, 303)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if exc.status_code != 404 or exc.detail != "Not Found" else _("Page introuvable.")
    return templates.TemplateResponse(
        request, "error.html",
        {"status": exc.status_code, "detail": detail, "user": None, "flashes": [], "csrf": "", "family": ""},
        status_code=exc.status_code)


def _has_users(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None:
        request.session.pop("uid", None)
        raise LoginRequired()
    return user


def parent_user(user: User = Depends(current_user)) -> User:
    if not user.is_parent:
        raise HTTPException(403, _("Réservé aux parents."))
    return user


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


# Textes marqués avec _() pour l'extraction ; traduits au moment de l'erreur par _(MSG_...).
MSG_HTTPS_ONLY = _("Le site est configuré pour fonctionner uniquement en HTTPS (CR_COOKIE_SECURE=1) mais cette page est ouverte en http://. Ouvrez-la en https:// ou mettez CR_COOKIE_SECURE=auto.")
MSG_NO_COOKIE = _("Le navigateur n'a pas conservé le cookie de session. Vérifiez que les cookies sont autorisés pour ce site, puis rechargez la page.")


async def check_csrf(request: Request, csrf: str = Form("")):
    expected = request.session.get("csrf", "")
    if not expected:
        # Aucun cookie de session reçu : cookies bloqués, ou cookie « Secure » sur une page en http://
        if config.COOKIE_SECURE == "1" and request.url.scheme != "https":
            raise HTTPException(400, _(MSG_HTTPS_ONLY))
        raise HTTPException(400, _(MSG_NO_COOKIE))
    if not secrets.compare_digest(csrf, expected):
        raise HTTPException(400, _("Le formulaire a expiré, recharge la page et réessaie."))


CSRF = [Depends(check_csrf)]


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = request.session.get("flash", []) + [[kind, message]]


def render(request: Request, name: str, user: User | None = None, db: Session | None = None, **ctx):
    pending = logic.pending_count(db) if user is not None and user.is_parent and db is not None else 0
    family = logic.get_setting(db, "family_name") if db is not None else ""
    context = {"user": user, "csrf": csrf_token(request), "flashes": request.session.pop("flash", []),
               "today": logic.today(), "pending_total": pending, "path": request.url.path,
               "family": family, **ctx}
    return templates.TemplateResponse(request, name, context)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, 303)


def back_to(next_url: str, fallback: str) -> RedirectResponse:
    """Retour à la page d'origine (chemin local uniquement), sinon à `fallback`."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(fallback)


def day_url(child_id: int, day: date) -> str:
    return f"/enfant/{child_id}/jour/{day.isoformat()}"


def get_child(db: Session, child_id: int) -> Child:
    child = db.get(Child, child_id)
    if child is None:
        raise HTTPException(404, _("Enfant introuvable."))
    return child


def check_day(user: User, day: date) -> None:
    if day > logic.today():
        raise HTTPException(400, _("Impossible de saisir des points dans le futur."))
    if not user.is_parent and not logic.kid_can_edit(day):
        raise HTTPException(403, _("Cette journée ne peut être modifiée que par un parent."))


def new_entry(user: User, child: Child, day: date, kind: str, label: str, points: int, **extra) -> Entry:
    """Les saisies d'un parent sont validées d'office, celles des enfants attendent une validation."""
    now = logic.now()
    status = APPROVED if user.is_parent else PENDING
    return Entry(child_id=child.id, day=day, kind=kind, label=label, points=points, status=status,
                 created_by_id=user.id, created_at=now,
                 validated_by_id=user.id if user.is_parent else None,
                 validated_at=now if user.is_parent else None, **extra)


def approve(entry: Entry, user: User) -> None:
    entry.status = APPROVED
    entry.validated_by_id = user.id
    entry.validated_at = logic.now()


def catalog_items(db: Session, model, active_only: bool = False):
    q = select(model).order_by(model.sort, model.id)
    if active_only:
        q = q.where(model.active)
    return db.scalars(q).all()


def _parse_int(value: str, default: int = 0) -> int | None:
    try:
        return int(value) if value.strip() else default
    except ValueError:
        return None


def _parse_points(value: str) -> int | None:
    n = _parse_int(value, default=-1)
    return n if n is not None and 1 <= n <= 1000 else None


def _parse_parent(username: str, name: str, password: str, errors: list[str]) -> tuple[str, str, str]:
    username = username.strip().lower()
    if not re.fullmatch(USERNAME_RE, username):
        errors.append(_("Identifiant parent : 3 à 30 caractères (lettres minuscules, chiffres, . _ -)."))
    elif username == KIDS_USERNAME:
        errors.append(_("L'identifiant « {name} » est réservé au compte des enfants.").format(name=KIDS_USERNAME))
    if len(password) < 8:
        errors.append(_("Mot de passe parent : 8 caractères minimum."))
    return username, (name.strip() or username)[:50], password


# --- Fichiers PWA ------------------------------------------------------------------------------

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest(db: Session = Depends(get_db)):
    family = logic.get_setting(db, "family_name")
    name = _("Points Famille") + (f" · {family}" if family else "")
    data = {
        "name": name, "short_name": _("Points"), "lang": i18n.get_language(),
        "description": _("Suivi des points et récompenses des enfants"),
        "start_url": "/", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#1e1b4b", "theme_color": "#1e1b4b",
        "icons": [
            {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"},
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(data, ensure_ascii=False), media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# --- Première installation ---------------------------------------------------------------------

@app.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request, db: Session = Depends(get_db)):
    if _has_users(db):
        return redirect("/login")
    return render(request, "setup.html", db=db, form=None, colors=CHILD_COLORS, languages=i18n.LANGUAGES)


@app.post("/setup", dependencies=CSRF)
async def setup_submit(request: Request, db: Session = Depends(get_db)):
    if _has_users(db):
        return redirect("/login")
    form = await request.form()
    language = str(form.get("language", ""))
    if language not in i18n.LANGUAGES:
        language = i18n.DEFAULT_LANGUAGE
    i18n.set_language(language)
    errors: list[str] = []

    family = str(form.get("family_name", "")).strip()[:50]
    if not family:
        errors.append(_("Indiquez le nom de la famille."))

    # Parents : lignes dont l'identifiant ou le mot de passe est rempli
    parents = []
    for u, n, p in zip(form.getlist("parent_username"), form.getlist("parent_name"),
                       form.getlist("parent_password")):
        if str(u).strip() or str(p):
            parents.append(_parse_parent(str(u), str(n), str(p), errors))
    if not parents:
        errors.append(_("Ajoutez au moins un parent."))
    if len({p[0] for p in parents}) != len(parents):
        errors.append(_("Chaque parent doit avoir un identifiant différent."))

    # Enfants : lignes dont le prénom est rempli
    kids = []
    for n, c, b in zip(form.getlist("child_name"), form.getlist("child_color"), form.getlist("child_balance")):
        name = str(n).strip()[:50]
        if not name:
            continue
        balance = _parse_int(str(b))
        if balance is None:
            errors.append(_("Points de départ invalides pour {name}.").format(name=name))
        color = str(c) if re.fullmatch(COLOR_RE, str(c)) else CHILD_COLORS[len(kids) % len(CHILD_COLORS)]
        kids.append((name, color, balance or 0))
    if not kids:
        errors.append(_("Ajoutez au moins un enfant."))
    if len(kids) > MAX_CHILDREN:
        errors.append(_("{n} enfants maximum.").format(n=MAX_CHILDREN))

    pin = str(form.get("kids_pin", ""))
    if not re.fullmatch(PIN_RE, pin):
        errors.append(_("Code PIN des enfants : 4 à 8 chiffres."))

    if errors:
        for e in dict.fromkeys(errors):
            flash(request, e, "error")
        return render(request, "setup.html", db=db, form=form, colors=CHILD_COLORS, languages=i18n.LANGUAGES)

    logic.set_setting(db, "family_name", family)
    logic.set_setting(db, "language", language)
    users = [User(username=u, display_name=n, role=PARENT, password_hash=hash_password(p))
             for u, n, p in parents]
    users.append(User(username=KIDS_USERNAME, display_name=_("Enfants"), role=KIDS,
                      password_hash=hash_password(pin)))
    db.add_all(users)
    db.add_all(Child(name=n, color=c, starting_balance=b, sort=i) for i, (n, c, b) in enumerate(kids))
    if form.get("examples"):
        seed_examples(db)
    db.commit()
    clear_language_cache()
    request.session["uid"] = users[0].id
    flash(request, _("Installation terminée, bienvenue !"))
    return redirect("/")


# --- Connexion ---------------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_db)):
    if not _has_users(db):
        return redirect("/setup")
    if request.session.get("uid"):
        return redirect("/")
    return render(request, "login.html", db=db, kids_username=KIDS_USERNAME)


@app.post("/login", dependencies=CSRF)
def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                 db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if throttle.is_blocked(ip):
        flash(request, _("Trop de tentatives. Réessaie dans 15 minutes."), "error")
        return redirect("/login")
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        throttle.record_failure(ip)
        flash(request, _("Identifiant ou code incorrect."), "error")
        return redirect("/login")
    throttle.reset(ip)
    request.session.clear()
    request.session["uid"] = user.id
    return redirect("/")


@app.post("/logout", dependencies=CSRF)
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


# --- Accueil -----------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    logic.purge_if_due(db)
    t = logic.today()
    cards = [{"child": c, "balance": logic.balance(db, c), "today": logic.day_net(db, c.id, t),
              "pending": logic.pending_count(db, c.id)} for c in logic.children(db)]
    return render(request, "home.html", user, db, cards=cards)


# --- Journée d'un enfant -----------------------------------------------------------------------

@app.get("/enfant/{child_id}")
def child_today(child_id: int, _user: User = Depends(current_user)):
    return redirect(day_url(child_id, logic.today()))


@app.get("/enfant/{child_id}/jour/{day}", response_class=HTMLResponse)
def day_page(request: Request, child_id: int, day: date, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    t = logic.today()
    if day > t:
        return redirect(day_url(child_id, t))
    entries = logic.day_entries(db, child_id, day)
    habit_entries = {e.habit_id: e for e in entries if e.kind == HABIT}
    rule_counts = Counter(e.rule_id for e in entries if e.kind == PENALTY and e.status != REJECTED)
    balance = logic.balance(db, child)
    return render(
        request, "day.html", user, db, child=child, day=day,
        prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1) if day < t else None,
        children=logic.children(db), entries=entries, habit_entries=habit_entries,
        rule_counts=rule_counts,
        habits=catalog_items(db, Habit, active_only=True),
        rules=catalog_items(db, Rule, active_only=True),
        rewards=sorted(catalog_items(db, Reward, active_only=True), key=lambda r: r.points),
        all_habits=catalog_items(db, Habit) if user.is_parent else [],
        all_rules=catalog_items(db, Rule) if user.is_parent else [],
        all_rewards=catalog_items(db, Reward) if user.is_parent else [],
        balance=balance, available=balance - logic.pending_reward_cost(db, child_id),
        day_net=logic.day_net(db, child_id, day),
        editable=user.is_parent or logic.kid_can_edit(day))


@app.post("/enfant/{child_id}/jour/{day}/habitude/{habit_id}", dependencies=CSRF)
def toggle_habit(request: Request, child_id: int, day: date, habit_id: int,
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    check_day(user, day)
    habit = db.get(Habit, habit_id)
    if habit is None:
        raise HTTPException(404, _("Habitude introuvable."))
    entry = db.scalar(select(Entry).where(Entry.child_id == child_id, Entry.day == day,
                                          Entry.habit_id == habit_id))
    if entry is None:
        db.add(new_entry(user, child, day, HABIT, habit.label, habit.points, habit_id=habit.id))
    elif user.is_parent:
        # Parent : valider ce qui est en attente/refusé, sinon décocher.
        if entry.status == APPROVED:
            db.delete(entry)
        else:
            approve(entry, user)
    elif entry.status == PENDING:
        db.delete(entry)
    elif entry.status == REJECTED:
        entry.status = PENDING
        entry.created_at = logic.now()
    else:
        flash(request, _("Déjà validé par un parent 👍"))
    db.commit()
    return redirect(day_url(child_id, day))


@app.post("/enfant/{child_id}/jour/{day}/penalite/{rule_id}", dependencies=CSRF)
def add_penalty(request: Request, child_id: int, day: date, rule_id: int,
                user: User = Depends(parent_user), db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    check_day(user, day)
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, _("Règle introuvable."))
    if rule.max_per_day:
        count = db.scalar(select(func.count(Entry.id)).where(
            Entry.child_id == child_id, Entry.day == day, Entry.rule_id == rule_id,
            Entry.status != REJECTED))
        if count >= rule.max_per_day:
            flash(request, _("Maximum {n} fois par jour pour « {label} ».").format(
                n=rule.max_per_day, label=rule.label), "error")
            return redirect(day_url(child_id, day))
    db.add(new_entry(user, child, day, PENALTY, rule.label, -rule.points, rule_id=rule.id))
    db.commit()
    return redirect(day_url(child_id, day))


@app.post("/enfant/{child_id}/jour/{day}/bonus", dependencies=CSRF)
def add_bonus(request: Request, child_id: int, day: date, points: int = Form(...),
              note: str = Form(""), user: User = Depends(parent_user), db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    check_day(user, day)
    if not 1 <= points <= 100:
        flash(request, _("Le bonus doit être entre 1 et 100 points."), "error")
        return redirect(day_url(child_id, day))
    note = note.strip()[:200]
    db.add(new_entry(user, child, day, BONUS, _("Bonne action"), points, note=note or None))
    db.commit()
    return redirect(day_url(child_id, day))


@app.post("/enfant/{child_id}/jour/{day}/recompense/{reward_id}", dependencies=CSRF)
def use_reward(request: Request, child_id: int, day: date, reward_id: int,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    check_day(user, day)
    reward = db.get(Reward, reward_id)
    if reward is None:
        raise HTTPException(404, _("Récompense introuvable."))
    if not user.is_parent:
        available = logic.balance(db, child) - logic.pending_reward_cost(db, child_id)
        if available < reward.points:
            flash(request, _("Pas encore assez de points pour cette récompense."), "error")
            return redirect(day_url(child_id, day))
    db.add(new_entry(user, child, day, REWARD, reward.label, -reward.points, reward_id=reward.id))
    db.commit()
    if user.is_parent:
        flash(request, _("« {label} » utilisée 🎉").format(label=reward.label))
    else:
        flash(request, _("Demande envoyée : « {label} ». Un parent doit valider.").format(label=reward.label))
    return redirect(day_url(child_id, day))


@app.post("/entree/{entry_id}/supprimer", dependencies=CSRF)
def delete_entry(request: Request, entry_id: int, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(404, _("Saisie introuvable."))
    if not user.is_parent and not (entry.status == PENDING and entry.created_by_id == user.id
                                   and logic.kid_can_edit(entry.day)):
        raise HTTPException(403, _("Seul un parent peut supprimer cette saisie."))
    child_id, day = entry.child_id, entry.day
    db.delete(entry)
    db.commit()
    return redirect(request.headers.get("referer") or day_url(child_id, day))


# --- Validation (parents) ----------------------------------------------------------------------

@app.get("/validation", response_class=HTMLResponse)
def validation_page(request: Request, user: User = Depends(parent_user), db: Session = Depends(get_db)):
    entries = db.scalars(select(Entry).where(Entry.status == PENDING)
                         .order_by(Entry.day, Entry.child_id, Entry.created_at)).all()
    return render(request, "validation.html", user, db, entries=entries)


@app.post("/entree/{entry_id}/valider", dependencies=CSRF)
def validate_entry(request: Request, entry_id: int, user: User = Depends(parent_user),
                   db: Session = Depends(get_db)):
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(404, _("Saisie introuvable."))
    approve(entry, user)
    db.commit()
    return redirect(request.headers.get("referer") or "/validation")


@app.post("/entree/{entry_id}/refuser", dependencies=CSRF)
def reject_entry(request: Request, entry_id: int, user: User = Depends(parent_user),
                 db: Session = Depends(get_db)):
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(404, _("Saisie introuvable."))
    entry.status = REJECTED
    entry.validated_by_id = user.id
    entry.validated_at = logic.now()
    db.commit()
    return redirect(request.headers.get("referer") or "/validation")


@app.post("/validation/tout", dependencies=CSRF)
def validate_all(request: Request, user: User = Depends(parent_user), db: Session = Depends(get_db)):
    entries = db.scalars(select(Entry).where(Entry.status == PENDING)).all()
    for entry in entries:
        approve(entry, user)
    db.commit()
    flash(request, _("{n} saisie(s) validée(s).").format(n=len(entries)))
    return redirect("/validation")


# --- Semaine & calendrier ----------------------------------------------------------------------

@app.get("/semaine", response_class=HTMLResponse)
def week_page(request: Request, d: date | None = None, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    start = logic.week_start(d or logic.today())
    summaries = [logic.week_summary(db, c, start) for c in logic.children(db)]
    next_start = start + timedelta(days=7)
    return render(request, "week.html", user, db, start=start, end=start + timedelta(days=6),
                  summaries=summaries, prev_week=start - timedelta(days=7),
                  next_week=next_start if next_start <= logic.today() else None)


@app.get("/calendrier", response_class=HTMLResponse)
def calendar_page(request: Request, enfant: int | None = None, m: str | None = None,
                  user: User = Depends(current_user), db: Session = Depends(get_db)):
    children = logic.children(db)
    if not children:
        return redirect("/")
    child = get_child(db, enfant) if enfant else children[0]
    t = logic.today()
    year, month = t.year, t.month
    if m and re.fullmatch(r"\d{4}-\d{2}", m):
        year, month = int(m[:4]), int(m[5:])
        if not 1 <= month <= 12:
            year, month = t.year, t.month
    weeks = logic.month_weeks(year, month)
    nets, pending = logic.month_activity(db, child.id, weeks[0][0], weeks[-1][-1])
    prev_m = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    next_m = f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"
    month_total = sum(v for k, v in nets.items() if k.month == month)
    return render(request, "calendar.html", user, db, child=child, children=children, weeks=weeks,
                  month=month, title=i18n.format_month(year, month), nets=nets, pending=pending,
                  prev_m=prev_m, next_m=next_m if (year, month) < (t.year, t.month) else None,
                  month_total=month_total, balance=logic.balance(db, child))


# --- Réglages (parents) ------------------------------------------------------------------------

CATALOG = {"habitude": Habit, "penalite": Rule, "recompense": Reward}
# Marqués avec _() pour l'extraction ; traduits à l'affichage par _(label) dans le template.
RETENTION_LABELS = {6: _("6 mois"), 12: _("1 an"), 24: _("2 ans"), 0: _("Illimité")}


@app.get("/reglages", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(parent_user), db: Session = Depends(get_db)):
    return render(request, "settings.html", user, db, children=logic.children(db, include_inactive=True),
                  habits=catalog_items(db, Habit), rules=catalog_items(db, Rule),
                  rewards=catalog_items(db, Reward), colors=CHILD_COLORS, languages=i18n.LANGUAGES,
                  retention=logic.retention_months(db), retention_choices=RETENTION_LABELS,
                  users=db.scalars(select(User).order_by(User.role.desc(), User.id)).all())


@app.post("/reglages/famille", dependencies=CSRF)
def settings_family(request: Request, family_name: str = Form(""), language: str = Form(""),
                    retention: str = Form(""), _user: User = Depends(parent_user),
                    db: Session = Depends(get_db)):
    name = family_name.strip()[:50]
    if not name:
        flash(request, _("Le nom de la famille ne peut pas être vide."), "error")
        return redirect("/reglages#famille")
    logic.set_setting(db, "family_name", name)
    if language in i18n.LANGUAGES:
        logic.set_setting(db, "language", language)
        i18n.set_language(language)
    if retention.isdigit() and int(retention) in logic.RETENTION_CHOICES:
        logic.set_setting(db, "retention_months", retention)
        logic.purge_old_entries(db)  # applique tout de suite une durée raccourcie
    db.commit()
    clear_language_cache()
    flash(request, _("Réglages de la famille enregistrés."))
    return redirect("/reglages#famille")


@app.post("/reglages/enfant/ajouter", dependencies=CSRF)
def settings_child_add(request: Request, name: str = Form(""), color: str = Form(""),
                       starting_balance: str = Form("0"), _user: User = Depends(parent_user),
                       db: Session = Depends(get_db)):
    name = name.strip()[:50]
    balance = _parse_int(starting_balance)
    if not name or balance is None:
        flash(request, _("Prénom obligatoire et points de départ en nombre entier."), "error")
        return redirect("/reglages#enfants")
    count = db.scalar(select(func.count(Child.id)))
    if count >= MAX_CHILDREN:
        flash(request, _("{n} enfants maximum.").format(n=MAX_CHILDREN), "error")
        return redirect("/reglages#enfants")
    if not re.fullmatch(COLOR_RE, color):
        color = CHILD_COLORS[count % len(CHILD_COLORS)]
    db.add(Child(name=name, color=color, starting_balance=balance, sort=count))
    db.commit()
    flash(request, _("{name} a été ajouté(e) 🎉").format(name=name))
    return redirect("/reglages#enfants")


@app.post("/reglages/enfant/{child_id}", dependencies=CSRF)
def settings_child(request: Request, child_id: int, name: str = Form(...), color: str = Form("#a78bfa"),
                   starting_balance: str = Form("0"), active: str | None = Form(None),
                   _user: User = Depends(parent_user), db: Session = Depends(get_db)):
    child = get_child(db, child_id)
    balance = _parse_int(starting_balance)
    if balance is None:
        flash(request, _("Points de départ invalides."), "error")
        return redirect("/reglages#enfants")
    if active is None and child.active and len(logic.children(db)) <= 1:
        flash(request, _("Il faut garder au moins un enfant actif."), "error")
        return redirect("/reglages#enfants")
    child.name = name.strip()[:50] or child.name
    if re.fullmatch(COLOR_RE, color):
        child.color = color
    child.starting_balance = balance
    child.active = active is not None
    db.commit()
    flash(request, _("{name} : modifications enregistrées.").format(name=child.name))
    return redirect("/reglages#enfants")


@app.post("/reglages/catalogue/{kind}/ajouter", dependencies=CSRF)
def settings_add(request: Request, kind: str, label: str = Form(...), points: str = Form("1"),
                 max_per_day: str = Form(""), next: str = Form(""), _user: User = Depends(parent_user),
                 db: Session = Depends(get_db)):
    model = CATALOG.get(kind)
    if model is None:
        raise HTTPException(404)
    n = _parse_points(points)
    if not label.strip() or n is None:
        flash(request, _("Libellé obligatoire et points entre 1 et 1000."), "error")
        return back_to(next, f"/reglages#{kind}")
    sort = (db.scalar(select(func.max(model.sort))) or 0) + 1
    item = model(label=label.strip()[:120], points=n, sort=sort, active=True)
    if model is Rule:
        item.max_per_day = _parse_points(max_per_day)
    db.add(item)
    db.commit()
    flash(request, _("« {label} » ajouté.").format(label=item.label))
    return back_to(next, f"/reglages#{kind}")


@app.post("/reglages/catalogue/{kind}/{item_id}", dependencies=CSRF)
def settings_update(request: Request, kind: str, item_id: int, label: str = Form(...),
                    points: str = Form("1"), max_per_day: str = Form(""), active: str | None = Form(None),
                    next: str = Form(""), _user: User = Depends(parent_user), db: Session = Depends(get_db)):
    model = CATALOG.get(kind)
    item = db.get(model, item_id) if model else None
    if item is None:
        raise HTTPException(404)
    n = _parse_points(points)
    if not label.strip() or n is None:
        flash(request, _("Libellé obligatoire et points entre 1 et 1000."), "error")
        return back_to(next, f"/reglages#{kind}")
    item.label = label.strip()[:120]
    item.points = n
    item.active = active is not None
    if model is Rule:
        item.max_per_day = _parse_points(max_per_day)
    db.commit()
    flash(request, _("« {label} » enregistré.").format(label=item.label))
    return back_to(next, f"/reglages#{kind}")


@app.post("/reglages/compte/ajouter", dependencies=CSRF)
def settings_account_add(request: Request, username: str = Form(""), display_name: str = Form(""),
                         password: str = Form(""), _user: User = Depends(parent_user),
                         db: Session = Depends(get_db)):
    errors: list[str] = []
    username, name, password = _parse_parent(username, display_name, password, errors)
    if not errors and db.scalar(select(User.id).where(User.username == username)):
        errors.append(_("L'identifiant « {name} » est déjà pris.").format(name=username))
    if errors:
        for e in errors:
            flash(request, e, "error")
        return redirect("/reglages#comptes")
    db.add(User(username=username, display_name=name, role=PARENT, password_hash=hash_password(password)))
    db.commit()
    flash(request, _("Compte parent « {name} » créé.").format(name=name))
    return redirect("/reglages#comptes")


@app.post("/reglages/compte/{user_id}/supprimer", dependencies=CSRF)
def settings_account_delete(request: Request, user_id: int, user: User = Depends(parent_user),
                            db: Session = Depends(get_db)):
    account = db.get(User, user_id)
    if account is None:
        raise HTTPException(404)
    if account.role != PARENT or account.id == user.id:
        raise HTTPException(400, _("Ce compte ne peut pas être supprimé."))
    db.delete(account)
    db.commit()
    flash(request, _("Compte « {name} » supprimé.").format(name=account.display_name))
    return redirect("/reglages#comptes")


@app.post("/reglages/compte/{user_id}", dependencies=CSRF)
def settings_account(request: Request, user_id: int, display_name: str = Form(""),
                     password: str = Form(""), _user: User = Depends(parent_user),
                     db: Session = Depends(get_db)):
    account = db.get(User, user_id)
    if account is None:
        raise HTTPException(404)
    if display_name.strip():
        account.display_name = display_name.strip()[:50]
    if password:
        if account.role == KIDS and not re.fullmatch(PIN_RE, password):
            flash(request, _("Le code PIN doit contenir 4 à 8 chiffres."), "error")
            return redirect("/reglages#comptes")
        if account.role == PARENT and len(password) < 8:
            flash(request, _("Le mot de passe doit faire au moins 8 caractères."), "error")
            return redirect("/reglages#comptes")
        account.password_hash = hash_password(password)
    db.commit()
    flash(request, _("Compte « {name} » mis à jour.").format(name=account.display_name))
    return redirect("/reglages#comptes")
