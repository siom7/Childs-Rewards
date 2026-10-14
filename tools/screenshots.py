"""Génère les captures d'écran de la documentation (docs/screenshots/<langue>/*.png).

Lance un serveur temporaire avec une famille fictive, remplit quelques semaines d'historique,
puis photographie les pages avec Chrome (via Playwright) au format téléphone.

Usage : python tools/screenshots.py [fr en ...]
Prérequis : pip install playwright  (utilise le Chrome installé, aucun navigateur téléchargé)
"""
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

DEMO = {
    "fr": {"family": "Famille Dupont", "parents": [("Papa", "papa"), ("Maman", "maman")]},
    "en": {"family": "The Smiths", "parents": [("Dad", "dad"), ("Mum", "mum")]},
}
KIDS = [("Léa", "#f472b6", 42), ("Tom", "#60a5fa", 35)]
PASSWORD = "demo-password"  # famille fictive, serveur temporaire
PIN = "2468"


def fill_history(data_dir: str, lang: str) -> None:
    """Ajoute ~3 semaines d'historique validé (exécuté dans un sous-processus avec CR_DATA_DIR)."""
    os.environ["CR_DATA_DIR"] = data_dir
    sys.path.insert(0, str(ROOT))
    from app import i18n, logic
    from app.db import SessionLocal
    from app.models import APPROVED, BONUS, HABIT, PENALTY, REWARD, Entry, Habit, Reward, Rule, User

    i18n.set_language(lang)
    rnd = random.Random(lang)
    with SessionLocal() as db:
        parent = db.query(User).filter_by(role="parent").first()
        habits, rules = db.query(Habit).all(), db.query(Rule).all()
        rewards = db.query(Reward).order_by(Reward.points).all()
        today = logic.today()
        for child in logic.children(db):
            for back in range(21, 0, -1):
                day = today - timedelta(days=back)
                now = logic.now() - timedelta(days=back)

                def add(kind, label, points, **kw):
                    db.add(Entry(child_id=child.id, day=day, kind=kind, label=label, points=points,
                                 status=APPROVED, created_by_id=parent.id, created_at=now,
                                 validated_by_id=parent.id, validated_at=now, **kw))

                for h in rnd.sample(habits, rnd.randint(4, len(habits))):
                    add(HABIT, h.label, h.points, habit_id=h.id)
                if rnd.random() < 0.35:
                    r = rnd.choice(rules)
                    add(PENALTY, r.label, -r.points, rule_id=r.id)
                if rnd.random() < 0.15:
                    add(BONUS, i18n._("Bonne action"), rnd.randint(1, 3))
                if rnd.random() < 0.2:
                    r = rnd.choice(rewards[:4])
                    add(REWARD, r.label, -r.points, reward_id=r.id)
        db.commit()


def capture(lang: str) -> None:
    from playwright.sync_api import sync_playwright

    demo = DEMO.get(lang, DEMO["en"])
    out = OUT / lang
    out.mkdir(parents=True, exist_ok=True)
    data_dir = tempfile.mkdtemp(prefix=f"cr-shots-{lang}-")
    env = {**os.environ, "CR_DATA_DIR": data_dir}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"{BASE}/healthz")
                break
            except OSError:
                time.sleep(0.2)

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome")
            device = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2,
                      "is_mobile": True, "has_touch": True, "color_scheme": "dark", "locale": lang}

            def shot(page, name, height=844):
                # Écran plus haut plutôt que full_page : la barre du bas et le fond restent à leur place
                page.set_viewport_size({"width": 390, "height": height})
                page.wait_for_timeout(300)
                page.screenshot(path=str(out / f"{name}.png"))
                page.set_viewport_size({"width": 390, "height": 844})

            # 1. Installation
            parent_ctx = browser.new_context(**device)
            page = parent_ctx.new_page()
            page.on("dialog", lambda d: d.accept())
            page.goto(f"{BASE}/setup?lang={lang}")
            page.fill("input[name=family_name]", demo["family"])
            for i, (name, user) in enumerate(demo["parents"]):
                page.locator("input[name=parent_name]").nth(i).fill(name)
                page.locator("input[name=parent_username]").nth(i).fill(user)
                page.locator("input[name=parent_password]").nth(i).fill(PASSWORD)
            for i, (name, _color, balance) in enumerate(KIDS):
                page.locator("input[name=child_name]").nth(i).fill(name)
                page.locator("input[name=child_balance]").nth(i).fill(str(balance))
            page.fill("input[name=kids_pin]", PIN)
            page.evaluate("window.scrollTo(0, 0)")
            shot(page, "01-setup")
            page.click("form[action='/setup'] button.btn-primary")
            page.wait_for_url(f"{BASE}/")

            subprocess.run([sys.executable, __file__, "--fill", data_dir, lang], check=True, cwd=ROOT)

            # 2. Côté enfant : cocher des habitudes et demander une récompense
            kid_ctx = browser.new_context(**device)
            kid = kid_ctx.new_page()
            kid.on("dialog", lambda d: d.accept())
            kid.goto(f"{BASE}/login")
            shot(kid, "02-login")
            kid.fill("#pin", PIN)
            kid.click("form[data-pane=kids] button")
            kid.wait_for_url(f"{BASE}/")
            shot(kid, "03-kids-home")
            kid.click("a.kid-card >> nth=1")
            kid.wait_for_load_state()
            for i in (0, 1, 3):
                kid.locator("button.habit").nth(i).click()
                kid.wait_for_load_state()
            kid.locator("button.tile.rew").first.click()
            kid.wait_for_load_state()
            kid.evaluate("window.scrollTo(0, 0)")
            shot(kid, "04-kid-day")

            # 3. Côté parent
            page.goto(f"{BASE}/")
            shot(page, "05-parent-home")
            page.click("a.kid-card >> nth=0")
            page.wait_for_load_state()
            for i in (0, 2, 4, 5):
                page.locator("button.habit").nth(i).click()
                page.wait_for_load_state()
            page.locator("button.tile.pen").nth(2).click()
            page.wait_for_load_state()
            page.evaluate("window.scrollTo(0, 0)")
            shot(page, "06-parent-day", height=1500)
            page.locator("details.edit-list").nth(1).evaluate("el => el.open = true")
            page.locator("details.edit-list").nth(1).scroll_into_view_if_needed()
            shot(page, "07-edit-rules")
            page.goto(f"{BASE}/validation")
            shot(page, "08-validation")
            page.goto(f"{BASE}/semaine")
            shot(page, "09-week", height=1450)
            page.goto(f"{BASE}/calendrier")
            shot(page, "10-calendar")
            page.goto(f"{BASE}/reglages")
            shot(page, "11-settings")
            browser.close()
    finally:
        server.terminate()
        server.wait()
    print(f"{lang}: captures dans {out}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fill":
        fill_history(sys.argv[2], sys.argv[3])
    else:
        for code in sys.argv[1:] or ["fr", "en"]:
            capture(code)
