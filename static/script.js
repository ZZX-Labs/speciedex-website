"use strict";

/*
==============================================================================
Speciedex.org
Public JavaScript Entry Point
==============================================================================

This is the only JavaScript file loaded directly by site pages.

Responsibilities:

    • Resolve the /static/ asset root
    • Load the internal site bootstrap
    • Expose minimal public loader helpers
    • Dispatch loader errors

The internal bootstrap:

    /static/js/script.js

is responsible for loading and initializing all remaining JavaScript modules.

Dependency flow:

    HTML
        |
        v
    /static/script.js
        |
        v
    /static/js/script.js
        |
        +--> includes.js
        +--> data.js
        +--> header.js
        +--> splash.js
        +--> nav.js
        +--> footer.js
        +--> statistics.js
        +--> future modules

==============================================================================
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (Speciedex.publicEntryPointLoaded) {
        return;
    }

    Speciedex.publicEntryPointLoaded = true;

    /*
    ==========================================================================
    Configuration
    ==========================================================================
    */

    const BOOTSTRAP_FILE =
        "js/script.js";

    /*
    ==========================================================================
    Resolve Static Root
    ==========================================================================
    */

    function getStaticRootURL() {
        if (
            Speciedex.staticRootURL instanceof URL
        ) {
            return Speciedex.staticRootURL;
        }

        const currentScript =
            document.currentScript;

        if (currentScript?.src) {
            Speciedex.staticRootURL =
                new URL(
                    "./",
                    currentScript.src
                );

            return Speciedex.staticRootURL;
        }

        Speciedex.staticRootURL =
            new URL(
                "/static/",
                window.location.origin
            );

        return Speciedex.staticRootURL;
    }

    /*
    ==========================================================================
    Resolve Static Asset
    ==========================================================================
    */

    function getStaticURL(path) {
        const value =
            String(path ?? "")
                .trim()
                .replace(/^\/+/, "");

        if (!value) {
            throw new TypeError(
                "A static asset path is required."
            );
        }

        if (
            value.includes("..") ||
            value.includes("\\")
        ) {
            throw new TypeError(
                `Invalid static asset path: ${path}`
            );
        }

        return new URL(
            value,
            getStaticRootURL()
        ).href;
    }

    /*
    ==========================================================================
    Find Existing Script
    ==========================================================================
    */

    function findExistingScript(url) {
        return Array.from(
            document.scripts
        ).find(
            (script) =>
                script.src === url
        ) || null;
    }

    /*
    ==========================================================================
    Load Script
    ==========================================================================
    */

    function loadScript(path) {
        const url =
            getStaticURL(path);

        const existing =
            findExistingScript(url);

        if (existing) {
            if (
                existing.dataset
                    .speciedexLoaded ===
                "true"
            ) {
                return Promise.resolve(
                    existing
                );
            }

            return new Promise(
                (resolve, reject) => {
                    existing.addEventListener(
                        "load",
                        () => {
                            existing.dataset
                                .speciedexLoaded =
                                "true";

                            resolve(existing);
                        },
                        {
                            once: true
                        }
                    );

                    existing.addEventListener(
                        "error",
                        () => {
                            reject(
                                new Error(
                                    `Unable to load JavaScript file: ${url}`
                                )
                            );
                        },
                        {
                            once: true
                        }
                    );
                }
            );
        }

        return new Promise(
            (resolve, reject) => {
                const script =
                    document.createElement(
                        "script"
                    );

                script.src = url;
                script.async = false;

                script.dataset
                    .speciedexEntry =
                    path;

                script.addEventListener(
                    "load",
                    () => {
                        script.dataset
                            .speciedexLoaded =
                            "true";

                        resolve(script);
                    },
                    {
                        once: true
                    }
                );

                script.addEventListener(
                    "error",
                    () => {
                        script.remove();

                        reject(
                            new Error(
                                `Unable to load JavaScript file: ${url}`
                            )
                        );
                    },
                    {
                        once: true
                    }
                );

                document.head.appendChild(
                    script
                );
            }
        );
    }

    /*
    ==========================================================================
    Load Bootstrap
    ==========================================================================
    */

    async function loadBootstrap() {
        if (Speciedex.bootstrapEntryLoaded) {
            return;
        }

        Speciedex.bootstrapEntryLoaded = true;

        try {
            await loadScript(
                BOOTSTRAP_FILE
            );

            document.dispatchEvent(
                new CustomEvent(
                    "speciedex:bootstrap-loaded",
                    {
                        detail: {
                            url:
                                getStaticURL(
                                    BOOTSTRAP_FILE
                                )
                        }
                    }
                )
            );
        } catch (error) {
            Speciedex.bootstrapEntryLoaded =
                false;

            console.error(
                "Speciedex JavaScript bootstrap loading failed:",
                error
            );

            document.dispatchEvent(
                new CustomEvent(
                    "speciedex:error",
                    {
                        detail: {
                            phase:
                                "bootstrap-loading",
                            error
                        }
                    }
                )
            );

            throw error;
        }
    }

    /*
    ==========================================================================
    Public Entry-Point API
    ==========================================================================
    */

    Speciedex.getStaticRootURL =
        getStaticRootURL;

    Speciedex.getStaticURL =
        getStaticURL;

    Speciedex.loadScript =
        loadScript;

    Speciedex.loadBootstrap =
        loadBootstrap;

    /*
    ==========================================================================
    Start
    ==========================================================================
    */

    loadBootstrap().catch(
        () => {
            /*
            ------------------------------------------------------------------
            Error already reported and dispatched above.
            ------------------------------------------------------------------
            */
        }
    );
})();
