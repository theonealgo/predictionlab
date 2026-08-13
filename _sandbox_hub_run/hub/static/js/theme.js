(function () {
  const key = "ss_theme";
  const root = document.documentElement;
  const saved = localStorage.getItem(key);
  if (saved) root.setAttribute("data-theme", saved);
  function toggle() {
    const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    if (next === "dark") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", "light");
    localStorage.setItem(key, next === "dark" ? "" : "light");
    const btn = document.getElementById("themeToggle");
    if (btn) btn.textContent = next === "light" ? "Dark mode" : "Light mode";
  }
  window.SSTheme = { toggle };
  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("themeToggle");
    if (!btn) return;
    const isLight = root.getAttribute("data-theme") === "light";
    btn.textContent = isLight ? "Dark mode" : "Light mode";
    btn.addEventListener("click", toggle);
  });
})();
