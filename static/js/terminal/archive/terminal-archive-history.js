/*
========================================================================
Speciedex.org
Terminal ArchiveHistory Module
========================================================================

Archive publication-history service for SpeciedexTerminal.

Provides:

    • Validated archive-history API requests
    • Query, date-range, sorting, offset, and limit controls
    • Response normalization
    • Service registration and lifecycle events
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "ArchiveHistory";
    const VERSION = "2.0.0";

    const DEFAULT_LIMIT = 50;
    const MIN_LIMIT = 1;
    const MAX_LIMIT = 1000;
    const SERVICE_NAME = "archive-history";

    function dispatch(target, name, detail, options = {}) {
        if (
            !target ||
            typeof target.dispatchEvent !== "function"
        ) {
            return false;
        }

        try {
            return target.dispatchEvent(
                new CustomEvent(
                    name,
                    {
                        bubbles:
                            options.bubbles === true,
                        cancelable:
                            options.cancelable === true,
                        detail
                    }
                )
            );
        } catch (_error) {
            return false;
        }
    }

    function clampInteger(value, fallback, minimum, maximum) {
        const parsed = Number.parseInt(value, 10);

        if (!Number.isFinite(parsed)) {
            return fallback;
        }

        return Math.min(
            maximum,
            Math.max(minimum, parsed)
        );
    }

    function normalizeText(value) {
        return String(value ?? "")
            .trim();
    }

    function normalizeDate(value) {
        const text =
            normalizeText(value);

        if (!text) {
            return "";
        }

        const timestamp =
            Date.parse(text);

        if (!Number.isFinite(timestamp)) {
            throw new TypeError(
                `Invalid date value: ${value}`
            );
        }

        return new Date(timestamp).toISOString();
    }

    function normalizeSort(value) {
        const normalized =
            normalizeText(value || "published_at")
                .toLowerCase();

        const allowed = new Set([
            "published_at",
            "created_at",
            "updated_at",
            "version",
            "records",
            "provider",
            "status"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported archive-history sort field: ${value}`
            );
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized =
            normalizeText(value || "desc")
                .toLowerCase();

        if (
            normalized !== "asc" &&
            normalized !== "desc"
        ) {
            throw new TypeError(
                `Unsupported sort direction: ${value}`
            );
        }

        return normalized;
    }

    function normalizeParameters(parameters = {}) {
        const source =
            parameters &&
            typeof parameters === "object"
                ? parameters
                : {};

        const normalized = {
            q:
                normalizeText(
                    source.q ??
                    source.query ??
                    ""
                ),
            limit:
                clampInteger(
                    source.limit,
                    DEFAULT_LIMIT,
                    MIN_LIMIT,
                    MAX_LIMIT
                ),
            offset:
                clampInteger(
                    source.offset,
                    0,
                    0,
                    Number.MAX_SAFE_INTEGER
                ),
            sort:
                normalizeSort(
                    source.sort
                ),
            direction:
                normalizeDirection(
                    source.direction ??
                    source.order
                )
        };

        const from =
            source.from ??
            source.since ??
            source.start;

        const to =
            source.to ??
            source.until ??
            source.end;

        if (
            from !== undefined &&
            from !== null &&
            from !== ""
        ) {
            normalized.from =
                normalizeDate(from);
        }

        if (
            to !== undefined &&
            to !== null &&
            to !== ""
        ) {
            normalized.to =
                normalizeDate(to);
        }

        if (
            normalized.from &&
            normalized.to &&
            Date.parse(normalized.from) >
            Date.parse(normalized.to)
        ) {
            throw new RangeError(
                "Archive-history start date must not be later than the end date."
            );
        }

        if (
            source.provider !== undefined &&
            source.provider !== null &&
            source.provider !== ""
        ) {
            normalized.provider =
                normalizeText(
                    source.provider
                );
        }

        if (
            source.status !== undefined &&
            source.status !== null &&
            source.status !== ""
        ) {
            normalized.status =
                normalizeText(
                    source.status
                );
        }

        return normalized;
    }

    function normalizeRecord(record, index = 0) {
        if (
            !record ||
            typeof record !== "object"
        ) {
            return {
                index,
                value: record
            };
        }

        return {
            ...record,
            index:
                record.index ??
                index
        };
    }

    function normalizeResponse(payload) {
        if (Array.isArray(payload)) {
            return {
                records:
                    payload.map(
                        normalizeRecord
                    ),
                total:
                    payload.length,
                limit:
                    payload.length,
                offset: 0,
                raw: payload
            };
        }

        if (
            payload &&
            typeof payload === "object"
        ) {
            const records =
                Array.isArray(payload.records)
                    ? payload.records
                    : (
                        Array.isArray(payload.items)
                            ? payload.items
                            : (
                                Array.isArray(payload.history)
                                    ? payload.history
                                    : []
                            )
                    );

            return {
                records:
                    records.map(
                        normalizeRecord
                    ),
                total:
                    Number.isFinite(
                        Number(payload.total)
                    )
                        ? Number(payload.total)
                        : records.length,
                limit:
                    Number.isFinite(
                        Number(payload.limit)
                    )
                        ? Number(payload.limit)
                        : records.length,
                offset:
                    Number.isFinite(
                        Number(payload.offset)
                    )
                        ? Number(payload.offset)
                        : 0,
                next:
                    payload.next ??
                    payload.nextPage ??
                    null,
                previous:
                    payload.previous ??
                    payload.previousPage ??
                    null,
                raw: payload
            };
        }

        return {
            records: [],
            total: 0,
            limit: 0,
            offset: 0,
            raw: payload
        };
    }

    class ArchiveHistoryService extends EventTarget {
        constructor(context) {
            super();

            if (!context || typeof context !== "object") {
                throw new TypeError(
                    "A terminal context is required."
                );
            }

            this.context = context;
            this.destroyed = false;
        }

        ensureAvailable() {
            if (this.destroyed) {
                throw new Error(
                    "Archive-history service has been destroyed."
                );
            }

            if (
                !this.context.api ||
                typeof this.context.api.get !==
                "function"
            ) {
                throw new Error(
                    "Speciedex API client is unavailable."
                );
            }
        }

        emit(name, detail) {
            dispatch(
                this,
                name,
                detail
            );

            try {
                this.context.events?.emit?.(
                    `archive-history:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                ----------------------------------------------------------------
                Observer failures must not break archive-history requests.
                ----------------------------------------------------------------
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-archive-history-${name}`,
                detail,
                {
                    bubbles: true
                }
            );
        }

        async list(parameters = {}, options = {}) {
            this.ensureAvailable();

            const normalized =
                normalizeParameters(
                    parameters
                );

            const startedAt =
                performance.now();

            this.emit(
                "request",
                {
                    parameters: normalized
                }
            );

            try {
                const payload =
                    await this.context.api.get(
                        "archive/history",
                        normalized,
                        options
                    );

                const result =
                    normalizeResponse(
                        payload
                    );

                result.parameters =
                    normalized;

                result.duration =
                    performance.now() -
                    startedAt;

                this.emit(
                    "complete",
                    result
                );

                return result;
            } catch (error) {
                this.emit(
                    "error",
                    {
                        error,
                        parameters:
                            normalized,
                        duration:
                            performance.now() -
                            startedAt
                    }
                );

                throw error;
            }
        }

        async latest(limit = 10, options = {}) {
            return this.list(
                {
                    limit,
                    sort:
                        "published_at",
                    direction:
                        "desc"
                },
                options
            );
        }

        async byProvider(provider, parameters = {}, options = {}) {
            const normalizedProvider =
                normalizeText(provider);

            if (!normalizedProvider) {
                throw new TypeError(
                    "A provider name is required."
                );
            }

            return this.list(
                {
                    ...parameters,
                    provider:
                        normalizedProvider
                },
                options
            );
        }

        async between(from, to, parameters = {}, options = {}) {
            return this.list(
                {
                    ...parameters,
                    from,
                    to
                },
                options
            );
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "archive/history",
                service:
                    SERVICE_NAME,
                available:
                    Boolean(
                        this.context.api &&
                        typeof this.context.api.get ===
                        "function"
                    ),
                destroyed:
                    this.destroyed
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.destroyed = true;

            dispatch(
                this,
                "destroy",
                {
                    timestamp:
                        new Date().toISOString()
                }
            );

            return true;
        }
    }

    function initialize(context) {
        const existing =
            context.services?.get?.(
                SERVICE_NAME
            );

        if (
            existing instanceof
            ArchiveHistoryService &&
            !existing.destroyed
        ) {
            context.archiveHistory =
                existing;

            return existing;
        }

        if (
            context.archiveHistory instanceof
            ArchiveHistoryService &&
            !context.archiveHistory.destroyed
        ) {
            return context.archiveHistory;
        }

        const service =
            new ArchiveHistoryService(
                context
            );

        context.archiveHistory =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "archiveHistory",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-archive-history-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.archiveHistory ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                ArchiveHistoryService
            )
        ) {
            throw new Error(
                "Archive-history service is unavailable."
            );
        }

        return service;
    }

    function writeJSONValue(writeJSON, value) {
        if (
            typeof writeJSON ===
            "function"
        ) {
            return writeJSON(value);
        }

        return value;
    }

    function parseCommandArguments(args = []) {
        const parameters = {};
        const positional = [];

        for (const argument of args) {
            if (
                argument.startsWith(
                    "--limit="
                )
            ) {
                parameters.limit =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--offset="
                )
            ) {
                parameters.offset =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--from="
                )
            ) {
                parameters.from =
                    argument.slice(7);
                continue;
            }

            if (
                argument.startsWith(
                    "--to="
                )
            ) {
                parameters.to =
                    argument.slice(5);
                continue;
            }

            if (
                argument.startsWith(
                    "--provider="
                )
            ) {
                parameters.provider =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--status="
                )
            ) {
                parameters.status =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--sort="
                )
            ) {
                parameters.sort =
                    argument.slice(7);
                continue;
            }

            if (
                argument.startsWith(
                    "--direction="
                )
            ) {
                parameters.direction =
                    argument.slice(12);
                continue;
            }

            positional.push(argument);
        }

        if (positional.length) {
            parameters.q =
                positional[0];
        }

        if (
            positional[1] !==
            undefined
        ) {
            parameters.limit =
                positional[1];
        }

        return normalizeParameters(
            parameters
        );
    }

    const commands = [
        {
            name: "archive-history",
            aliases: [
                "archive-log"
            ],
            category: "archive",
            description:
                "Display archive publication history.",
            usage:
                "archive-history [query] [limit] [--from=DATE] [--to=DATE] [--provider=NAME] [--status=STATUS] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const service =
                    requireService(context);

                const parameters =
                    parseCommandArguments(
                        args
                    );

                const result =
                    await service.list(
                        parameters
                    );

                return writeJSONValue(
                    writeJSON,
                    result
                );
            }
        },
        {
            name: "archive-history-latest",
            aliases: [
                "archive-latest"
            ],
            category: "archive",
            description:
                "Display the most recent archive publications.",
            usage:
                "archive-history-latest [limit]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).latest(
                        args[0] ||
                        DEFAULT_LIMIT
                    )
                )
        },
        {
            name: "archive-history-status",
            category: "archive",
            description:
                "Show archive-history service status.",
            usage:
                "archive-history-status",
            handler: ({
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    requireService(
                        context
                    ).status()
                )
        }
    ];

    const api = Object.freeze({
        name: MODULE_NAME,
        version: VERSION,
        serviceName:
            SERVICE_NAME,
        ArchiveHistoryService,
        normalizeParameters,
        normalizeResponse,
        normalizeRecord,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalArchiveHistory =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

    dispatch(
        document,
        "speciedex:terminal-module-available",
        {
            name: MODULE_NAME,
            module: api
        }
    );
})(window, document);
