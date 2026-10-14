// Confirmation avant les actions sensibles (pénalités, récompenses, suppressions).
document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-confirm]");
  if (el && !window.confirm(el.dataset.confirm)) event.preventDefault();
});

// Conserver la position de défilement après un envoi de formulaire (rechargement de page).
const SCROLL_KEY = "cr-scroll:" + location.pathname;
document.addEventListener("submit", () => {
  try { sessionStorage.setItem(SCROLL_KEY, String(window.scrollY)); } catch (_) {}
});
window.addEventListener("DOMContentLoaded", () => {
  try {
    const y = sessionStorage.getItem(SCROLL_KEY);
    if (y !== null) { window.scrollTo(0, Number(y)); sessionStorage.removeItem(SCROLL_KEY); }
  } catch (_) {}

  // Changement de langue à l'installation : recharge la page dans la langue choisie
  document.querySelectorAll("[data-autosubmit]").forEach((el) => {
    el.addEventListener("change", () => el.form.submit());
  });

  // Ajout de lignes (parents, enfants) à partir d'un <template>
  document.querySelectorAll("[data-add-row]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.getElementById(btn.dataset.target);
      const row = document.getElementById(btn.dataset.addRow).content.cloneNode(true);
      const color = row.querySelector("[data-palette]");
      if (color) {
        const palette = color.dataset.palette.split(",");
        color.value = palette[target.children.length % palette.length];
      }
      target.appendChild(row);
      target.lastElementChild.querySelector("input")?.focus();
    });
  });

  // Onglets Enfants / Parents de la page de connexion
  document.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b === btn));
      document.querySelectorAll("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== btn.dataset.tab));
    });
  });
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
