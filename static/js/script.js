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

    • Loading internal JavaScript modules
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
    Internal modules.

    Order matters:

        data.js
            Shared data and JSON utilities.

        includes.js
            Loads reusable HTML partials.

        header.js
        splash.js
        nav.js
        footer.js
            Depend on included markup.

        statistics.js
            Depends on data.js.
    --------------------------------------------------------------------------
    */

    const modules = [
        "data.js",
        "includes.js",
        "header.js",
        "splash.js",
        "nav.js",
        "footer.js",
        "statistics.js"
    ];

    /*
    --------------------------------------------------------------------------
    Resolve the current /static/js/ directory.

    Current file:

        /static/js/script.js

    Module files:

        /static/js/data.js
        /static/js/includes.js
        ...
    --------------------------------------------------------------------------
    */

    function getModuleRoot() {
        if (
            Speciedex.moduleRootURL
            instanceof URL
        ) {
            return Speciedex.moduleRootURL;
        }

        const currentScript =
            document.currentScript;

        if (currentScript?.src) {
            Speciedex.moduleRootURL =
                new URL(
                    "./",
                    currentScript.src
                );

            return Speciedex.moduleRootURL;
        }

        Speciedex.moduleRootURL =
            new URL(
                "/static/js/",
                window.location.origin
            );

        return Speciedex.moduleRootURL;
    }

    /*
    --------------------------------------------------------------------------
    Resolve one module URL.
    --------------------------------------------------------------------------
    */

    function getModuleURL(filename) {
        return new URL(
            filename,
            getModuleRoot()
        ).href;
    }

    /*
    --------------------------------------------------------------------------
    Locate an existing script element.
    --------------------------------------------------------------------------
    */

    function findExistingScript(url) {
        return Array.from(
            document.scripts
        ).find((script) => {
            return script.src === url;
        });
    }

    /*
    --------------------------------------------------------------------------
    Load one module.
    --------------------------------------------------------------------------
    */

    function loadModule(filename) {
        const url =
            getModuleURL(filename);

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
                                    `Unable to load module: ${url}`
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
                script.defer = true;

                script.dataset
                    .speciedexModule =
                    filename;

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
                                `Unable to load module: ${url}`
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
    --------------------------------------------------------------------------
    Load all modules sequentially.

    Sequential loading preserves dependency order.
    --------------------------------------------------------------------------
    */

    async function loadModules() {
        for (const filename of modules) {
            await loadModule(filename);
        }
    }

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
    Initialize the site.
    --------------------------------------------------------------------------
    */

    async function initializeSite() {
        if (Speciedex.siteInitialized) {
            return;
        }

        Speciedex.siteInitialized = true;

        try {
            /*
            --------------------------------------------------------------
            Load HTML partials first.
            --------------------------------------------------------------
            */

            if (
                typeof Speciedex
                    .loadIncludes ===
                "function"
            ) {
                await Speciedex
                    .loadIncludes(
                        document
                    );
            }

            /*
            --------------------------------------------------------------
            Initialize modules.
            --------------------------------------------------------------
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
            --------------------------------------------------------------
            Site ready.
            --------------------------------------------------------------
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
                "Speciedex initialization failed:",
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
    Start the bootstrap process.
    --------------------------------------------------------------------------
    */

    async function bootstrap() {
        try {
            await loadModules();

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

                return;
            }

            await initializeSite();
        } catch (error) {
            console.error(
                "Speciedex module loading failed:",
                error
            );

            document.dispatchEvent(
                new CustomEvent(
                    "speciedex:error",
                    {
                        detail: {
                            phase:
                                "module-loading",

                            error
                        }
                    }
                )
            );
        }
    }

    /*
    --------------------------------------------------------------------------
    Public bootstrap API.
    --------------------------------------------------------------------------
    */

    Speciedex.getModuleRoot =
        getModuleRoot;

    Speciedex.getModuleURL =
        getModuleURL;

    Speciedex.loadModule =
        loadModule;

    Speciedex.loadModules =
        loadModules;

    Speciedex.initializeSite =
        initializeSite;

    bootstrap();
})();
