"use strict";

document.addEventListener("DOMContentLoaded", async () => {
    await loadIncludes(document);
    initializeNavigation();
    initializeCurrentYear();
});

async function loadIncludes(root) {
    const includes = Array.from(
        root.querySelectorAll("[data-include]")
    );

    for (const element of includes) {
        const name = element.dataset.include;

        if (!/^[a-z0-9_-]+$/i.test(name)) {
            element.removeAttribute("data-include");
            continue;
        }

        try {
            const response = await fetch(
                `/_partials/${name}.html`,
                {
                    cache: "no-store",
                    credentials: "same-origin"
                }
            );

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            element.innerHTML = await response.text();
            element.removeAttribute("data-include");

            await loadIncludes(element);
        } catch (error) {
            console.error(
                `Unable to load ${name}:`,
                error
            );

            element.innerHTML = `
                <div class="include-error" role="alert">
                    Unable to load ${name}.
                </div>
            `;

            element.removeAttribute("data-include");
        }
    }
}

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
        menuToggle.addEventListener("click", () => {
            const open = menu.classList.toggle("open");

            menuToggle.classList.toggle("open", open);
            menuToggle.setAttribute(
                "aria-expanded",
                String(open)
            );

            document.body.classList.toggle(
                "menu-open",
                open
            );
        });
    }

    dropdownToggles.forEach((toggle) => {
        const dropdown = toggle.closest(".dropdown");

        if (!dropdown) {
            return;
        }

        toggle.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();

            const open =
                !dropdown.classList.contains("open");

            closeDropdowns(nav, dropdown);

            dropdown.classList.toggle("open", open);
            toggle.setAttribute(
                "aria-expanded",
                String(open)
            );
        });
    });

    document.addEventListener("click", (event) => {
        if (!nav.contains(event.target)) {
            closeDropdowns(nav);
            closeMenu(menu, menuToggle);
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeDropdowns(nav);
            closeMenu(menu, menuToggle);
        }
    });

    highlightCurrentPage(nav);
}

function closeDropdowns(nav, current = null) {
    nav.querySelectorAll(".dropdown.open").forEach(
        (dropdown) => {
            if (dropdown === current) {
                return;
            }

            dropdown.classList.remove("open");

            const toggle = dropdown.querySelector(
                ":scope > .dropdown-toggle, " +
                ":scope > [data-dropdown-toggle]"
            );

            toggle?.setAttribute(
                "aria-expanded",
                "false"
            );
        }
    );
}

function closeMenu(menu, toggle) {
    if (!menu || !toggle) {
        return;
    }

    menu.classList.remove("open");
    toggle.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");

    document.body.classList.remove("menu-open");
}

function highlightCurrentPage(nav) {
    const current = normalizePath(
        window.location.pathname
    );

    nav.querySelectorAll("a[href]").forEach((link) => {
        const url = new URL(
            link.href,
            window.location.href
        );

        if (url.origin !== window.location.origin) {
            return;
        }

        if (normalizePath(url.pathname) !== current) {
            return;
        }

        link.classList.add("active");
        link.setAttribute("aria-current", "page");

        link.closest(".dropdown")
            ?.classList.add("active-branch");
    });
}

function normalizePath(path) {
    path = path.replace(/\/index\.html$/i, "/");

    return path.endsWith("/")
        ? path
        : `${path}/`;
}

function initializeCurrentYear() {
    const year = new Date().getFullYear();

    document.querySelectorAll(
        "[data-current-year], #current-year"
    ).forEach((element) => {
        element.textContent = year;
    });
}
