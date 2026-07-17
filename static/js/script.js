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

    const Speciedex = window.Speciedex || {};

    async function initializeSite() {

        /*
        ----------------------------------------------------------------------
        Load HTML partials first.
        ----------------------------------------------------------------------
        */

        if (typeof Speciedex.loadIncludes === "function") {
            await Speciedex.loadIncludes(document);
        }

        /*
        ----------------------------------------------------------------------
        Initialize modules.
        ----------------------------------------------------------------------
        */

        initializeModule("Header");
        initializeModule("Splash");
        initializeModule("Navigation");
        initializeModule("Footer");
        initializeModule("CurrentYear");
        initializeModule("ExternalLinks");
        initializeModule("Statistics");
        initializeModule("Releases");
        initializeModule("Status");
        initializeModule("Activity");

        /*
        ----------------------------------------------------------------------
        Site ready.
        ----------------------------------------------------------------------
        */

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:ready"
            )
        );

    }

    function initializeModule(name) {

        const fn = Speciedex[`initialize${name}`];

        if (typeof fn === "function") {
            fn();
        }

    }

    if (document.readyState === "loading") {

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

})();
