"use strict";

document.addEventListener("DOMContentLoaded", async () => {
    await loadIncludes(document);
    initializeNavigation();
    initializeCurrentYear();
    await initializeStatistics();
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
                    Unable to load ${escapeHTML(name)}.
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
        menuToggle.setAttribute("aria-expanded", "false");

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

        toggle.setAttribute("aria-expanded", "false");

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

    window.addEventListener("resize", () => {
        if (window.innerWidth > 860) {
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

async function initializeStatistics() {
    const elements = {
        species: findElement([
            "[data-stat='species']",
            "[data-stat='known-species']",
            "#known-species",
            "#species-count"
        ]),

        kingdoms: findElement([
            "[data-stat='kingdoms']",
            "#kingdoms-count",
            "#kingdom-count"
        ]),

        genera: findElement([
            "[data-stat='genera']",
            "#genera-count",
            "#genus-count"
        ]),

        families: findElement([
            "[data-stat='families']",
            "#families-count",
            "#family-count"
        ]),

        updated: findElement([
            "[data-stat='updated']",
            "[data-stat='last-updated']",
            "#last-updated"
        ])
    };

    if (!Object.values(elements).some(Boolean)) {
        return;
    }

    try {
        const response = await fetch(
            "/data/statistics.json",
            {
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    Accept: "application/json"
                }
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();

        setStatistic(
            elements.species,
            firstDefined(
                data.known_species,
                data.species,
                data.species_count,
                data.total_species
            )
        );

        setStatistic(
            elements.kingdoms,
            firstDefined(
                data.kingdoms,
                data.kingdom_count,
                data.total_kingdoms
            )
        );

        setStatistic(
            elements.genera,
            firstDefined(
                data.genera,
                data.genus_count,
                data.genera_count,
                data.total_genera
            )
        );

        setStatistic(
            elements.families,
            firstDefined(
                data.families,
                data.family_count,
                data.families_count,
                data.total_families
            )
        );

        setStatistic(
            elements.updated,
            formatDate(
                firstDefined(
                    data.last_updated,
                    data.updated,
                    data.updated_at,
                    data.generated_at
                )
            ),
            false
        );
    } catch (error) {
        console.error(
            "Unable to load Speciedex statistics:",
            error
        );

        Object.values(elements).forEach((element) => {
            if (
                element &&
                element.textContent.trim().toLowerCase()
                    .startsWith("loading")
            ) {
                element.textContent = "Unavailable";
            }
        });
    }
}

function findElement(selectors) {
    for (const selector of selectors) {
        const element = document.querySelector(selector);

        if (element) {
            return element;
        }
    }

    return null;
}

function firstDefined(...values) {
    return values.find(
        (value) =>
            value !== undefined &&
            value !== null &&
            value !== ""
    );
}

function setStatistic(element, value, formatNumber = true) {
    if (!element) {
        return;
    }

    if (
        value === undefined ||
        value === null ||
        value === ""
    ) {
        element.textContent = "Unavailable";
        return;
    }

    if (
        formatNumber &&
        !Number.isNaN(Number(value))
    ) {
        element.textContent = Number(value)
            .toLocaleString("en-US");

        return;
    }

    element.textContent = String(value);
}

function formatDate(value) {
    if (!value) {
        return "Unavailable";
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    return date.toLocaleDateString(
        "en-US",
        {
            year: "numeric",
            month: "long",
            day: "numeric"
        }
    );
}

function escapeHTML(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
