/*
========================================================================
Speciedex.org
Terminal Releases Module
========================================================================

Archive release service for SpeciedexTerminal.

Provides:

    • Validated release-list API requests
    • Provider, status, channel, version, date, and pagination filters
    • Single-release retrieval
    • Latest and stable release helpers
    • Normalized release responses
    • Lifecycle events and service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Releases";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "releases";

    const DEFAULT_LIMIT = 50;
    const MIN_LIMIT = 1;
    const MAX_LIMIT = 1000;

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
            normalizeText(
                value || "published_at"
            ).toLowerCase();

        const allowed = new Set([
            "published_at",
            "created_at",
            "updated_at",
            "version",
            "provider",
            "status",
            "channel",
            "records",
            "files",
            "size"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported release sort field: ${value}`
            );
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized =
            normalizeText(
                value || "desc"
            ).toLowerCase();

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

        for (
            const key of
            [
                "provider",
                "status",
                "channel",
                "version",
                "archive",
                "volume",
                "format",
                "type"
            ]
        ) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                normalized[key] =
                    normalizeText(
                        source[key]
                    );
            }
        }

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
                "Release start date must not be later than the end date."
            );
        }

        return normalized;
    }

    function numericValue(value, fallback = null) {
        const number =
            Number(value);

        return Number.isFinite(number)
            ? number
            : fallback;
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

        const publishedAt =
            record.published_at ??
            record.publishedAt ??
            record.released_at ??
            record.releasedAt ??
            record.created_at ??
            record.createdAt ??
            "";

        return {
            ...record,
            index:
                record.index ??
                index,
            id:
                normalizeText(
                    record.id ??
                    record.release_id ??
                    record.releaseId ??
                    ""
                ),
            version:
                normalizeText(
                    record.version ??
                    record.tag ??
                    record.name ??
                    ""
                ),
            provider:
                normalizeText(
                    record.provider ??
                    record.source ??
                    ""
                ),
            status:
                normalizeText(
                    record.status ??
                    ""
                ),
            channel:
                normalizeText(
                    record.channel ??
                    record.track ??
                    ""
                ),
            published_at:
                publishedAt
                    ? normalizeDate(
                        publishedAt
                    )
                    : "",
            record_count:
                numericValue(
                    record.record_count ??
                    record.recordCount ??
                    record.records
                ),
            file_count:
                numericValue(
                    record.file_count ??
                    record.fileCount ??
                    (
                        Array.isArray(
                            record.files
                        )
                            ? record.files.length
                            : null
                    )
                ),
            size:
                numericValue(
                    record.size ??
                    record.size_bytes ??
                    record.sizeBytes
                )
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
                                Array.isArray(payload.releases)
                                    ? payload.releases
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

    function compareVersions(left, right) {
        const tokenize = value =>
            normalizeText(value)
                .replace(/^v/i, "")
                .split(/[.-]/)
                .map(part =>
                    /^\d+$/.test(part)
                        ? Number(part)
                        : part.toLowerCase()
                );

        const leftParts =
            tokenize(left);

        const rightParts =
            tokenize(right);

        const length =
            Math.max(
                leftParts.length,
                rightParts.length
            );

        for (
            let index = 0;
            index < length;
            index += 1
        ) {
            const leftPart =
                leftParts[index] ?? 0;

            const rightPart =
                rightParts[index] ?? 0;

            if (leftPart === rightPart) {
                continue;
            }

            if (
                typeof leftPart === "number" &&
                typeof rightPart === "number"
            ) {
                return leftPart - rightPart;
            }

            return String(leftPart)
                .localeCompare(
                    String(rightPart),
                    undefined,
                    {
                        numeric: true,
                        sensitivity: "base"
                    }
                );
        }

        return 0;
    }

    class ReleasesService extends EventTarget {
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
                    "Releases service has been destroyed."
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
                    `releases:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                ----------------------------------------------------------------
                Observer failures must not break release requests.
                ----------------------------------------------------------------
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-releases-${name}`,
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
                    operation:
                        "list",
                    parameters:
                        normalized
                }
            );

            try {
                const payload =
                    await this.context.api.get(
                        "archive/releases",
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
                        operation:
                            "list",
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

        async get(id, options = {}) {
            this.ensureAvailable();

            const normalizedId =
                normalizeText(id);

            if (!normalizedId) {
                throw new TypeError(
                    "A release ID or version is required."
                );
            }

            const startedAt =
                performance.now();

            this.emit(
                "request",
                {
                    operation:
                        "get",
                    id:
                        normalizedId
                }
            );

            try {
                const payload =
                    await this.context.api.get(
                        `archive/releases/${encodeURIComponent(normalizedId)}`,
                        {},
                        options
                    );

                const release =
                    normalizeRecord(
                        payload,
                        0
                    );

                this.emit(
                    "complete",
                    {
                        release,
                        duration:
                            performance.now() -
                            startedAt
                    }
                );

                return release;
            } catch (error) {
                this.emit(
                    "error",
                    {
                        operation:
                            "get",
                        id:
                            normalizedId,
                        error,
                        duration:
                            performance.now() -
                            startedAt
                    }
                );

                throw error;
            }
        }

        async latest(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        limit:
                            parameters.limit ??
                            1,
                        sort:
                            parameters.sort ??
                            "published_at",
                        direction:
                            parameters.direction ??
                            "desc"
                    },
                    options
                );

            return (
                result.records[0] ||
                null
            );
        }

        async stable(parameters = {}, options = {}) {
            return this.latest(
                {
                    ...parameters,
                    status:
                        parameters.status ??
                        "stable",
                    channel:
                        parameters.channel ??
                        "stable"
                },
                options
            );
        }

        async byProvider(
            provider,
            parameters = {},
            options = {}
        ) {
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

        status() {
            return {
                version: VERSION,
                endpoint:
                    "archive/releases",
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
            ReleasesService &&
            !existing.destroyed
        ) {
            context.releases =
                existing;

            return existing;
        }

        if (
            context.releases instanceof
            ReleasesService &&
            !context.releases.destroyed
        ) {
            return context.releases;
        }

        const service =
            new ReleasesService(
                context
            );

        context.releases =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "release",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-releases-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.releases ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                ReleasesService
            )
        ) {
            throw new Error(
                "Releases service is unavailable."
            );
        }

        return service;
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
                    "--channel="
                )
            ) {
                parameters.channel =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--version="
                )
            ) {
                parameters.version =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--archive="
                )
            ) {
                parameters.archive =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--volume="
                )
            ) {
                parameters.volume =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--format="
                )
            ) {
                parameters.format =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--type="
                )
            ) {
                parameters.type =
                    argument.slice(7);
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

    function writeJSONValue(writeJSON, value) {
        if (
            typeof writeJSON ===
            "function"
        ) {
            return writeJSON(value);
        }

        return value;
    }

    const commands = [
        {
            name: "releases",
            aliases: [
                "release-list"
            ],
            category: "archive",
            description:
                "List Speciedex archive releases.",
            usage:
                "releases [query] [limit] [--provider=NAME] [--status=STATUS] [--channel=CHANNEL] [--version=VERSION] [--archive=NAME] [--volume=ID] [--format=FORMAT] [--type=TYPE] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const parameters =
                    parseCommandArguments(
                        args
                    );

                const result =
                    await requireService(
                        context
                    ).list(
                        parameters
                    );

                return writeJSONValue(
                    writeJSON,
                    result
                );
            }
        },
        {
            name: "release",
            aliases: [
                "release-get"
            ],
            category: "archive",
            description:
                "Retrieve one archive release by ID or version.",
            usage:
                "release <id|version>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                if (!args[0]) {
                    throw new Error(
                        "A release ID or version is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).get(
                        args[0]
                    )
                );
            }
        },
        {
            name: "release-latest",
            aliases: [
                "latest-release"
            ],
            category: "archive",
            description:
                "Display the latest archive release.",
            usage:
                "release-latest [--provider=NAME] [--channel=CHANNEL]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const parameters =
                    parseCommandArguments(
                        args
                    );

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).latest(
                        parameters
                    )
                );
            }
        },
        {
            name: "release-stable",
            aliases: [
                "stable-release"
            ],
            category: "archive",
            description:
                "Display the latest stable archive release.",
            usage:
                "release-stable [--provider=NAME]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const parameters =
                    parseCommandArguments(
                        args
                    );

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).stable(
                        parameters
                    )
                );
            }
        },
        {
            name: "releases-status",
            category: "archive",
            description:
                "Show release-service status.",
            usage:
                "releases-status",
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
        ReleasesService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        compareVersions,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalReleases =
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
