/*
========================================================================
Speciedex.org
Terminal Live Species Splash
========================================================================

Coordinates terminal-cmatrix.js, terminal-zmatrix.js, and terminal-wordcloud.js
to create the live species visualization mounted above the interactive terminal
console.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/
(function (window, document) {
    "use strict";

    const MODULE_NAME = "Splash";

    const DOCUMENT_EVENTS = [
        "speciedex:species-detected",
        "speciedex:scan-record",
        "speciedex:provider-record",
        "speciedex:terminal-search-results",
        "speciedex:terminal-species-results",
        "speciedex:archive-record",
        "speciedex:api-record",
        "speciedex:import-record"
    ];

    function first(record, keys, fallback = "") {
        for (const key of keys) {
            const value = record?.[key];

            if (
                value !== undefined &&
                value !== null &&
                value !== ""
            ) {
                return value;
            }
        }

        return fallback;
    }

    function normalize(record) {
        return {
            scientificName:
                String(
                    first(
                        record,
                        [
                            "scientific_name",
                            "scientificName",
                            "canonical_name",
                            "canonicalName",
                            "accepted_name",
                            "acceptedName",
                            "name"
                        ],
                        "Unknown taxon"
                    )
                ).trim(),

            commonName:
                String(
                    first(
                        record,
                        [
                            "common_name",
                            "commonName",
                            "vernacular_name",
                            "vernacularName",
                            "preferred_common_name",
                            "preferredCommonName"
                        ],
                        "No common name"
                    )
                ).trim(),

            speciedexId:
                String(
                    first(
                        record,
                        [
                            "speciedex_id",
                            "speciedexId",
                            "speciedex_key",
                            "speciedexKey",
                            "canonical_id",
                            "canonicalId",
                            "id",
                            "key"
                        ],
                        "pending"
                    )
                ).trim(),

            rank:
                String(
                    first(
                        record,
                        [
                            "rank",
                            "taxon_rank",
                            "taxonRank"
                        ]
                    )
                ).trim(),

            provider:
                String(
                    first(
                        record,
                        [
                            "provider",
                            "source",
                            "provider_id",
                            "providerId"
                        ]
                    )
                ).trim(),

            raw:
                record,

            detectedAt:
                new Date().toISOString()
        };
    }

    function collect(payload) {
        if (!payload) {
            return [];
        }

        if (Array.isArray(payload)) {
            return payload;
        }

        const candidates = [
            payload.records,
            payload.results,
            payload.items,
            payload.species,
            payload.data,
            payload.record,
            payload.result
        ];

        for (const candidate of candidates) {
            if (Array.isArray(candidate)) {
                return candidate;
            }

            if (
                candidate &&
                typeof candidate === "object"
            ) {
                return [candidate];
            }
        }

        return (
            typeof payload === "object"
                ? [payload]
                : []
        );
    }

    function key(record) {
        return [
            record.speciedexId,
            record.scientificName.toLowerCase(),
            record.commonName.toLowerCase()
        ].join("|");
    }

    class TerminalSplashController {
        constructor(context, options = {}) {
            this.context = context;
            this.root = context.root;

            this.options = {
                capacity:
                    Number(options.capacity) || 128,
                visible:
                    Number(options.visible) || 12,
                interval:
                    Number(options.interval) || 140,
                batch:
                    Number(options.batch) || 1,
                preferZMatrix:
                    options.preferZMatrix !== false,
                ...options
            };

            this.records = [];
            this.seen = new Set();
            this.cursor = 0;
            this.timer = 0;
            this.destroyed = false;
            this.unsubscribers = [];

            this.elements =
                this.captureElements();

            this.matrixController = null;
            this.wordCloudController = null;

            this.mountVisualizations();
            this.bindEvents();
            this.start();
        }

        captureElements() {
            const host =
                this.root.querySelector(
                    "[data-terminal-splash]"
                );

            if (!host) {
                throw new Error(
                    "terminal.html must provide [data-terminal-splash]."
                );
            }

            const list =
                host.querySelector(
                    "[data-terminal-splash-list]"
                );

            const canvas =
                host.querySelector(
                    "[data-terminal-splash-canvas]"
                );

            const wordcloud =
                host.querySelector(
                    "[data-terminal-splash-wordcloud]"
                );

            if (!list || !canvas || !wordcloud) {
                throw new Error(
                    "Terminal splash markup is incomplete."
                );
            }

            return {
                host,
                list,
                canvas,
                wordcloud,
                count:
                    host.querySelector(
                        "[data-terminal-splash-count]"
                    ),
                status:
                    host.querySelector(
                        "[data-terminal-splash-status]"
                    ),
                source:
                    host.querySelector(
                        "[data-terminal-splash-source]"
                    )
            };
        }

        mountVisualizations() {
            const visualizations =
                this.context.visualizations;

            const zmatrix =
                visualizations?.get?.("zmatrix") ||
                window.SpeciedexTerminalZMatrix;

            const cmatrix =
                visualizations?.get?.("cmatrix") ||
                window.SpeciedexTerminalCMatrix;

            const wordcloud =
                visualizations?.get?.("wordcloud") ||
                window.SpeciedexTerminalWordCloud;

            if (
                this.options.preferZMatrix &&
                zmatrix?.mount
            ) {
                this.matrixController =
                    zmatrix.mount(
                        this.elements.canvas,
                        {
                            baseSpeed: 0.82,
                            pulseSpeed: 0.022,
                            opacity: 0.30
                        }
                    );
            } else if (cmatrix?.mount) {
                this.matrixController =
                    cmatrix.mount(
                        this.elements.canvas,
                        {
                            speed: 0.82,
                            density: 0.86,
                            trail: 0.10,
                            opacity: 0.24
                        }
                    );
            }

            if (wordcloud?.mount) {
                this.wordCloudController =
                    wordcloud.mount(
                        this.elements.wordcloud,
                        {
                            source:
                                () =>
                                    this.records.flatMap(
                                        record => [
                                            record.scientificName,
                                            record.commonName,
                                            record.rank,
                                            record.provider
                                        ].filter(Boolean)
                                    ),
                            maxWords: 28,
                            refresh: 720,
                            minFont: 10,
                            maxFont: 24,
                            opacity: 0.24
                        }
                    );
            }
        }

        bindEvents() {
            for (const eventName of DOCUMENT_EVENTS) {
                const handler =
                    event =>
                        this.ingest(
                            event.detail,
                            eventName
                        );

                document.addEventListener(
                    eventName,
                    handler,
                    {
                        signal:
                            this.context.signal
                    }
                );
            }

            const eventBus =
                this.context.events;

            if (eventBus?.on) {
                for (const eventName of [
                    "species:detected",
                    "scan:record",
                    "provider:record",
                    "search:results",
                    "archive:record",
                    "api:record",
                    "import:record"
                ]) {
                    this.unsubscribers.push(
                        eventBus.on(
                            eventName,
                            event =>
                                this.ingest(
                                    event.detail,
                                    eventName
                                )
                        )
                    );
                }
            }

            this.root.addEventListener(
                "speciedex:terminal-command-complete",
                event => {
                    this.ingest(
                        event.detail?.result,
                        "terminal-command"
                    );
                },
                {
                    signal:
                        this.context.signal
                }
            );
        }

        ingest(payload, source = "runtime") {
            const incoming =
                collect(payload);

            let added = 0;

            for (const raw of incoming) {
                if (
                    !raw ||
                    typeof raw !== "object"
                ) {
                    continue;
                }

                const record =
                    normalize(raw);

                const recordKey =
                    key(record);

                if (
                    this.seen.has(
                        recordKey
                    )
                ) {
                    continue;
                }

                this.seen.add(
                    recordKey
                );

                this.records.push(
                    record
                );

                this.matrixController?.
                    inject?.(raw);

                added += 1;

                if (
                    this.records.length >
                    this.options.capacity
                ) {
                    const removed =
                        this.records.shift();

                    this.seen.delete(
                        key(removed)
                    );
                }
            }

            if (!added) {
                return;
            }

            if (this.elements.count) {
                this.elements.count.textContent =
                    String(
                        this.records.length
                    );
            }

            if (this.elements.status) {
                this.elements.status.textContent =
                    `Streaming ${added} newly observed ` +
                    `record${added === 1 ? "" : "s"}`;
            }

            if (this.elements.source) {
                this.elements.source.textContent =
                    `Source: ${source}`;
            }

            this.wordCloudController?.
                refresh?.();

            this.render();
        }

        start() {
            this.render();

            const reducedMotion =
                window.matchMedia?.
                    (
                        "(prefers-reduced-motion: reduce)"
                    ).matches;

            if (reducedMotion) {
                return;
            }

            this.timer =
                window.setInterval(
                    () => {
                        if (!this.records.length) {
                            return;
                        }

                        this.cursor =
                            (
                                this.cursor +
                                this.options.batch
                            ) %
                            this.records.length;

                        this.render();
                    },
                    this.options.interval
                );
        }

        render() {
            const list =
                this.elements.list;

            if (!this.records.length) {
                list.innerHTML = `
                    <div class="terminal-splash-empty">
                        Awaiting live species records from providers,
                        scans, search, imports, and archive reconciliation.
                    </div>
                `;

                return;
            }

            const fragment =
                document.createDocumentFragment();

            const visible =
                Math.min(
                    this.options.visible,
                    this.records.length
                );

            for (
                let offset = 0;
                offset < visible;
                offset += 1
            ) {
                const index =
                    (
                        this.cursor +
                        offset
                    ) %
                    this.records.length;

                const record =
                    this.records[index];

                const row =
                    document.createElement(
                        "article"
                    );

                row.className =
                    "terminal-splash-row";

                row.dataset.speciedexId =
                    record.speciedexId;

                row.style.setProperty(
                    "--terminal-splash-row-index",
                    String(offset)
                );

                const scientific =
                    document.createElement(
                        "span"
                    );

                scientific.className =
                    "terminal-splash-scientific";

                scientific.textContent =
                    record.scientificName;

                const common =
                    document.createElement(
                        "span"
                    );

                common.className =
                    "terminal-splash-common";

                common.textContent =
                    record.commonName;

                const identifier =
                    document.createElement(
                        "code"
                    );

                identifier.className =
                    "terminal-splash-id";

                identifier.textContent =
                    record.speciedexId;

                row.append(
                    scientific,
                    common,
                    identifier
                );

                fragment.appendChild(
                    row
                );
            }

            list.replaceChildren(
                fragment
            );
        }

        clear() {
            this.records = [];
            this.seen.clear();
            this.cursor = 0;

            if (this.elements.count) {
                this.elements.count.textContent =
                    "0";
            }

            if (this.elements.status) {
                this.elements.status.textContent =
                    "Species stream cleared";
            }

            this.render();
        }

        show() {
            this.elements.host.hidden =
                false;
        }

        hide() {
            this.elements.host.hidden =
                true;
        }

        destroy() {
            this.destroyed = true;

            if (this.timer) {
                window.clearInterval(
                    this.timer
                );
            }

            this.matrixController?.
                destroy?.();

            this.wordCloudController?.
                destroy?.();

            for (const unsubscribe of this.unsubscribers) {
                try {
                    unsubscribe();
                } catch (error) {}
            }

            this.unsubscribers = [];
        }
    }

    function initialize(context) {
        const controller =
            new TerminalSplashController(
                context,
                {
                    capacity:
                        context.root?.
                            dataset.
                            terminalSplashCapacity,

                    visible:
                        context.root?.
                            dataset.
                            terminalSplashVisible,

                    interval:
                        context.root?.
                            dataset.
                            terminalSplashInterval,

                    batch:
                        context.root?.
                            dataset.
                            terminalSplashBatch
                }
            );

        context.terminalSplash =
            controller;

        context.registerVisualization?.(
            "splash",
            controller
        );

        context.registerService?.(
            "terminal-splash",
            controller
        );

        return controller;
    }

    const commands = [
        {
            name: "splash",
            category: "visualization",
            description:
                "Inspect or control the live species splash.",
            usage:
                "splash [status|show|hide|clear]",
            handler: ({
                args,
                context,
                writeJSON,
                write
            }) => {
                const controller =
                    context.terminalSplash;

                if (!controller) {
                    throw new Error(
                        "Terminal splash is unavailable."
                    );
                }

                const action =
                    args[0] || "status";

                if (action === "show") {
                    controller.show();
                    return write(
                        "Terminal splash shown.",
                        "success"
                    );
                }

                if (action === "hide") {
                    controller.hide();
                    return write(
                        "Terminal splash hidden.",
                        "success"
                    );
                }

                if (action === "clear") {
                    controller.clear();
                    return write(
                        "Terminal splash cleared.",
                        "success"
                    );
                }

                return writeJSON({
                    records:
                        controller.records.length,
                    visible:
                        controller.options.visible,
                    capacity:
                        controller.options.capacity,
                    interval:
                        controller.options.interval,
                    hidden:
                        controller.elements.host.hidden,
                    matrix:
                        controller.matrixController?.
                            constructor?.name || null,
                    wordcloud:
                        Boolean(
                            controller.wordCloudController
                        )
                });
            }
        }
    ];

    const api = Object.freeze({
        name: MODULE_NAME,
        TerminalSplashController,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalSplash =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

    document.dispatchEvent(
        new CustomEvent(
            "speciedex:terminal-module-available",
            {
                detail: {
                    name: MODULE_NAME,
                    module: api
                }
            }
        )
    );
})(window, document);
