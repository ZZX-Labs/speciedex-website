
"use strict";

/*
==============================================================================
Speciedex.org
HTML Include Loader
==============================================================================

Loaded by:

    /static/js/script.js

Loads reusable HTML partials declared with:

    <div data-include="header"></div>
    <div data-include="splash"></div>
    <div data-include="nav"></div>
    <div data-include="footer"></div>

Each include resolves to:

    /_partials/{name}.html

Nested includes are supported.
==============================================================================
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (Speciedex.includesModuleLoaded) {
        return;
    }

    Speciedex.includesModuleLoaded = true;

    const INCLUDE_SELECTOR = "[data-include]";
    const INCLUDE_PATTERN = /^[a-z0-9_-]+$/i;
    const PARTIAL_ROOT = "/_partials/";

    /*
    --------------------------------------------------------------------------
    Load every include found within a document or element.
    --------------------------------------------------------------------------
    */

    async function loadIncludes(root = document) {
        if (
            !root ||
            typeof root.querySelectorAll !== "function"
        ) {
            return;
        }

        const includes = Array.from(
            root.querySelectorAll(INCLUDE_SELECTOR)
        );

        for (const element of includes) {
            await loadInclude(element);
        }
    }

    /*
    --------------------------------------------------------------------------
    Load one include element.
    --------------------------------------------------------------------------
    */

    async function loadInclude(element) {
        if (!(element instanceof Element)) {
            return;
        }

        const rawName =
            element.dataset.include || "";

        const name = sanitizeIncludeName(rawName);

        if (!name) {
            console.warn(
                "Speciedex rejected an invalid include name:",
                rawName
            );

            element.removeAttribute("data-include");
            return;
        }

        if (
            element.dataset.includeState ===
            "loading"
        ) {
            return;
        }

        element.dataset.includeState = "loading";
        element.setAttribute("aria-busy", "true");

        const url = getIncludeURL(name);

        try {
            const response = await fetch(
                url,
                {
                    method: "GET",
                    cache: "no-store",
                    credentials: "same-origin",
                    headers: {
                        Accept: "text/html"
                    }
                }
            );

            if (!response.ok) {
                throw new Error(
                    `HTTP ${response.status}: ${response.url}`
                );
            }

            const html = await response.text();

            element.innerHTML = html;

            element.removeAttribute(
                "data-include"
            );

            element.dataset.includeState =
                "loaded";

            element.removeAttribute(
                "aria-busy"
            );

            /*
            --------------------------------------------------------------
            Load any nested partials contained in the newly inserted HTML.
            --------------------------------------------------------------
            */

            await loadIncludes(element);

            element.dispatchEvent(
                new CustomEvent(
                    "speciedex:include-loaded",
                    {
                        bubbles: true,
                        detail: {
                            name,
                            url,
                            element
                        }
                    }
                )
            );
        } catch (error) {
            handleIncludeError(
                element,
                name,
                url,
                error
            );
        }
    }

    /*
    --------------------------------------------------------------------------
    Resolve a partial URL.
    --------------------------------------------------------------------------
    */

    function getIncludeURL(name) {
        return new URL(
            `${PARTIAL_ROOT}${name}.html`,
            window.location.origin
        ).href;
    }

    /*
    --------------------------------------------------------------------------
    Validate and normalize include names.
    --------------------------------------------------------------------------
    */

    function sanitizeIncludeName(value) {
        const name = String(value)
            .trim()
            .toLowerCase();

        return INCLUDE_PATTERN.test(name)
            ? name
            : "";
    }

    /*
    --------------------------------------------------------------------------
    Handle failed include requests.
    --------------------------------------------------------------------------
    */

    function handleIncludeError(
        element,
        name,
        url,
        error
    ) {
        console.error(
            `Unable to load include "${name}" from ${url}:`,
            error
        );

        element.innerHTML = `
            <div
                class="include-error"
                role="alert"
            >
                Unable to load ${escapeHTML(name)}.
            </div>
        `;

        element.removeAttribute(
            "data-include"
        );

        element.dataset.includeState =
            "error";

        element.removeAttribute(
            "aria-busy"
        );

        element.dispatchEvent(
            new CustomEvent(
                "speciedex:include-error",
                {
                    bubbles: true,
                    detail: {
                        name,
                        url,
                        element,
                        error
                    }
                }
            )
        );
    }

    /*
    --------------------------------------------------------------------------
    Escape text before inserting it into error markup.
    --------------------------------------------------------------------------
    */

    function escapeHTML(value) {
        return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    /*
    --------------------------------------------------------------------------
    Public module API.
    --------------------------------------------------------------------------
    */

    Speciedex.loadIncludes =
        loadIncludes;

    Speciedex.loadInclude =
        loadInclude;

    Speciedex.getIncludeURL =
        getIncludeURL;
})();
