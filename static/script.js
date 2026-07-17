"use strict";

/*
========================================================================
Speciedex.org
Site JavaScript
========================================================================
*/

const SCRIPT_URL = document.currentScript?.src || "/static/script.js";
const SITE_ROOT = new URL("../", SCRIPT_URL);

document.addEventListener("DOMContentLoaded", async () => {
    await loadIncludes();
    initializeNavigation();
    initializeCurrentYear();
    secureExternalLinks();
});

/*
========================================================================
HTML Partials
========================================================================
*/

async function loadIncludes(root = document) {
    const includes = Array.from(
        root.querySelectorAll("[data-include]")
    );

    for (const element of includes) {
        const name = sanitizeIncludeName(
            element.getAttribute("data-include")
        );

        if (!name) {
            element.removeAttribute("data-include");
            continue;
        }

        const url = new URL(
            `_partials/${name}.html`,
            SITE_ROOT
        );

        try {
            const response = await fetch(url.href, {
                method: "GET",
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    Accept: "text/html"
                }
            });

            if (!response.ok) {
                throw new Error(
                    `HTTP ${response.status} ${response.statusText}`
                );
            }

            element.innerHTML = await response.text();
            element.removeAttribute("data-include");

            await loadIncludes(element);
        } catch (error) {
            console.error(
                `Unable to load ${name} from ${url.href}`,
                error
            );

            element.innerHTML = `
                <div class="include-error" role="alert">
                    Unable to load ${escapeHTML(name)}.
                </div>
            `;

            element.removeAttribute("data-include");
        }
    }
}

function sanitizeIncludeName(value) {
    const name = String(value || "")
        .trim()
        .toLowerCase();

    return /^[a-z0-9_-]+$/.test(name)
        ? name
        : "";
}

function escapeHTML(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

/*
========================================================================
Navigation
========================================================================
*/

function initializeNavigation() {
    const nav = document.querySelector(".site-nav");

    if (!nav) {
        return;
    }

    const menuToggle = nav.querySelector(
        "[data-nav-toggle], .menu-toggle"
    );

    const menu = nav.querySelector(
        "[data-nav-menu], .nav-menu"
    );

    const dropdownToggles = nav.querySelectorAll(
        "[data-dropdown-toggle], .dropdown-toggle"
    );

    if (menuToggle && menu) {
        menuToggle.setAttribute("aria-expanded", "false");

        menuToggle.addEventListener("click", () => {
            const isOpen = menu.classList.toggle("open");

            menuToggle.classList.toggle("open", isOpen);
            menuToggle.setAttribute(
                "aria-expanded",
                String(isOpen)
            );

            document.body.classList.toggle(
                "menu-open",
                isOpen
            );
        });
    }

    dropdownToggles.forEach((toggle) => {
        const dropdown = toggle.closest(".dropdown");

        if (!dropdown) {
            return;
        }

        toggle.setAttribute("aria-expanded", "false");

        toggle.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();

            const isOpen =
                !dropdown.classList.contains("open");

            closeDropdowns(nav, dropdown);

            dropdown.classList.toggle("open", isOpen);
            toggle.setAttribute(
                "aria-expanded",
                String(isOpen)
            );
        });
    });

    document.addEventListener("click", (event) => {
        if (nav.contains(event.target)) {
            return;
        }

        closeDropdowns(nav);
        closeMenu(menu, menuToggle);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") {
            return;
        }

        closeDropdowns(nav);
        closeMenu(menu, menuToggle);
        menuToggle?.focus();
    });

    window.addEventListener("resize", () => {
        if (window.innerWidth <= 860) {
            return;
        }

        closeDropdowns(nav);
        closeMenu(menu, menuToggle);
    });

    highlightCurrentPage(nav);
}

function closeDropdowns(nav, exception = null) {
    nav.querySelectorAll(".dropdown.open").forEach(
        (dropdown) => {
            if (dropdown === exception) {
                return;
            }

            dropdown.classList.remove("open");

            const toggle = dropdown.querySelector(
                ":scope > [data-dropdown-toggle], " +
                ":scope > .dropdown-toggle"
            );

            toggle?.setAttribute(
                "aria-expanded",
                "false"
            );
        }
    );
}

function closeMenu(menu, menuToggle) {
    if (!menu || !menuToggle) {
        return;
    }

    menu.classList.remove("open");
    menuToggle.classList.remove("open");
    menuToggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("menu-open");
}

function highlightCurrentPage(nav) {
    const currentPath = normalizePath(
        window.location.pathname
    );

    nav.querySelectorAll("a[href]").forEach((link) => {
        const url = new URL(
            link.getAttribute("href"),
            window.location.href
        );

        if (url.origin !== window.location.origin) {
            return;
        }

        if (normalizePath(url.pathname) !== currentPath) {
            return;
        }

        link.classList.add("active");
        link.setAttribute("aria-current", "page");

        link.closest(".dropdown")?.classList.add(
            "active-branch"
        );
    });
}

function normalizePath(pathname) {
    let path = pathname || "/";

    path = path.replace(/\/index\.html$/i, "/");

    if (!path.endsWith("/")) {
        path += "/";
    }

    return path;
}

/*
========================================================================
Footer and External Links
========================================================================
*/

function initializeCurrentYear() {
    const year = String(
        new Date().getFullYear()
    );

    document.querySelectorAll(
        "[data-current-year], #current-year"
    ).forEach((element) => {
        element.textContent = year;
    });
}

function secureExternalLinks() {
    document.querySelectorAll(
        'a[target="_blank"]'
    ).forEach((link) => {
        link.setAttribute(
            "rel",
            "noopener noreferrer"
        );
    });
}
