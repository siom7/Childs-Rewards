import os
import re
import tempfile
from datetime import timedelta

import pytest

os.environ["CR_DATA_DIR"] = tempfile.mkdtemp(prefix="cr-test-")

from fastapi.testclient import TestClient  # noqa: E402

from app import cli, i18n, logic  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app, throttle  # noqa: E402
from app.models import Child, Entry, Habit, Reward, Rule, User  # noqa: E402
from app.security import verify_password  # noqa: E402
from tools.i18n_extract import source_keys  # noqa: E402


def csrf_of(client: TestClient, url: str = "/") -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get(url).text).group(1)


def post(client, url, data=None, csrf_page="/"):
    data = dict(data or {})
    data["csrf"] = csrf_of(client, csrf_page)
    return client.post(url, data=data, follow_redirects=False)


def login(client, username, password):
    return post(client, "/login", {"username": username, "password": password}, csrf_page="/login")


SETUP = {
    "language": "fr", "family_name": "Famille Test",
    "parent_name": ["Papa", "Maman", ""], "parent_username": ["papa", "maman", ""],
    "parent_password": ["motdepasse1", "motdepasse2", ""],
    "child_name": ["Léa", "Tom", ""], "child_color": ["#f472b6", "#60a5fa", "#34d399"],
    "child_balance": ["111", "86", "0"],
    "kids_pin": "1234", "examples": "on",
}


@pytest.fixture(scope="module")
def setup_done():
    with TestClient(app) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
        # Erreurs de saisie : la page est réaffichée sans rien créer
        bad = post(c, "/setup", {**SETUP, "kids_pin": "12", "family_name": ""}, csrf_page="/setup")
        assert bad.status_code == 200
        assert "Indiquez le nom de la famille." in bad.text and "4 à 8 chiffres" in bad.text
        with SessionLocal() as db:
            assert db.query(User).count() == 0
        r = post(c, "/setup", SETUP, csrf_page="/setup")
        assert r.status_code == 303
    with SessionLocal() as db:
        return [ch.id for ch in logic.children(db)]


@pytest.fixture
def parent(setup_done):
    with TestClient(app) as c:
        assert login(c, "papa", "motdepasse1").headers["location"] == "/"
        yield c


@pytest.fixture
def kids(setup_done):
    with TestClient(app) as c:
        assert login(c, "enfants", "1234").headers["location"] == "/"
        yield c


def first(model):
    with SessionLocal() as db:
        return db.query(model).order_by(model.sort).first()


def balance(child_id):
    with SessionLocal() as db:
        return logic.balance(db, db.get(Child, child_id))


def test_setup_creates_family(setup_done):
    lea, tom = setup_done
    assert balance(lea) == 111 and balance(tom) == 86
    with SessionLocal() as db:
        assert logic.get_setting(db, "family_name") == "Famille Test"
        assert db.query(User).filter_by(role="parent").count() == 2
        assert db.query(Habit).count() == 8
        assert db.query(Rule).count() == 5
        assert db.query(Reward).count() == 8


def test_setup_closed_after_install(setup_done):
    with TestClient(app) as c:
        assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_wrong_pin_rejected(setup_done):
    with TestClient(app) as c:
        login(c, "enfants", "0000")
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
    throttle.reset("testclient")


def test_post_without_csrf_rejected(parent, setup_done):
    habit = first(Habit)
    r = parent.post(f"/enfant/{setup_done[0]}/jour/{logic.today()}/habitude/{habit.id}", data={})
    assert r.status_code == 400


def test_kid_habit_pending_then_parent_validates(kids, parent, setup_done):
    lea = setup_done[0]
    habit = first(Habit)
    url = f"/enfant/{lea}/jour/{logic.today()}/habitude/{habit.id}"
    start = balance(lea)

    post(kids, url)
    assert balance(lea) == start  # en attente : pas encore compté
    with SessionLocal() as db:
        entry = db.query(Entry).filter_by(child_id=lea, habit_id=habit.id).one()
        assert entry.status == "pending"

    post(parent, f"/entree/{entry.id}/valider")
    assert balance(lea) == start + 1

    # Un enfant ne peut plus décocher une habitude validée
    post(kids, url)
    assert balance(lea) == start + 1


def test_kids_cannot_penalize_or_bonus(kids, setup_done):
    rule = first(Rule)
    day = logic.today()
    assert post(kids, f"/enfant/{setup_done[1]}/jour/{day}/penalite/{rule.id}").status_code == 403
    assert post(kids, f"/enfant/{setup_done[1]}/jour/{day}/bonus", {"points": "5"}).status_code == 403
    assert kids.get("/validation").status_code == 403
    assert kids.get("/reglages").status_code == 403
    assert post(kids, "/reglages/catalogue/penalite/ajouter", {"label": "x", "points": "1"}).status_code == 403


def test_kids_only_edit_today(kids, parent, setup_done):
    tom = setup_done[1]
    habit, reward = first(Habit), first(Reward)
    yesterday = logic.today() - timedelta(days=1)
    # Consultation possible, mais en lecture seule
    page = kids.get(f"/enfant/{tom}/jour/{yesterday}")
    assert page.status_code == 200 and "seul un parent peut la modifier" in page.text
    assert post(kids, f"/enfant/{tom}/jour/{yesterday}/habitude/{habit.id}").status_code == 403
    assert post(kids, f"/enfant/{tom}/jour/{yesterday}/recompense/{reward.id}").status_code == 403
    # Une demande en attente d'hier ne peut plus être supprimée par l'enfant
    with SessionLocal() as db:
        kid_user = db.query(User).filter_by(username="enfants").one()
        entry = Entry(child_id=tom, day=yesterday, kind="habit", habit_id=habit.id, label=habit.label,
                      points=1, status="pending", created_by_id=kid_user.id, created_at=logic.now())
        db.add(entry)
        db.commit()
        entry_id = entry.id
    assert post(kids, f"/entree/{entry_id}/supprimer").status_code == 403
    # Un parent peut toujours modifier les jours passés
    assert post(parent, f"/entree/{entry_id}/valider").status_code == 303
    assert post(parent, f"/enfant/{tom}/jour/{yesterday}/habitude/{habit.id}").status_code == 303


def test_penalty_max_per_day(parent, setup_done):
    tom = setup_done[1]
    with SessionLocal() as db:
        rule = db.query(Rule).filter(Rule.max_per_day.isnot(None)).first()
    start = balance(tom)
    for _ in range(rule.max_per_day + 1):
        post(parent, f"/enfant/{tom}/jour/{logic.today()}/penalite/{rule.id}")
    assert balance(tom) == start - rule.points * rule.max_per_day


def test_balance_can_go_negative_with_parent_reward(parent, setup_done):
    tom = setup_done[1]
    with SessionLocal() as db:
        reward = db.query(Reward).order_by(Reward.points.desc()).first()
    for _ in range(3):
        post(parent, f"/enfant/{tom}/jour/{logic.today()}/recompense/{reward.id}")
    assert balance(tom) < 0


def test_kid_reward_request_needs_enough_points(kids, setup_done):
    tom = setup_done[1]  # solde négatif (test précédent)
    reward = first(Reward)
    post(kids, f"/enfant/{tom}/jour/{logic.today()}/recompense/{reward.id}")
    with SessionLocal() as db:
        assert db.query(Entry).filter_by(child_id=tom, kind="reward", status="pending").count() == 0


def test_parent_adds_rule_from_day_page(parent, setup_done):
    day_page = f"/enfant/{setup_done[0]}/jour/{logic.today()}"
    r = post(parent, "/reglages/catalogue/penalite/ajouter",
             {"label": "Crier à table", "points": "2", "max_per_day": "", "next": day_page})
    assert r.headers["location"] == day_page
    assert "Crier à table" in parent.get(day_page).text
    # Une redirection vers un autre site est refusée
    r = post(parent, "/reglages/catalogue/recompense/ajouter",
             {"label": "Glace", "points": "12", "next": "//evil.example"})
    assert r.headers["location"] == "/reglages#recompense"


def test_parent_manages_family(parent, setup_done):
    post(parent, "/reglages/enfant/ajouter", {"name": "Léo", "color": "#34d399", "starting_balance": "5"})
    with SessionLocal() as db:
        leo = db.query(Child).filter_by(name="Léo").one()
    assert balance(leo.id) == 5
    # Retirer l'enfant le masque sans supprimer son historique
    post(parent, f"/reglages/enfant/{leo.id}", {"name": "Léo", "color": "#34d399", "starting_balance": "5"})
    assert f'href="/enfant/{leo.id}"' not in parent.get("/").text
    post(parent, "/reglages/compte/ajouter", {"username": "mamie", "display_name": "Mamie", "password": "grandmere1"})
    with TestClient(app) as c:
        assert login(c, "mamie", "grandmere1").headers["location"] == "/"


def test_week_and_calendar_pages(parent, kids):
    for client in (parent, kids):
        assert client.get("/semaine").status_code == 200
        assert client.get("/calendrier").status_code == 200
    assert "Points conservés" in parent.get("/semaine").text


def test_language_switch(parent):
    post(parent, "/reglages/famille", {"family_name": "Famille Test", "language": "en"})
    try:
        page = parent.get("/semaine").text
        assert "Points carried over" in page and 'lang="en"' in page
    finally:
        post(parent, "/reglages/famille", {"family_name": "Famille Test", "language": "fr"})
    assert "Points conservés" in parent.get("/semaine").text


def test_translation_catalogs_complete():
    keys = source_keys()
    for code in i18n.LANGUAGES:
        if code != i18n.DEFAULT_LANGUAGE:
            missing = keys - i18n._CATALOGS[code].keys()
            assert not missing, f"{code}: {sorted(missing)[:5]}"
            # Les variables {x} doivent être conservées dans chaque traduction
            for k in keys:
                assert set(re.findall(r"{\w+}", k)) == set(re.findall(r"{\w+}", i18n._CATALOGS[code][k])), (code, k)


def test_cli_reset_password_and_pin(setup_done, monkeypatch):
    answers = iter(["nouveaumdp1", "nouveaumdp1", "9876", "9876"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt="": next(answers))
    assert cli.main(["reset-password", "maman"]) == 0
    assert cli.main(["reset-pin"]) == 0
    with SessionLocal() as db:
        assert verify_password("nouveaumdp1", db.query(User).filter_by(username="maman").one().password_hash)
        assert verify_password("9876", db.query(User).filter_by(username="enfants").one().password_hash)


def test_week_summary_matches_paper_formula(setup_done):
    """Total = total de la veille + gagnés − retirés + bonus − récompenses."""
    with SessionLocal() as db:
        child = db.get(Child, setup_done[0])
        s = logic.week_summary(db, child, logic.week_start(logic.today()))
    running = s["opening"]
    for r in s["rows"]:
        running += r["earned"] - r["removed"] + r["bonus"] - r["used"]
        if r["total"] is not None:
            assert r["total"] == running


def test_session_cookie_secure_only_over_https(setup_done):
    """CR_COOKIE_SECURE=auto : la connexion marche en http:// (installation sur IP:8000) et le cookie
    devient « Secure » dès que la page est servie en HTTPS (reverse proxy)."""
    with TestClient(app) as c:
        cookie = c.get("/login").headers["set-cookie"]
        assert cookie.startswith("cr_session=") and "secure" not in cookie.lower()
    with TestClient(app, base_url="https://testserver") as c:
        assert "secure" in c.get("/login").headers["set-cookie"].lower()
        assert login(c, "papa", "motdepasse1").headers["location"] == "/"


def test_missing_session_cookie_has_clear_message(setup_done):
    with TestClient(app) as c:
        r = c.post("/login", data={"username": "papa", "password": "x", "csrf": "abc"})
        assert r.status_code == 400 and "cookie de session" in r.text
