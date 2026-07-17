"use strict";

/*
==============================================================================
Speciedex.org
Site Bootstrap
==============================================================================

Internal wrapper.

Loaded only by:

    /static/script.js

Responsible for:

    • Waiting for DOM readiness
    • Loading HTML partials
    • Initializing modules
    • Broadcasting lifecycle events

Contains NO page-specific logic.

------------------------------------------------------------------------------
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (Speciedex.siteBootstrapLoaded) {
        return;
    }

    Speciedex.siteBootstrapLoaded = true;

    /*
    --------------------------------------------------------------------------
    Initialize one registered module.
    --------------------------------------------------------------------------
    */

    async function initializeModule(name) {
        const fn =
            Speciedex[
                `initialize${name}`
            ];

        if (
            typeof fn !==
            "function"
        ) {
            return;
        }

        await fn();
    }

    /*
    --------------------------------------------------------------------------
    Initialize the complete site.
    --------------------------------------------------------------------------
    */

    async function initializeSite() {
        if (Speciedex.siteInitialized) {
            return;
        }

        Speciedex.siteInitialized = true;

        try {
            /*
            ------------------------------------------------------------------
            Load HTML partials first.
            ------------------------------------------------------------------
            */

            if (
                typeof Speciedex.loadIncludes ===
                "function"
            ) {
                await Speciedex.loadIncludes(
                    document
                );
            }

            /*
            ------------------------------------------------------------------
            Initialize modules.
            ------------------------------------------------------------------
            */

            await initializeModule(
                "Header"
            );

            await initializeModule(
                "Splash"
            );

            await initializeModule(
                "Navigation"
            );

            await initializeModule(
                "Footer"
            );

            await initializeModule(
                "CurrentYear"
            );

            await initializeModule(
                "ExternalLinks"
            );

            await initializeModule(
                "Statistics"
            );

            await initializeModule(
                "Releases"
            );

            await initializeModule(
                "Status"
            );

            await initializeModule(
                "Activity"
            );

            /*
            ------------------------------------------------------------------
            Site ready.
            ------------------------------------------------------------------
            */

            document.dispatchEvent(
                new CustomEvent(
                    "speciedex:ready",
                    {
                        detail: {
                            Speciedex
                        }
                    }
                )
            );
        } catch (error) {
            Speciedex.siteInitialized =
                false;

            console.error(
                "Speciedex site initialization failed:",
                error
            );

            document.dispatchEvent(
                new CustomEvent(
                    "speciedex:error",
                    {
                        detail: {
                            phase:
                                "initialization",

                            error
                        }
                    }
                )
            );
        }
    }

    /*
    --------------------------------------------------------------------------
    Wait for DOM readiness.
    --------------------------------------------------------------------------
    */

    if (
        document.readyState ===
        "loading"
    ) {
        document.addEventListener(
            "DOMContentLoaded",
            initializeSite,
            {
                once: true
            }
        );
    } else {
        initializeSite();
    }

    /*
    --------------------------------------------------------------------------
    Public bootstrap API.
    --------------------------------------------------------------------------
    */

    Speciedex.initializeModule =
        initializeModule;

    Speciedex.initializeSite =
        initializeSite;
})();
