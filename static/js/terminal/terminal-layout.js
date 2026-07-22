/*
========================================================================
Speciedex.org
Terminal Layout Controller
========================================================================

Layout and region-management service for SpeciedexTerminal.

Provides:

    • terminal layout modes
    • splash and console region visibility
    • split sizing
    • responsive layout selection
    • persisted layout preferences
    • fullscreen coordination
    • layout inspection
    • resize observation
    • terminal commands

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "Layout";

    const VERSION =
        "2.0.0";

    const STORAGE_PREFIX =
        "speciedex-terminal:layout:";

    const MODES =
        Object.freeze([
            "standard",
            "compact",
            "wide",
            "split",
            "fullscreen",
            "console-only",
            "splash-only"
        ]);

    const DEFAULT_OPTIONS =
        Object.freeze({
            mode:
                "standard",

            splashRatio:
                0.42,

            minimumSplashRatio:
                0.15,

            maximumSplashRatio:
                0.8,

            persist:
                true,

            responsive:
                true,

            compactBreakpoint:
                720,

            wideBreakpoint:
                1280
        });

    /*
    ==========================================================================
    Utilities
    ==========================================================================
    */

    function clamp(
        value,
        minimum,
        maximum
    ) {
        return Math.min(
            maximum,
            Math.max(
                minimum,
                value
            )
        );
    }

    function parseBoolean(
        value,
        fallback = false
    ) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return fallback;
        }

        return ![
            "false",
            "0",
            "no",
            "off"
        ].includes(
            String(value)
                .trim()
                .toLowerCase()
        );
    }

    function parseNumber(
        value,
        fallback
    ) {
        const parsed =
            Number(value);

        return Number.isFinite(
            parsed
        )
            ? parsed
            : fallback;
    }

    function normalizeMode(
        mode
    ) {
        return String(
            mode ?? ""
        )
            .trim()
            .toLowerCase();
    }

    function safeStorage() {
        try {
            const key =
                "__speciedex_layout_probe__";

            window.localStorage.setItem(
                key,
                key
            );

            window.localStorage.removeItem(
                key
            );

            return window.localStorage;
        } catch (error) {
            return null;
        }
    }

    /*
    ==========================================================================
    Layout Controller
    ==========================================================================
    */

    class LayoutController
        extends EventTarget {
        constructor(
            context,
            options = {}
        ) {
            super();

            this.context =
                context;

            this.root =
                context.root;

            this.options = {
                ...DEFAULT_OPTIONS,
                ...options
            };

            this.storage =
                safeStorage();

            this.storageKey =
                `${STORAGE_PREFIX}${
                    this.root.dataset.terminalInstance ||
                    "default"
                }`;

            this.elements = {
                shell:
                    this.root.querySelector(
                        "[data-terminal-shell]"
                    ) ||
                    this.root,

                regions:
                    this.root.querySelector(
                        "[data-terminal-regions]"
                    ),

                splash:
                    this.root.querySelector(
                        "[data-terminal-splash]"
                    ),

                console:
                    this.root.querySelector(
                        "[data-terminal-console-region]"
                    ),

                screen:
                    this.root.querySelector(
                        "[data-terminal-screen]"
                    )
            };

            this.mode =
                "standard";

            this.splashRatio =
                clamp(
                    parseNumber(
                        this.options.splashRatio,
                        DEFAULT_OPTIONS.splashRatio
                    ),
                    this.options.minimumSplashRatio,
                    this.options.maximumSplashRatio
                );

            this.previousMode =
                null;

            this.destroyed =
                false;

            this.resizeObserver =
                null;

            this.boundResize =
                () =>
                    this.handleResize();

            this.restore();
            this.bind();
            this.apply();
        }

        /*
        ======================================================================
        Persistence
        ======================================================================
        */

        restore() {
            if (
                !this.options.persist ||
                !this.storage
            ) {
                this.mode =
                    normalizeMode(
                        this.options.mode
                    ) ||
                    "standard";

                return;
            }

            try {
                const stored =
                    JSON.parse(
                        this.storage.getItem(
                            this.storageKey
                        ) ||
                        "{}"
                    );

                this.mode =
                    MODES.includes(
                        normalizeMode(
                            stored.mode
                        )
                    )
                        ? normalizeMode(
                            stored.mode
                        )
                        : normalizeMode(
                            this.options.mode
                        ) ||
                        "standard";

                this.splashRatio =
                    clamp(
                        parseNumber(
                            stored.splashRatio,
                            this.splashRatio
                        ),
                        this.options.minimumSplashRatio,
                        this.options.maximumSplashRatio
                    );
            } catch (error) {
                this.mode =
                    normalizeMode(
                        this.options.mode
                    ) ||
                    "standard";
            }
        }

        persist() {
            if (
                !this.options.persist ||
                !this.storage
            ) {
                return;
            }

            try {
                this.storage.setItem(
                    this.storageKey,
                    JSON.stringify({
                        mode:
                            this.mode,

                        splashRatio:
                            this.splashRatio
                    })
                );
            } catch (error) {
                /*
                --------------------------------------------------------------
                Storage is optional. Layout behavior must continue without it.
                --------------------------------------------------------------
                */
            }
        }

        resetPersistence() {
            try {
                this.storage?.removeItem(
                    this.storageKey
                );
            } catch (error) {
                /*
                --------------------------------------------------------------
                Ignore unavailable storage.
                --------------------------------------------------------------
                */
            }
        }

        /*
        ======================================================================
        Binding
        ======================================================================
        */

        bind() {
            if (
                this.options.responsive &&
                "ResizeObserver" in
                window
            ) {
                this.resizeObserver =
                    new ResizeObserver(
                        this.boundResize
                    );

                this.resizeObserver.observe(
                    this.root
                );
            } else if (
                this.options.responsive
            ) {
                window.addEventListener(
                    "resize",
                    this.boundResize
                );
            }
        }

        /*
        ======================================================================
        Mode Management
        ======================================================================
        */

        setMode(
            mode,
            options = {}
        ) {
            const normalized =
                normalizeMode(
                    mode
                );

            if (
                !MODES.includes(
                    normalized
                )
            ) {
                throw new Error(
                    `Unsupported layout mode: ${mode}`
                );
            }

            if (
                normalized ===
                this.mode &&
                options.force !==
                true
            ) {
                return this.mode;
            }

            const previous =
                this.mode;

            this.previousMode =
                previous;

            this.mode =
                normalized;

            this.apply();

            if (
                options.persist !==
                false
            ) {
                this.persist();
            }

            this.dispatchEvent(
                new CustomEvent(
                    "mode",
                    {
                        detail: {
                            previous,
                            mode:
                                this.mode
                        }
                    }
                )
            );

            this.context.events?.emit?.(
                "layout:mode",
                {
                    previous,
                    mode:
                        this.mode
                }
            );

            this.root.dispatchEvent(
                new CustomEvent(
                    "speciedex:terminal-layout",
                    {
                        bubbles:
                            true,

                        detail: {
                            previous,
                            mode:
                                this.mode,

                            controller:
                                this
                        }
                    }
                )
            );

            return this.mode;
        }

        restorePreviousMode() {
            if (
                !this.previousMode ||
                !MODES.includes(
                    this.previousMode
                )
            ) {
                return this.setMode(
                    "standard"
                );
            }

            return this.setMode(
                this.previousMode
            );
        }

        /*
        ======================================================================
        Region Sizing
        ======================================================================
        */

        setSplashRatio(
            ratio,
            options = {}
        ) {
            const parsed =
                parseNumber(
                    ratio,
                    this.splashRatio
                );

            this.splashRatio =
                clamp(
                    parsed,
                    this.options.minimumSplashRatio,
                    this.options.maximumSplashRatio
                );

            this.applySplit();

            if (
                options.persist !==
                false
            ) {
                this.persist();
            }

            this.dispatchEvent(
                new CustomEvent(
                    "ratio",
                    {
                        detail: {
                            splashRatio:
                                this.splashRatio,

                            consoleRatio:
                                1 -
                                this.splashRatio
                        }
                    }
                )
            );

            return this.splashRatio;
        }

        applySplit() {
            const regions =
                this.elements.regions;

            if (!regions) {
                return;
            }

            regions.style.setProperty(
                "--terminal-splash-ratio",
                String(
                    this.splashRatio
                )
            );

            regions.style.setProperty(
                "--terminal-console-ratio",
                String(
                    1 -
                    this.splashRatio
                )
            );

            regions.style.setProperty(
                "--terminal-splash-percent",
                `${(
                    this.splashRatio *
                    100
                ).toFixed(2)}%`
            );

            regions.style.setProperty(
                "--terminal-console-percent",
                `${(
                    (
                        1 -
                        this.splashRatio
                    ) *
                    100
                ).toFixed(2)}%`
            );
        }

        /*
        ======================================================================
        Region Visibility
        ======================================================================
        */

        setRegionVisibility(
            name,
            visible
        ) {
            if (
                typeof this.context.setRegionVisibility ===
                "function"
            ) {
                return this.context.setRegionVisibility(
                    name,
                    visible
                );
            }

            const element =
                name ===
                "splash"
                    ? this.elements.splash
                    : name ===
                        "console"
                        ? this.elements.console
                        : name ===
                            "terminal"
                            ? this.elements.regions
                            : null;

            if (!element) {
                return false;
            }

            element.hidden =
                !visible;

            element.dataset.collapsed =
                visible
                    ? "false"
                    : "true";

            return true;
        }

        applyVisibilityForMode() {
            switch (this.mode) {
                case "console-only":
                    this.setRegionVisibility(
                        "terminal",
                        true
                    );

                    this.setRegionVisibility(
                        "splash",
                        false
                    );

                    this.setRegionVisibility(
                        "console",
                        true
                    );

                    break;

                case "splash-only":
                    this.setRegionVisibility(
                        "terminal",
                        true
                    );

                    this.setRegionVisibility(
                        "splash",
                        true
                    );

                    this.setRegionVisibility(
                        "console",
                        false
                    );

                    break;

                default:
                    this.setRegionVisibility(
                        "terminal",
                        true
                    );

                    this.setRegionVisibility(
                        "splash",
                        true
                    );

                    this.setRegionVisibility(
                        "console",
                        true
                    );
            }
        }

        /*
        ======================================================================
        Apply Layout
        ======================================================================
        */

        apply() {
            this.root.dataset.terminalLayout =
                this.mode;

            this.elements.shell.dataset.terminalLayout =
                this.mode;

            for (const mode of MODES) {
                this.root.classList.toggle(
                    `terminal-layout-${mode}`,
                    mode ===
                    this.mode
                );

                this.elements.shell.classList.toggle(
                    `terminal-layout-${mode}`,
                    mode ===
                    this.mode
                );
            }

            this.applyVisibilityForMode();
            this.applySplit();

            if (
                this.mode ===
                "compact"
            ) {
                this.elements.screen?.setAttribute(
                    "data-terminal-density",
                    "compact"
                );
            } else {
                this.elements.screen?.removeAttribute(
                    "data-terminal-density"
                );
            }

            this.root.setAttribute(
                "aria-label",
                `SpeciedexTerminal ${this.mode} layout`
            );
        }

        /*
        ======================================================================
        Responsive Behavior
        ======================================================================
        */

        handleResize() {
            if (
                !this.options.responsive ||
                this.destroyed
            ) {
                return;
            }

            const width =
                this.root.getBoundingClientRect()
                    .width;

            if (
                this.mode ===
                    "fullscreen" ||
                this.mode ===
                    "console-only" ||
                this.mode ===
                    "splash-only"
            ) {
                return;
            }

            let suggested =
                this.mode;

            if (
                width <=
                this.options.compactBreakpoint
            ) {
                suggested =
                    "compact";
            } else if (
                width >=
                this.options.wideBreakpoint
            ) {
                suggested =
                    "wide";
            } else if (
                [
                    "compact",
                    "wide"
                ].includes(
                    this.mode
                )
            ) {
                suggested =
                    "standard";
            }

            if (
                suggested !==
                this.mode
            ) {
                this.setMode(
                    suggested,
                    {
                        persist:
                            false
                    }
                );
            }
        }

        /*
        ======================================================================
        Fullscreen Coordination
        ======================================================================
        */

        async enterFullscreen() {
            const shell =
                this.elements.shell;

            if (
                document.fullscreenElement ===
                shell
            ) {
                return true;
            }

            this.previousMode =
                this.mode;

            try {
                await shell.requestFullscreen();

                this.setMode(
                    "fullscreen",
                    {
                        persist:
                            false
                    }
                );

                return true;
            } catch (error) {
                shell.classList.add(
                    "terminal-fullscreen-fallback"
                );

                this.setMode(
                    "fullscreen",
                    {
                        persist:
                            false
                    }
                );

                return false;
            }
        }

        async exitFullscreen() {
            const shell =
                this.elements.shell;

            try {
                if (
                    document.fullscreenElement
                ) {
                    await document.exitFullscreen();
                }
            } catch (error) {
                /*
                --------------------------------------------------------------
                Fallback class is still removed below.
                --------------------------------------------------------------
                */
            }

            shell.classList.remove(
                "terminal-fullscreen-fallback"
            );

            this.restorePreviousMode();

            return true;
        }

        async toggleFullscreen() {
            if (
                this.mode ===
                    "fullscreen" ||
                document.fullscreenElement ===
                    this.elements.shell ||
                this.elements.shell.classList.contains(
                    "terminal-fullscreen-fallback"
                )
            ) {
                return this.exitFullscreen();
            }

            return this.enterFullscreen();
        }

        /*
        ======================================================================
        Inspection
        ======================================================================
        */

        status() {
            return {
                version:
                    VERSION,

                mode:
                    this.mode,

                previousMode:
                    this.previousMode,

                splashRatio:
                    this.splashRatio,

                consoleRatio:
                    1 -
                    this.splashRatio,

                responsive:
                    this.options.responsive,

                persist:
                    this.options.persist,

                regions: {
                    terminal:
                        this.elements.regions
                            ? !this.elements.regions.hidden
                            : null,

                    splash:
                        this.elements.splash
                            ? !this.elements.splash.hidden
                            : null,

                    console:
                        this.elements.console
                            ? !this.elements.console.hidden
                            : null
                },

                width:
                    this.root.getBoundingClientRect()
                        .width,

                fullscreen:
                    document.fullscreenElement ===
                        this.elements.shell ||
                    this.elements.shell.classList.contains(
                        "terminal-fullscreen-fallback"
                    )
            };
        }

        reset() {
            this.resetPersistence();

            this.splashRatio =
                DEFAULT_OPTIONS.splashRatio;

            this.previousMode =
                null;

            return this.setMode(
                "standard",
                {
                    persist:
                        false,

                    force:
                        true
                }
            );
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.resizeObserver?.
                disconnect();

            window.removeEventListener(
                "resize",
                this.boundResize
            );

            this.destroyed =
                true;

            this.dispatchEvent(
                new CustomEvent(
                    "destroy"
                )
            );
        }
    }

    /*
    ==========================================================================
    Initialization
    ==========================================================================
    */

    function initialize(
        context
    ) {
        if (
            context.layout instanceof
            LayoutController
        ) {
            return context.layout;
        }

        const controller =
            new LayoutController(
                context,
                {
                    mode:
                        context.root?.
                            dataset.
                            terminalLayout ||
                        DEFAULT_OPTIONS.mode,

                    splashRatio:
                        parseNumber(
                            context.root?.
                                dataset.
                                terminalSplashRatio,
                            DEFAULT_OPTIONS.splashRatio
                        ),

                    persist:
                        parseBoolean(
                            context.root?.
                                dataset.
                                terminalPersistLayout,
                            true
                        ),

                    responsive:
                        parseBoolean(
                            context.root?.
                                dataset.
                                terminalResponsiveLayout,
                            true
                        ),

                    compactBreakpoint:
                        parseNumber(
                            context.root?.
                                dataset.
                                terminalCompactBreakpoint,
                            DEFAULT_OPTIONS.compactBreakpoint
                        ),

                    wideBreakpoint:
                        parseNumber(
                            context.root?.
                                dataset.
                                terminalWideBreakpoint,
                            DEFAULT_OPTIONS.wideBreakpoint
                        )
                }
            );

        context.layout =
            controller;

        context.registerService?.(
            "layout",
            controller
        );

        return controller;
    }

    /*
    ==========================================================================
    Commands
    ==========================================================================
    */

    const commands =
        [
            {
                name:
                    "layout",

                category:
                    "interface",

                description:
                    "Display or set the terminal layout mode.",

                usage:
                    "layout [standard|compact|wide|split|fullscreen|console-only|splash-only]",

                handler: async ({
                    args,
                    context,
                    write,
                    writeJSON
                }) => {
                    if (!args.length) {
                        return writeJSON(
                            context.layout.status()
                        );
                    }

                    const mode =
                        args[0];

                    if (
                        mode ===
                        "fullscreen"
                    ) {
                        await context.layout.enterFullscreen();
                    } else {
                        context.layout.setMode(
                            mode
                        );
                    }

                    return write(
                        `Layout: ${context.layout.mode}`,
                        "success"
                    );
                }
            },

            {
                name:
                    "layout-ratio",

                category:
                    "interface",

                description:
                    "Set the terminal splash-to-console height ratio.",

                usage:
                    "layout-ratio <0.15-0.80|15%-80%>",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    if (!args[0]) {
                        return write(
                            `Splash ratio: ${(
                                context.layout.splashRatio *
                                100
                            ).toFixed(2)}%`
                        );
                    }

                    const raw =
                        String(
                            args[0]
                        );

                    const value =
                        raw.endsWith("%")
                            ? Number(
                                raw.slice(
                                    0,
                                    -1
                                )
                            ) /
                            100
                            : Number(raw);

                    const ratio =
                        context.layout.setSplashRatio(
                            value
                        );

                    return write(
                        `Splash ratio: ${(
                            ratio *
                            100
                        ).toFixed(2)}%`,
                        "success"
                    );
                }
            },

            {
                name:
                    "layout-status",

                category:
                    "interface",

                description:
                    "Display current terminal layout state.",

                usage:
                    "layout-status",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.layout.status()
                    )
            },

            {
                name:
                    "layout-reset",

                category:
                    "interface",

                description:
                    "Reset terminal layout preferences.",

                usage:
                    "layout-reset",

                handler: ({
                    context,
                    write
                }) => {
                    context.layout.reset();

                    return write(
                        "Terminal layout reset.",
                        "success"
                    );
                }
            },

            {
                name:
                    "layout-show",

                category:
                    "interface",

                description:
                    "Show a terminal region.",

                usage:
                    "layout-show <terminal|splash|console>",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const region =
                        args[0];

                    if (
                        ![
                            "terminal",
                            "splash",
                            "console"
                        ].includes(
                            region
                        )
                    ) {
                        throw new Error(
                            "Use: layout-show terminal|splash|console"
                        );
                    }

                    context.layout.setRegionVisibility(
                        region,
                        true
                    );

                    return write(
                        `Visible: ${region}`,
                        "success"
                    );
                }
            },

            {
                name:
                    "layout-hide",

                category:
                    "interface",

                description:
                    "Hide a terminal region.",

                usage:
                    "layout-hide <terminal|splash|console>",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const region =
                        args[0];

                    if (
                        ![
                            "terminal",
                            "splash",
                            "console"
                        ].includes(
                            region
                        )
                    ) {
                        throw new Error(
                            "Use: layout-hide terminal|splash|console"
                        );
                    }

                    context.layout.setRegionVisibility(
                        region,
                        false
                    );

                    return write(
                        `Hidden: ${region}`,
                        "success"
                    );
                }
            }
        ];

    /*
    ==========================================================================
    Public Module API
    ==========================================================================
    */

    const api =
        Object.freeze({
            name:
                MODULE_NAME,

            version:
                VERSION,

            MODES,
            DEFAULT_OPTIONS,
            LayoutController,

            normalizeMode,
            parseBoolean,
            parseNumber,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalLayout =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

    document.dispatchEvent(
        new CustomEvent(
            "speciedex:terminal-module-available",
            {
                detail: {
                    name:
                        MODULE_NAME,

                    module:
                        api
                }
            }
        )
    );
})(window, document);
