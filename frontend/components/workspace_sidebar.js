const MOBILE = "(max-width: 760px)";
const THEME_KEY = "era5.appearance.v1";

function icon(value) {
  const node = document.createElement("span");
  node.className = "era5-icon";
  node.setAttribute("aria-hidden", "true");
  node.textContent = value;
  return node;
}

function button(label, glyph, className, onClick, iconOnly = false) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = className;
  node.setAttribute("aria-label", label);
  node.title = label;
  node.append(icon(glyph));
  if (!iconOnly) {
    const text = document.createElement("span");
    text.className = "era5-button-label";
    text.textContent = label;
    node.append(text);
  }
  node.addEventListener("click", onClick);
  return node;
}

// Use Streamlit's active theme so charts, grids and popovers share the palette.
function applyTheme(mode) {
  const menu = document.querySelector('[data-testid="stMainMenuButton"]');
  if (!menu) return;
  const wasOpen = menu.getAttribute("aria-expanded") === "true";
  if (!wasOpen) menu.click();
  const label = mode === "dark" ? "Dark" : "Light";
  const select = (attempt = 0) => {
    const desired = document.querySelector(`[data-testid="stMainMenuItem-theme-${label}"]`);
    if (!desired && attempt < 10) {
      requestAnimationFrame(() => select(attempt + 1));
      return;
    }
    if (desired && desired.getAttribute("aria-checked") !== "true") desired.click();
    if (!wasOpen && menu.getAttribute("aria-expanded") === "true") menu.click();
  };
  requestAnimationFrame(() => select());
}

export default function({data, parentElement, setTriggerValue}) {
  const root = parentElement.querySelector(".era5-shell");
  if (!root) return;
  if (!root.workspace) {
    let theme = "light";
    try { if (localStorage.getItem(THEME_KEY) === "dark") theme = "dark"; } catch (_) {}
    root.workspace = {theme, collapsed: false, drawer: false};
    applyTheme(theme);
  }
  const state = root.workspace;
  function render() {
    root.dataset.collapsed = String(state.collapsed);
    root.dataset.drawer = String(state.drawer);
    root.dataset.theme = state.theme;
    const panel = document.createElement("aside");
    panel.className = "era5-workspace-sidebar";
    panel.setAttribute("aria-label", "Điều hướng khí tượng");
    panel.id = "era5-sidebar-panel";
    const header = document.createElement("header");
    header.className = "era5-sidebar-header";
    const brand = document.createElement("strong");
    brand.className = "weahub-wordmark";
    brand.append("Weather ");
    const hub = document.createElement("span");
    hub.textContent = "Hub";
    brand.append(hub);
    const mobile = matchMedia(MOBILE).matches;
    const collapse = button(mobile ? "Đóng menu điều hướng" : state.collapsed ? "Mở rộng sidebar" : "Thu gọn sidebar",
      mobile ? "close" : state.collapsed ? "keyboard_double_arrow_right" : "keyboard_double_arrow_left", "era5-sidebar-collapse",
      () => {
        if (matchMedia(MOBILE).matches) state.drawer = false;
        else state.collapsed = !state.collapsed;
        render();
        (matchMedia(MOBILE).matches ? root.querySelector(".era5-mobile-menu") : root.querySelector(".era5-sidebar-collapse"))?.focus();
      }, true);
    collapse.setAttribute("aria-expanded", String(mobile ? state.drawer : !state.collapsed));
    collapse.setAttribute("aria-controls", panel.id);
    header.append(brand, collapse);
    const nav = document.createElement("nav");
    nav.className = "era5-sidebar-nav";
    nav.setAttribute("aria-label", "Điều hướng");
    const navLabel = document.createElement("span");
    navLabel.className = "era5-sidebar-nav-label";
    navLabel.setAttribute("aria-hidden", "true");
    navLabel.textContent = "ĐIỀU HƯỚNG";
    nav.append(navLabel);
    for (const item of data.navigation) {
      const row = button(item.label, item.icon, "era5-sidebar-row", () => {
        state.drawer = false;
        render();
        if (item.id !== data.active) {
          if (data.workspaceNavigation) {
            if (!data.guest) {
              document.documentElement.dataset.weahubDestination = String(data.navigation.indexOf(item));
            }
            document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0);
          }
          if (data.dismissAdminNoticeOnNavigate) {
            document.querySelector(".admin-notice button")?.click();
          }
          setTriggerValue("navigate", item.id);
        }
      });
      if (item.id === data.active) row.setAttribute("aria-current", "page");
      nav.append(row);
    }
    const bottom = document.createElement("div");
    bottom.className = "era5-sidebar-bottom";
    const footer = document.createElement("footer");
    footer.className = "era5-sidebar-footer";
    const theme = button(state.theme === "light" ? "Bật chế độ tối" : "Bật chế độ sáng",
      state.theme === "light" ? "dark_mode" : "light_mode", "era5-sidebar-theme", () => {
        state.theme = state.theme === "light" ? "dark" : "light";
        try { localStorage.setItem(THEME_KEY, state.theme); } catch (_) {}
        applyTheme(state.theme);
        render();
        root.querySelector(".era5-sidebar-theme")?.focus();
      }, true);
    theme.setAttribute("aria-pressed", String(state.theme === "dark"));
    if (data.guest) {
      footer.append(button("Đăng nhập", "login", "era5-sidebar-login", () => {
        state.drawer = false;
        render();
        document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0);
        setTriggerValue("login", true);
      }), theme);
    } else {
      const profile = document.createElement("div");
      profile.className = "era5-sidebar-profile";
      const avatar = document.createElement("span");
      avatar.className = "era5-sidebar-avatar";
      avatar.textContent = data.username ? data.username.slice(0, 1).toUpperCase() : "·";
      const name = document.createElement("strong");
      name.className = "era5-button-label";
      name.textContent = data.username;
      profile.append(avatar, name);
      footer.append(profile, theme,
        button("Đăng xuất", "logout", "era5-sidebar-logout", () => setTriggerValue("logout", true), true));
    }
    bottom.append(footer);
    panel.append(header, nav, bottom);
    const open = button("Mở menu điều hướng", "menu", "era5-mobile-menu", () => {
      state.drawer = !state.drawer;
      render();
      const target = root.querySelector(state.drawer ? ".era5-sidebar-collapse" : ".era5-mobile-menu");
      target?.focus();
    }, true);
    open.setAttribute("aria-expanded", String(state.drawer));
    open.setAttribute("aria-controls", panel.id);
    const backdrop = document.createElement("div");
    backdrop.className = "era5-sidebar-backdrop";
    backdrop.onclick = () => { state.drawer = false; render(); root.querySelector(".era5-mobile-menu")?.focus(); };
    root.replaceChildren(open, backdrop, panel);
    root.onkeydown = event => {
      if (event.key === "Escape" && state.drawer) {
        state.drawer = false;
        render();
        root.querySelector(".era5-mobile-menu")?.focus();
      }
    };
  }
  render();
}
