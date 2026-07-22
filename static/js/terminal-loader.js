/*
========================================================================
Speciedex.org
SpeciedexTerminal Loader
========================================================================

Manifest-driven loader for the complete modular SpeciedexTerminal runtime.

Responsibilities:

    • Load /static/js/terminal/manifest.json
    • Merge repository manifest entries with the built-in runtime graph
    • Preserve required modules when a repository manifest is incomplete
    • Resolve module dependencies deterministically
    • Load stylesheets before terminal modules
    • Prevent duplicate script and stylesheet injection
    • Track loaded, pending, failed, disabled, and optional modules
    • Register workers and runtime modules
    • Expose loader diagnostics and lifecycle events

Required terminal splash chain:

    terminal-matrix.js
        |
        v
    terminal-cmatrix.js
        |
        v
    terminal-zmatrix.js

    terminal-wordcloud.js

    zmatrix + wordcloud + events + settings
        |
        v
    terminal-splash.js
        |
        v
    terminal-help.js
        |
        v
    speciedex-terminal.js

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const GLOBAL_NAME =
        "SpeciedexTerminalLoader";

    const VERSION =
        "3.0.0";

    const BASE_PATH =
        "/static/js/terminal/";

    const MANIFEST_URL =
        `${BASE_PATH}manifest.json`;

    /*
    ==========================================================================
    Worker Registry
    ==========================================================================
    */

    const WORKERS =
        Object.freeze({
            filter:
                `${BASE_PATH}workers/filter-worker.js`,

            index:
                `${BASE_PATH}workers/index-worker.js`,

            library:
                `${BASE_PATH}workers/library-worker.js`,

            map:
                `${BASE_PATH}workers/map-worker.js`,

            provider:
                `${BASE_PATH}workers/provider-worker.js`,

            search:
                `${BASE_PATH}workers/search-worker.js`,

            statistics:
                `${BASE_PATH}workers/statistics-worker.js`,

            timeline:
                `${BASE_PATH}workers/timeline-worker.js`
        });

    /*
    ==========================================================================
    Built-in Runtime Graph
    ==========================================================================
    */

    const DEFAULT_MODULES =
        Object.freeze([
            /*
            ------------------------------------------------------------------
            Foundation
            ------------------------------------------------------------------
            */
            {
                name:
                    "state",

                path:
                    "terminal-state.js"
            },

            {
                name:
                    "storage",

                path:
                    "terminal-storage.js",

                dependencies:
                    [
                        "state"
                    ]
            },

            {
                name:
                    "events",

                path:
                    "terminal-events.js",

                dependencies:
                    [
                        "state"
                    ]
            },

            {
                name:
                    "log",

                path:
                    "terminal-log.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "loading",

                path:
                    "terminal-loading.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "theme",

                path:
                    "terminal-theme.js",

                dependencies:
                    [
                        "storage"
                    ]
            },

            {
                name:
                    "settings",

                path:
                    "terminal-settings.js",

                dependencies:
                    [
                        "storage",
                        "events"
                    ]
            },

            {
                name:
                    "library",

                path:
                    "terminal-library.js",

                dependencies:
                    [
                        "storage",
                        "events"
                    ]
            },

            {
                name:
                    "index",

                path:
                    "terminal-index.js",

                dependencies:
                    [
                        "library"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Interface
            ------------------------------------------------------------------
            */
            {
                name:
                    "layout",

                path:
                    "terminal-layout.js",

                dependencies:
                    [
                        "state",
                        "settings"
                    ]
            },

            {
                name:
                    "windows",

                path:
                    "terminal-windows.js",

                dependencies:
                    [
                        "layout",
                        "events"
                    ]
            },

            {
                name:
                    "toolbar",

                path:
                    "terminal-toolbar.js",

                dependencies:
                    [
                        "layout",
                        "events"
                    ]
            },

            {
                name:
                    "statusbar",

                path:
                    "terminal-statusbar.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "notifications",

                path:
                    "terminal-notifications.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "progress",

                path:
                    "terminal-progress.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "console",

                path:
                    "terminal-console.js",

                dependencies:
                    [
                        "log",
                        "events"
                    ]
            },

            {
                name:
                    "keyboard",

                path:
                    "terminal-keyboard.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "contextmenu",

                path:
                    "terminal-contextmenu.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "history",

                path:
                    "terminal-history.js",

                dependencies:
                    [
                        "storage",
                        "events"
                    ]
            },

            {
                name:
                    "bookmarks",

                path:
                    "terminal-bookmarks.js",

                dependencies:
                    [
                        "storage",
                        "events"
                    ]
            },

            {
                name:
                    "recent",

                path:
                    "terminal-recent.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Renderers
            ------------------------------------------------------------------
            */
            {
                name:
                    "table",

                path:
                    "terminal-table.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "lists",

                path:
                    "terminal-lists.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "grid",

                path:
                    "terminal-grid.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "tree",

                path:
                    "terminal-tree.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "charts",

                path:
                    "terminal-charts.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "graphs",

                path:
                    "terminal-graphs.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "map",

                path:
                    "terminal-map.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "heatmap",

                path:
                    "terminal-heatmap.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "matrix",

                path:
                    "terminal-matrix.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            {
                name:
                    "timeline",

                path:
                    "terminal-timeline.js",

                dependencies:
                    [
                        "layout"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Data and Commands
            ------------------------------------------------------------------
            */
            {
                name:
                    "api",

                path:
                    "terminal-api.js",

                dependencies:
                    [
                        "events",
                        "loading"
                    ]
            },

            {
                name:
                    "router",

                path:
                    "terminal-router.js",

                dependencies:
                    [
                        "events"
                    ]
            },

            {
                name:
                    "search",

                path:
                    "terminal-search.js",

                dependencies:
                    [
                        "api",
                        "library",
                        "index"
                    ]
            },

            {
                name:
                    "scan",

                path:
                    "terminal-scan.js",

                dependencies:
                    [
                        "api",
                        "events"
                    ]
            },

            {
                name:
                    "stream",

                path:
                    "terminal-stream.js",

                dependencies:
                    [
                        "api",
                        "events"
                    ]
            },

            {
                name:
                    "import",

                path:
                    "terminal-import.js",

                dependencies:
                    [
                        "library",
                        "events"
                    ]
            },

            {
                name:
                    "export",

                path:
                    "terminal-export.js",

                dependencies:
                    [
                        "library"
                    ]
            },

            {
                name:
                    "stats",

                path:
                    "terminal-stats.js",

                dependencies:
                    [
                        "library"
                    ]
            },

            {
                name:
                    "tags",

                path:
                    "terminal-tags.js",

                dependencies:
                    [
                        "library",
                        "storage"
                    ]
            },

            {
                name:
                    "provider-health",

                path:
                    "terminal-provider-health.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "provider-manager",

                path:
                    "terminal-provider-manager.js",

                dependencies:
                    [
                        "api",
                        "storage"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Archive
            ------------------------------------------------------------------
            */
            {
                name:
                    "checksums",

                path:
                    "archive/terminal-checksums.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "manifests",

                path:
                    "archive/terminal-manifests.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "releases",

                path:
                    "archive/terminal-releases.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "volumes",

                path:
                    "archive/terminal-volumes.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "records-archived",

                path:
                    "archive/terminal-records-archived.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "source-assertions",

                path:
                    "archive/terminal-source-assertions.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "synonyms",

                path:
                    "archive/terminal-synonyms.js",

                dependencies:
                    [
                        "api",
                        "search"
                    ]
            },

            {
                name:
                    "unresolved-conflicts",

                path:
                    "archive/terminal-unresolved-conflicts.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "archive-history",

                path:
                    "archive/terminal-archive-history.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            {
                name:
                    "last-updated",

                path:
                    "archive/terminal-last-updated.js",

                dependencies:
                    [
                        "api"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Providers
            ------------------------------------------------------------------
            */
            {
                name:
                    "providers",

                path:
                    "providers/terminal-providers.js",

                dependencies:
                    [
                        "api",
                        "search"
                    ]
            },

            {
                name:
                    "enabled-providers",

                path:
                    "providers/terminal-enabled-providers.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "eligible-providers",

                path:
                    "providers/terminal-eligible-providers.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-assertions",

                path:
                    "providers/terminal-provider-assertions.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-documentation",

                path:
                    "providers/terminal-provider-documentation.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-errors",

                path:
                    "providers/terminal-provider-errors.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-latency",

                path:
                    "providers/terminal-provider-latency.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-overlap",

                path:
                    "providers/terminal-provider-overlap.js",

                dependencies:
                    [
                        "providers"
                    ]
            },

            {
                name:
                    "provider-species",

                path:
                    "providers/terminal-provider-species.js",

                dependencies:
                    [
                        "providers",
                        "search"
                    ]
            },

            {
                name:
                    "provider-statistics",

                path:
                    "providers/terminal-provider-statistics.js",

                dependencies:
                    [
                        "providers",
                        "stats"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Taxonomy
            ------------------------------------------------------------------
            */
            {
                name:
                    "ranks",

                path:
                    "taxa/terminal-ranks.js",

                dependencies:
                    [
                        "api",
                        "search"
                    ]
            },

            {
                name:
                    "domains",

                path:
                    "taxa/terminal-domains.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "kingdoms",

                path:
                    "taxa/terminal-kingdoms.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "phyla",

                path:
                    "taxa/terminal-phyla.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "classes",

                path:
                    "taxa/terminal-classes.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "orders",

                path:
                    "taxa/terminal-orders.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "families",

                path:
                    "taxa/terminal-families.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "tribes",

                path:
                    "taxa/terminal-tribes.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "genera",

                path:
                    "taxa/terminal-genera.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            {
                name:
                    "species",

                path:
                    "taxa/terminal-species.js",

                dependencies:
                    [
                        "ranks",
                        "search"
                    ]
            },

            {
                name:
                    "subspecies",

                path:
                    "taxa/terminal-subspecies.js",

                dependencies:
                    [
                        "species"
                    ]
            },

            {
                name:
                    "varieties",

                path:
                    "taxa/terminal-varieties.js",

                dependencies:
                    [
                        "species"
                    ]
            },

            {
                name:
                    "forms",

                path:
                    "taxa/terminal-forms.js",

                dependencies:
                    [
                        "species"
                    ]
            },

            {
                name:
                    "clades",

                path:
                    "taxa/terminal-clades.js",

                dependencies:
                    [
                        "ranks"
                    ]
            },

            /*
            ------------------------------------------------------------------
            General Visualizations
            ------------------------------------------------------------------
            */
            {
                name:
                    "constellation",

                path:
                    "visualization/terminal-constellation.js",

                dependencies:
                    [
                        "graphs"
                    ]
            },

            {
                name:
                    "density",

                path:
                    "visualization/terminal-density.js",

                dependencies:
                    [
                        "heatmap"
                    ]
            },

            {
                name:
                    "forcegraph",

                path:
                    "visualization/terminal-forcegraph.js",

                dependencies:
                    [
                        "graphs"
                    ]
            },

            {
                name:
                    "globe",

                path:
                    "visualization/terminal-globe.js",

                dependencies:
                    [
                        "map"
                    ]
            },

            {
                name:
                    "heatmesh",

                path:
                    "visualization/terminal-heatmesh.js",

                dependencies:
                    [
                        "heatmap"
                    ]
            },

            {
                name:
                    "hexmap",

                path:
                    "visualization/terminal-hexmap.js",

                dependencies:
                    [
                        "map"
                    ]
            },

            {
                name:
                    "network",

                path:
                    "visualization/terminal-network.js",

                dependencies:
                    [
                        "graphs"
                    ]
            },

            {
                name:
                    "phylogeny",

                path:
                    "visualization/terminal-phylogeny.js",

                dependencies:
                    [
                        "tree"
                    ]
            },

            {
                name:
                    "provider-matrix",

                path:
                    "visualization/terminal-provider-matrix.js",

                dependencies:
                    [
                        "matrix",
                        "providers"
                    ]
            },

            {
                name:
                    "radial",

                path:
                    "visualization/terminal-radial.js",

                dependencies:
                    [
                        "charts"
                    ]
            },

            {
                name:
                    "range-map",

                path:
                    "visualization/terminal-range-map.js",

                dependencies:
                    [
                        "map"
                    ]
            },

            {
                name:
                    "sankey",

                path:
                    "visualization/terminal-sankey.js",

                dependencies:
                    [
                        "graphs"
                    ]
            },

            {
                name:
                    "streamgraph",

                path:
                    "visualization/terminal-streamgraph.js",

                dependencies:
                    [
                        "charts"
                    ]
            },

            {
                name:
                    "taxonomy-tree",

                path:
                    "visualization/terminal-taxonomy-tree.js",

                dependencies:
                    [
                        "tree",
                        "ranks"
                    ]
            },

            {
                name:
                    "time-slider",

                path:
                    "visualization/terminal-time-slider.js",

                dependencies:
                    [
                        "timeline"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Terminal Splash Chain
            ------------------------------------------------------------------
            */
            {
                name:
                    "cmatrix",

                path:
                    "visualization/terminal-cmatrix.js",

                dependencies:
                    [
                        "matrix"
                    ]
            },

            {
                name:
                    "zmatrix",

                path:
                    "visualization/terminal-zmatrix.js",

                dependencies:
                    [
                        "cmatrix"
                    ]
            },

            {
                name:
                    "wordcloud",

                path:
                    "visualization/terminal-wordcloud.js",

                dependencies:
                    [
                        "charts"
                    ]
            },

            {
                name:
                    "terminal-splash",

                path:
                    "visualization/terminal-splash.js",

                dependencies:
                    [
                        "cmatrix",
                        "zmatrix",
                        "wordcloud",
                        "events",
                        "settings"
                    ]
            },

            /*
            ------------------------------------------------------------------
            Help and Application
            ------------------------------------------------------------------
            */
            {
                name:
                    "help",

                path:
                    "terminal-help.js",

                dependencies:
                    [
                        "terminal-splash"
                    ]
            },

            {
                name:
                    "application",

                path:
                    "speciedex-terminal.js",

                dependencies:
                    [
                        "help",
                        "console",
                        "search",
                        "terminal-splash"
                    ]
            }
        ]);

    const DEFAULT_MANIFEST =
        Object.freeze({
            version:
                3,

            basePath:
                BASE_PATH,

            styles:
                [],

            modules:
                DEFAULT_MODULES
        });

    /*
    ==========================================================================
    Loader State
    ==========================================================================
    */

    const loadedURLs =
        new Set();

    const pendingURLs =
        new Map();

    const loadedModules =
        new Map();

    const failedModules =
        new Map();

    const disabledModules =
        new Map();

    const registeredModules =
        new Map();

    let state =
        "idle";

    let manifest =
        null;

    let loadPromise =
        null;

    let loadStartedAt =
        null;

    let loadCompletedAt =
        null;

    /*
    ==========================================================================
    Events
    ==========================================================================
    */

    function emit(
        name,
        detail = {}
    ) {
        document.dispatchEvent(
            new CustomEvent(
                name,
                {
                    detail
                }
            )
        );
    }

    /*
    ==========================================================================
    URL Handling
    ==========================================================================
    */

    function normalizeURL(
        path,
        basePath = BASE_PATH
    ) {
        const value =
            String(
                path ?? ""
            ).trim();

        if (!value) {
            throw new Error(
                "Terminal resource path cannot be empty."
            );
        }

        if (
            value.includes("\\") ||
            value.includes("\0")
        ) {
            throw new Error(
                `Invalid terminal resource path: ${value}`
            );
        }

        if (
            /^(?:https?:)?\/\//i.test(
                value
            ) ||
            value.startsWith("/")
        ) {
            return new URL(
                value,
                window.location.origin
            ).href;
        }

        return new URL(
            value,
            new URL(
                basePath,
                window.location.origin
            )
        ).href;
    }

    /*
    ==========================================================================
    Module Normalization
    ==========================================================================
    */

    function normalizeModule(
        entry,
        index = 0
    ) {
        if (
            typeof entry ===
            "string"
        ) {
            const path =
                entry.trim();

            if (!path) {
                throw new Error(
                    `Terminal module at index ${index} has an empty path.`
                );
            }

            return {
                name:
                    path
                        .replace(/^.*\//, "")
                        .replace(/\.js$/i, ""),

                path,

                enabled:
                    true,

                optional:
                    false,

                dependencies:
                    [],

                attributes:
                    {},

                metadata:
                    {}
            };
        }

        if (
            !entry ||
            typeof entry !==
            "object"
        ) {
            throw new TypeError(
                `Invalid terminal module at index ${index}.`
            );
        }

        const path =
            String(
                entry.path ||
                entry.src ||
                entry.url ||
                ""
            ).trim();

        if (!path) {
            throw new Error(
                `Terminal module at index ${index} has no path.`
            );
        }

        const name =
            String(
                entry.name ||
                path
                    .replace(/^.*\//, "")
                    .replace(/\.js$/i, "")
            ).trim();

        if (!name) {
            throw new Error(
                `Terminal module at index ${index} has no name.`
            );
        }

        return {
            name,

            path,

            enabled:
                entry.enabled !==
                false,

            optional:
                entry.optional ===
                true,

            dependencies:
                Array.isArray(
                    entry.dependencies
                )
                    ? [
                        ...new Set(
                            entry.dependencies
                                .map(
                                    dependency =>
                                        String(
                                            dependency
                                        ).trim()
                                )
                                .filter(
                                    Boolean
                                )
                        )
                    ]
                    : [],

            attributes:
                entry.attributes &&
                typeof entry.attributes ===
                "object"
                    ? {
                        ...entry.attributes
                    }
                    : {},

            metadata:
                entry.metadata &&
                typeof entry.metadata ===
                "object"
                    ? {
                        ...entry.metadata
                    }
                    : {}
        };
    }

    function cloneDefaultModules() {
        return DEFAULT_MODULES.map(
            (
                module,
                index
            ) =>
                normalizeModule(
                    module,
                    index
                )
        );
    }

    /*
    ==========================================================================
    Manifest Merging
    ==========================================================================
    */

    function mergeModuleDefinitions(
        defaults,
        overrides
    ) {
        const byPath =
            new Map();

        const byName =
            new Map();

        const orderedPaths =
            [];

        function insert(
            entry,
            source
        ) {
            const normalized =
                normalizeModule(
                    entry,
                    orderedPaths.length
                );

            const existingByPath =
                byPath.get(
                    normalized.path
                );

            const existingByName =
                byName.get(
                    normalized.name
                );

            const existing =
                existingByPath ||
                existingByName ||
                null;

            if (!existing) {
                const record = {
                    ...normalized,
                    source
                };

                byPath.set(
                    record.path,
                    record
                );

                byName.set(
                    record.name,
                    record
                );

                orderedPaths.push(
                    record.path
                );

                return;
            }

            const merged = {
                ...existing,
                ...normalized,

                /*
                --------------------------------------------------------------
                Keep the canonical built-in name when an override addresses a
                built-in module by path using a different repository name.
                This preserves built-in dependency references.
                --------------------------------------------------------------
                */
                name:
                    existing.name,

                dependencies:
                    normalized.dependencies.length
                        ? normalized.dependencies
                        : existing.dependencies,

                attributes: {
                    ...(existing.attributes || {}),
                    ...(normalized.attributes || {})
                },

                metadata: {
                    ...(existing.metadata || {}),
                    ...(normalized.metadata || {})
                },

                source:
                    source
            };

            byPath.set(
                merged.path,
                merged
            );

            byName.set(
                merged.name,
                merged
            );
        }

        for (
            const entry of
            defaults
        ) {
            insert(
                entry,
                "default"
            );
        }

        for (
            const entry of
            overrides
        ) {
            insert(
                entry,
                "manifest"
            );
        }

        /*
        ----------------------------------------------------------------------
        Repository manifests may use descriptive names such as
        "terminal-api" while the built-in graph uses canonical runtime names
        such as "api". Entries are merged by path and retain the canonical
        name, so dependency references must be canonicalized as well.
        ----------------------------------------------------------------------
        */

        const dependencyAliases =
            new Map();

        for (
            const entry of
            overrides
        ) {
            const normalized =
                normalizeModule(
                    entry,
                    dependencyAliases.size
                );

            const canonical =
                byPath.get(
                    normalized.path
                );

            if (canonical) {
                dependencyAliases.set(
                    normalized.name,
                    canonical.name
                );
            }
        }

        return orderedPaths.map(
            path => {
                const module =
                    byPath.get(
                        path
                    );

                return {
                    ...module,
                    dependencies:
                        module.dependencies.map(
                            dependency =>
                                dependencyAliases.get(
                                    dependency
                                ) || dependency
                        )
                };
            }
        );
    }

    function normalizeManifest(
        value
    ) {
        const source =
            value &&
            typeof value ===
            "object"
                ? value
                : {};

        const overrideModules =
            Array.isArray(
                source.modules
            )
                ? source.modules
                : [];

        const modules =
            mergeModuleDefinitions(
                cloneDefaultModules(),
                overrideModules
            );

        const styles =
            Array.isArray(
                source.styles
            )
                ? source.styles
                : [];

        return {
            version:
                Number(
                    source.version
                ) || 3,

            basePath:
                String(
                    source.basePath ||
                    BASE_PATH
                ),

            styles,

            modules
        };
    }

    async function fetchManifest(
        url = MANIFEST_URL
    ) {
        const normalizedURL =
            normalizeURL(
                url,
                "/"
            );

        try {
            const response =
                await fetch(
                    normalizedURL,
                    {
                        method:
                            "GET",

                        cache:
                            "no-store",

                        credentials:
                            "same-origin",

                        headers: {
                            Accept:
                                "application/json"
                        }
                    }
                );

            if (!response.ok) {
                throw new Error(
                    `Terminal manifest request failed with HTTP ${response.status}.`
                );
            }

            const data =
                await response.json();

            const normalized =
                normalizeManifest(
                    data
                );

            emit(
                "speciedex:terminal-manifest-loaded",
                {
                    url:
                        normalizedURL,

                    manifest:
                        normalized,

                    fallback:
                        false
                }
            );

            return normalized;
        } catch (error) {
            console.warn(
                "[SpeciedexTerminalLoader] " +
                "Unable to load manifest.json; using the complete built-in manifest.",
                error
            );

            const fallback =
                normalizeManifest(
                    DEFAULT_MANIFEST
                );

            emit(
                "speciedex:terminal-manifest-loaded",
                {
                    url:
                        normalizedURL,

                    manifest:
                        fallback,

                    fallback:
                        true,

                    error
                }
            );

            return fallback;
        }
    }

    /*
    ==========================================================================
    Existing Resource Detection
    ==========================================================================
    */

    function findScript(
        url
    ) {
        return (
            Array.from(
                document.scripts
            ).find(
                script =>
                    script.src ===
                    url
            ) ||
            null
        );
    }

    function findStyle(
        url
    ) {
        return (
            Array.from(
                document.querySelectorAll(
                    'link[rel="stylesheet"]'
                )
            ).find(
                link =>
                    link.href ===
                    url
            ) ||
            null
        );
    }

    /*
    ==========================================================================
    Script Loading
    ==========================================================================
    */

    function loadScript(
        url,
        attributes = {}
    ) {
        const normalized =
            normalizeURL(
                url
            );

        if (
            loadedURLs.has(
                normalized
            )
        ) {
            return Promise.resolve(
                normalized
            );
        }

        if (
            pendingURLs.has(
                normalized
            )
        ) {
            return pendingURLs.get(
                normalized
            );
        }

        const promise =
            new Promise(
                (
                    resolve,
                    reject
                ) => {
                    const existing =
                        findScript(
                            normalized
                        );

                    const script =
                        existing ||
                        document.createElement(
                            "script"
                        );

                    let settled =
                        false;

                    function cleanup() {
                        script.removeEventListener(
                            "load",
                            handleLoad
                        );

                        script.removeEventListener(
                            "error",
                            handleError
                        );

                        pendingURLs.delete(
                            normalized
                        );
                    }

                    function succeed() {
                        if (settled) {
                            return;
                        }

                        settled =
                            true;

                        script.dataset.speciedexTerminalLoaded =
                            "true";

                        loadedURLs.add(
                            normalized
                        );

                        cleanup();

                        resolve(
                            normalized
                        );
                    }

                    function fail(
                        error
                    ) {
                        if (settled) {
                            return;
                        }

                        settled =
                            true;

                        cleanup();

                        reject(
                            error
                        );
                    }

                    function handleLoad() {
                        succeed();
                    }

                    function handleError() {
                        fail(
                            new Error(
                                `Unable to load terminal script: ${normalized}`
                            )
                        );
                    }

                    if (
                        existing &&
                        existing.dataset.speciedexTerminalLoaded ===
                            "true"
                    ) {
                        succeed();
                        return;
                    }

                    if (
                        existing &&
                        existing.dataset.speciedexTerminalResource ===
                            "script" &&
                        existing.readyState ===
                            "complete"
                    ) {
                        succeed();
                        return;
                    }

                    if (!existing) {
                        script.src =
                            normalized;

                        script.async =
                            false;

                        script.defer =
                            false;

                        script.dataset.speciedexTerminalResource =
                            "script";

                        for (
                            const [
                                name,
                                value
                            ] of Object.entries(
                                attributes
                            )
                        ) {
                            if (
                                value !==
                                    undefined &&
                                value !==
                                    null
                            ) {
                                script.setAttribute(
                                    name,
                                    String(
                                        value
                                    )
                                );
                            }
                        }
                    }

                    script.addEventListener(
                        "load",
                        handleLoad,
                        {
                            once:
                                true
                        }
                    );

                    script.addEventListener(
                        "error",
                        handleError,
                        {
                            once:
                                true
                        }
                    );

                    if (!existing) {
                        document.head.appendChild(
                            script
                        );
                    }
                }
            );

        pendingURLs.set(
            normalized,
            promise
        );

        return promise;
    }

    /*
    ==========================================================================
    Stylesheet Loading
    ==========================================================================
    */

    function loadStyle(
        url,
        attributes = {}
    ) {
        const normalized =
            normalizeURL(
                url
            );

        if (
            loadedURLs.has(
                normalized
            )
        ) {
            return Promise.resolve(
                normalized
            );
        }

        if (
            pendingURLs.has(
                normalized
            )
        ) {
            return pendingURLs.get(
                normalized
            );
        }

        const promise =
            new Promise(
                (
                    resolve,
                    reject
                ) => {
                    const existing =
                        findStyle(
                            normalized
                        );

                    if (existing) {
                        loadedURLs.add(
                            normalized
                        );

                        resolve(
                            normalized
                        );

                        return;
                    }

                    const link =
                        document.createElement(
                            "link"
                        );

                    link.rel =
                        "stylesheet";

                    link.href =
                        normalized;

                    link.dataset.speciedexTerminalResource =
                        "style";

                    for (
                        const [
                            name,
                            value
                        ] of Object.entries(
                            attributes
                        )
                    ) {
                        if (
                            value !==
                                undefined &&
                            value !==
                                null
                        ) {
                            link.setAttribute(
                                name,
                                String(
                                    value
                                )
                            );
                        }
                    }

                    function cleanup() {
                        link.removeEventListener(
                            "load",
                            handleLoad
                        );

                        link.removeEventListener(
                            "error",
                            handleError
                        );

                        pendingURLs.delete(
                            normalized
                        );
                    }

                    function handleLoad() {
                        loadedURLs.add(
                            normalized
                        );

                        cleanup();

                        resolve(
                            normalized
                        );
                    }

                    function handleError() {
                        cleanup();

                        reject(
                            new Error(
                                `Unable to load terminal stylesheet: ${normalized}`
                            )
                        );
                    }

                    link.addEventListener(
                        "load",
                        handleLoad,
                        {
                            once:
                                true
                        }
                    );

                    link.addEventListener(
                        "error",
                        handleError,
                        {
                            once:
                                true
                        }
                    );

                    document.head.appendChild(
                        link
                    );
                }
            );

        pendingURLs.set(
            normalized,
            promise
        );

        return promise;
    }

    /*
    ==========================================================================
    Dependency Validation and Ordering
    ==========================================================================
    */

    function validateModules(
        entries
    ) {
        const modules =
            entries.map(
                normalizeModule
            );

        const names =
            new Set();

        const paths =
            new Set();

        for (
            const module of
            modules
        ) {
            if (
                names.has(
                    module.name
                )
            ) {
                throw new Error(
                    `Duplicate terminal module name: ${module.name}`
                );
            }

            if (
                paths.has(
                    module.path
                )
            ) {
                throw new Error(
                    `Duplicate terminal module path: ${module.path}`
                );
            }

            names.add(
                module.name
            );

            paths.add(
                module.path
            );
        }

        return modules;
    }

    function orderModules(
        entries
    ) {
        const modules =
            validateModules(
                entries
            );

        const enabledModules =
            modules.filter(
                module =>
                    module.enabled
            );

        disabledModules.clear();

        for (
            const module of
            modules
        ) {
            if (!module.enabled) {
                disabledModules.set(
                    module.name,
                    module
                );
            }
        }

        const byName =
            new Map(
                enabledModules.map(
                    module => [
                        module.name,
                        module
                    ]
                )
            );

        const ordered =
            [];

        const permanent =
            new Set();

        const temporary =
            new Set();

        const stack =
            [];

        function visit(
            module
        ) {
            if (
                permanent.has(
                    module.name
                )
            ) {
                return;
            }

            if (
                temporary.has(
                    module.name
                )
            ) {
                const cycleStart =
                    stack.indexOf(
                        module.name
                    );

                const cycle =
                    [
                        ...stack.slice(
                            cycleStart
                        ),
                        module.name
                    ];

                throw new Error(
                    `Circular terminal module dependency: ${cycle.join(" -> ")}`
                );
            }

            temporary.add(
                module.name
            );

            stack.push(
                module.name
            );

            for (
                const dependencyName of
                module.dependencies
            ) {
                const dependency =
                    byName.get(
                        dependencyName
                    );

                if (!dependency) {
                    throw new Error(
                        `Terminal module "${module.name}" requires missing or disabled dependency "${dependencyName}".`
                    );
                }

                visit(
                    dependency
                );
            }

            stack.pop();

            temporary.delete(
                module.name
            );

            permanent.add(
                module.name
            );

            ordered.push(
                module
            );
        }

        for (
            const module of
            enabledModules
        ) {
            visit(
                module
            );
        }

        return ordered;
    }

    /*
    ==========================================================================
    Style Loading
    ==========================================================================
    */

    async function loadStyles(
        entries,
        basePath
    ) {
        const loaded =
            [];

        for (
            let index = 0;
            index < entries.length;
            index += 1
        ) {
            const entry =
                entries[
                    index
                ];

            const definition =
                typeof entry ===
                "string"
                    ? {
                        path:
                            entry,

                        optional:
                            false,

                        attributes:
                            {}
                    }
                    : {
                        path:
                            entry?.path ||
                            entry?.href ||
                            entry?.url,

                        optional:
                            entry?.optional ===
                            true,

                        attributes:
                            entry?.attributes &&
                            typeof entry.attributes ===
                                "object"
                                ? {
                                    ...entry.attributes
                                }
                                : {}
                    };

            if (!definition.path) {
                throw new Error(
                    `Terminal stylesheet at index ${index} has no path.`
                );
            }

            const url =
                normalizeURL(
                    definition.path,
                    basePath
                );

            try {
                await loadStyle(
                    url,
                    definition.attributes
                );

                loaded.push(
                    url
                );

                emit(
                    "speciedex:terminal-style-loaded",
                    {
                        path:
                            definition.path,

                        url
                    }
                );
            } catch (error) {
                emit(
                    "speciedex:terminal-style-error",
                    {
                        path:
                            definition.path,

                        url,

                        optional:
                            definition.optional,

                        error
                    }
                );

                if (!definition.optional) {
                    throw error;
                }

                console.warn(
                    "[SpeciedexTerminalLoader] Optional style failed:",
                    error
                );
            }
        }

        return loaded;
    }

    /*
    ==========================================================================
    Module Loading
    ==========================================================================
    */

    async function loadModules(
        entries,
        basePath
    ) {
        const ordered =
            orderModules(
                entries
            );

        for (
            let index = 0;
            index < ordered.length;
            index += 1
        ) {
            const module =
                ordered[
                    index
                ];

            const url =
                normalizeURL(
                    module.path,
                    basePath
                );

            if (
                loadedModules.has(
                    module.name
                )
            ) {
                continue;
            }

            emit(
                "speciedex:terminal-module-loading",
                {
                    module:
                        module.name,

                    path:
                        module.path,

                    url,

                    index,

                    total:
                        ordered.length
                }
            );

            try {
                await loadScript(
                    url,
                    module.attributes
                );

                const record = {
                    ...module,

                    url,

                    index,

                    loadedAt:
                        new Date().toISOString()
                };

                loadedModules.set(
                    module.name,
                    record
                );

                failedModules.delete(
                    module.name
                );

                emit(
                    "speciedex:terminal-module-loaded",
                    {
                        module:
                            module.name,

                        path:
                            module.path,

                        url,

                        index,

                        total:
                            ordered.length
                    }
                );
            } catch (error) {
                const failure = {
                    ...module,

                    url,

                    index,

                    failedAt:
                        new Date().toISOString(),

                    error
                };

                failedModules.set(
                    module.name,
                    failure
                );

                emit(
                    "speciedex:terminal-module-error",
                    failure
                );

                if (!module.optional) {
                    throw error;
                }

                console.warn(
                    `[SpeciedexTerminalLoader] Optional module "${module.name}" failed:`,
                    error
                );
            }
        }

        return [
            ...loadedModules.values()
        ];
    }

    /*
    ==========================================================================
    Loader Lifecycle
    ==========================================================================
    */

    async function performLoad(
        options = {}
    ) {
        state =
            "loading";

        loadStartedAt =
            new Date().toISOString();

        loadCompletedAt =
            null;

        emit(
            "speciedex:terminal-loader-start",
            {
                options,

                startedAt:
                    loadStartedAt
            }
        );

        const sourceManifest =
            options.manifest ||
            await fetchManifest(
                options.manifestURL ||
                MANIFEST_URL
            );

        manifest =
            normalizeManifest(
                sourceManifest
            );

        const basePath =
            options.basePath ||
            manifest.basePath ||
            BASE_PATH;

        const styles =
            Array.isArray(
                options.styles
            )
                ? options.styles
                : manifest.styles;

        const modules =
            Array.isArray(
                options.modules
            )
                ? mergeModuleDefinitions(
                    manifest.modules,
                    options.modules
                )
                : manifest.modules;

        await loadStyles(
            styles,
            basePath
        );

        await loadModules(
            modules,
            basePath
        );

        state =
            failedModules.size
                ? "ready-with-errors"
                : "ready";

        loadCompletedAt =
            new Date().toISOString();

        const result =
            snapshot();

        emit(
            "speciedex:terminal-loader-ready",
            result
        );

        return result;
    }

    function load(
        options = {}
    ) {
        if (
            (
                state ===
                    "ready" ||
                state ===
                    "ready-with-errors"
            ) &&
            !options.reload
        ) {
            return Promise.resolve(
                snapshot()
            );
        }

        if (
            loadPromise &&
            !options.reload
        ) {
            return loadPromise;
        }

        if (
            options.reload
        ) {
            state =
                "idle";

            loadPromise =
                null;

            loadedModules.clear();

            failedModules.clear();

            disabledModules.clear();

            manifest =
                null;

            loadStartedAt =
                null;

            loadCompletedAt =
                null;
        }

        loadPromise =
            performLoad(
                options
            )
                .catch(
                    error => {
                        state =
                            "error";

                        loadCompletedAt =
                            new Date().toISOString();

                        emit(
                            "speciedex:terminal-loader-error",
                            {
                                error,

                                failedModules:
                                    [
                                        ...failedModules.values()
                                    ],

                                completedAt:
                                    loadCompletedAt
                            }
                        );

                        loadPromise =
                            null;

                        throw error;
                    }
                );

        return loadPromise;
    }

    /*
    ==========================================================================
    Runtime Registration
    ==========================================================================
    */

    function registerModule(
        definition
    ) {
        const normalized =
            normalizeModule(
                definition,
                registeredModules.size
            );

        registeredModules.set(
            normalized.name,
            normalized
        );

        if (!manifest) {
            manifest =
                normalizeManifest(
                    DEFAULT_MANIFEST
                );
        }

        const existingIndex =
            manifest.modules.findIndex(
                module =>
                    module.name ===
                        normalized.name ||
                    module.path ===
                        normalized.path
            );

        if (
            existingIndex >=
            0
        ) {
            const existing =
                manifest.modules[
                    existingIndex
                ];

            manifest.modules[
                existingIndex
            ] = {
                ...existing,
                ...normalized,

                name:
                    existing.name,

                dependencies:
                    normalized.dependencies.length
                        ? normalized.dependencies
                        : existing.dependencies,

                attributes: {
                    ...(existing.attributes || {}),
                    ...(normalized.attributes || {})
                },

                metadata: {
                    ...(existing.metadata || {}),
                    ...(normalized.metadata || {})
                }
            };
        } else {
            manifest.modules.push(
                normalized
            );
        }

        emit(
            "speciedex:terminal-module-registered",
            {
                module:
                    normalized
            }
        );

        return normalized;
    }

    function unregisterModule(
        name
    ) {
        const normalizedName =
            String(
                name ?? ""
            ).trim();

        if (!normalizedName) {
            return false;
        }

        const removed =
            registeredModules.delete(
                normalizedName
            );

        if (manifest) {
            manifest.modules =
                manifest.modules.filter(
                    module =>
                        module.name !==
                        normalizedName
                );
        }

        emit(
            "speciedex:terminal-module-unregistered",
            {
                module:
                    normalizedName,

                removed
            }
        );

        return removed;
    }

    /*
    ==========================================================================
    Worker Creation
    ==========================================================================
    */

    function createWorker(
        name,
        options = {}
    ) {
        const workerName =
            String(
                name ?? ""
            ).trim();

        const url =
            WORKERS[
                workerName
            ];

        if (!url) {
            throw new Error(
                `Unknown SpeciedexTerminal worker: ${workerName}`
            );
        }

        const worker =
            new Worker(
                url,
                {
                    name:
                        `speciedex-terminal-${workerName}`,

                    ...options
                }
            );

        emit(
            "speciedex:terminal-worker-created",
            {
                name:
                    workerName,

                url,

                worker
            }
        );

        return worker;
    }

    /*
    ==========================================================================
    Diagnostics
    ==========================================================================
    */

    function snapshot() {
        return {
            state,

            version:
                VERSION,

            manifest,

            startedAt:
                loadStartedAt,

            completedAt:
                loadCompletedAt,

            loadedURLs:
                [
                    ...loadedURLs
                ],

            pendingURLs:
                [
                    ...pendingURLs.keys()
                ],

            loadedModules:
                [
                    ...loadedModules.values()
                ],

            failedModules:
                [
                    ...failedModules.values()
                ],

            disabledModules:
                [
                    ...disabledModules.values()
                ],

            registeredModules:
                [
                    ...registeredModules.values()
                ],

            workers: {
                ...WORKERS
            }
        };
    }

    function graph() {
        const modules =
            manifest?.modules ||
            DEFAULT_MODULES;

        return modules.map(module => ({
            name:
                module.name,
            path:
                module.path,
            dependencies:
                Array.isArray(module.dependencies)
                    ? module.dependencies.slice()
                    : [],
            optional:
                module.optional === true,
            disabled:
                module.disabled === true,
            loaded:
                loadedModules.has(module.name),
            failed:
                failedModules.has(module.name)
        }));
    }

    function status() {
        const current =
            snapshot();

        return {
            state:
                current.state,

            version:
                current.version,

            manifestVersion:
                current.manifest?.version ||
                null,

            loaded:
                current.loadedModules.length,

            failed:
                current.failedModules.length,

            disabled:
                current.disabledModules.length,

            pending:
                current.pendingURLs.length,

            startedAt:
                current.startedAt,

            completedAt:
                current.completedAt
        };
    }

    /*
    ==========================================================================
    Public API
    ==========================================================================
    */

    const api =
        Object.freeze({
            VERSION,
            BASE_PATH,
            MANIFEST_URL,
            DEFAULT_MANIFEST,
            DEFAULT_MODULES,
            WORKERS,

            load,
            loadScript,
            loadStyle,
            loadStyles,
            loadModules,

            fetchManifest,
            normalizeManifest,
            normalizeModule,
            normalizeURL,
            mergeModuleDefinitions,
            validateModules,
            orderModules,

            registerModule,
            unregisterModule,

            createWorker,

            snapshot,
            status,
            graph,

            get state() {
                return state;
            },

            get manifest() {
                return manifest;
            },

            get loadedModules() {
                return [
                    ...loadedModules.values()
                ];
            },

            get failedModules() {
                return [
                    ...failedModules.values()
                ];
            },

            get disabledModules() {
                return [
                    ...disabledModules.values()
                ];
            },

            get registeredModules() {
                return [
                    ...registeredModules.values()
                ];
            },

            get ready() {
                return loadPromise;
            }
        });

    window[GLOBAL_NAME] =
        api;

    emit(
        "speciedex:terminal-loader-available",
        {
            loader:
                api,

            version:
                VERSION
        }
    );
})(window, document);
