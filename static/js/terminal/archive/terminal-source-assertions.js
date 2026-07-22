/*
========================================================================
Speciedex.org
Terminal SourceAssertions Module
========================================================================

Source-assertion service for SpeciedexTerminal.

Provides:

    • Validated source-assertion API requests
    • Provider, taxon, rank, status, confidence, date, and pagination filters
    • Normalized assertion records and confidence values
    • Conflict, consensus, and provider summaries
    • Lifecycle events and service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "SourceAssertions";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "source-assertions";

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

    function clampNumber(value, fallback, minimum, maximum) {
        const parsed = Number(value);

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
                value || "asserted_at"
            ).toLowerCase();

        const allowed = new Set([
            "asserted_at",
            "created_at",
            "updated_at",
            "provider",
            "taxon",
            "rank",
            "status",
            "confidence",
            "authority"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported source-assertion sort field: ${value}`
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

    function normalizeConfidence(value, fallback = null) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return fallback;
        }

        let number =
            Number(value);

        if (!Number.isFinite(number)) {
            return fallback;
        }

        if (number > 1 && number <= 100) {
            number /= 100;
        }

        return clampNumber(
            number,
            fallback,
            0,
            1
        );
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
                "taxon",
                "rank",
                "status",
                "authority",
                "source",
                "type",
                "dataset",
                "release"
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

        const minimumConfidence =
            source.minConfidence ??
            source.min_confidence;

        const maximumConfidence =
            source.maxConfidence ??
            source.max_confidence;

        if (
            minimumConfidence !== undefined &&
            minimumConfidence !== null &&
            minimumConfidence !== ""
        ) {
            normalized.min_confidence =
                normalizeConfidence(
                    minimumConfidence,
                    0
                );
        }

        if (
            maximumConfidence !== undefined &&
            maximumConfidence !== null &&
            maximumConfidence !== ""
        ) {
            normalized.max_confidence =
                normalizeConfidence(
                    maximumConfidence,
                    1
                );
        }

        if (
            normalized.min_confidence !== undefined &&
            normalized.max_confidence !== undefined &&
            normalized.min_confidence >
            normalized.max_confidence
        ) {
            throw new RangeError(
                "Minimum confidence must not exceed maximum confidence."
            );
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
                "Source-assertion start date must not be later than the end date."
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
                value: record,
                confidence: null
            };
        }

        const assertedAt =
            record.asserted_at ??
            record.assertedAt ??
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
                    record.assertion_id ??
                    record.assertionId ??
                    ""
                ),
            provider:
                normalizeText(
                    record.provider ??
                    record.source ??
                    ""
                ),
            taxon:
                normalizeText(
                    record.taxon ??
                    record.taxon_name ??
                    record.scientific_name ??
                    record.name ??
                    ""
                ),
            rank:
                normalizeText(
                    record.rank ??
                    ""
                ),
            status:
                normalizeText(
                    record.status ??
                    record.assertion_status ??
                    ""
                ),
            authority:
                normalizeText(
                    record.authority ??
                    record.author ??
                    ""
                ),
            confidence:
                normalizeConfidence(
                    record.confidence ??
                    record.score ??
                    record.probability
                ),
            asserted_at:
                assertedAt
                    ? normalizeDate(
                        assertedAt
                    )
                    : "",
            accepted:
                record.accepted ??
                (
                    String(
                        record.status || ""
                    ).toLowerCase() ===
                    "accepted"
                ),
            conflict:
                record.conflict ??
                record.conflicted ??
                false
        };
    }

    function summarize(records) {
        const values =
            Array.isArray(records)
                ? records
                : [];

        const confidences =
            values
                .map(
                    record =>
                        normalizeConfidence(
                            record.confidence
                        )
                )
                .filter(
                    value =>
                        Number.isFinite(
                            value
                        )
                );

        const accepted =
            values.filter(
                record =>
                    record.accepted === true
            ).length;

        const conflicts =
            values.filter(
                record =>
                    record.conflict === true
            ).length;

        const providers =
            new Set(
                values
                    .map(
                        record =>
                            record.provider
                    )
                    .filter(Boolean)
            );

        const taxa =
            new Set(
                values
                    .map(
                        record =>
                            record.taxon
                    )
                    .filter(Boolean)
            );

        const confidenceTotal =
            confidences.reduce(
                (sum, value) =>
                    sum + value,
                0
            );

        return {
            total:
                values.length,
            accepted,
            rejected:
                values.length -
                accepted,
            conflicts,
            providers:
                providers.size,
            taxa:
                taxa.size,
            averageConfidence:
                confidences.length
                    ? confidenceTotal /
                      confidences.length
                    : null,
            minimumConfidence:
                confidences.length
                    ? Math.min(
                        ...confidences
                    )
                    : null,
            maximumConfidence:
                confidences.length
                    ? Math.max(
                        ...confidences
                    )
                    : null
        };
    }

    function groupBy(records, key) {
        const values =
            Array.isArray(records)
                ? records
                : [];

        const groups = new Map();

        for (const record of values) {
            const group =
                normalizeText(
                    record[key] ??
                    "unknown"
                ) || "unknown";

            const current =
                groups.get(group) || {
                    key: group,
                    count: 0,
                    accepted: 0,
                    conflicts: 0,
                    confidenceTotal: 0,
                    confidenceCount: 0
                };

            current.count += 1;

            if (record.accepted === true) {
                current.accepted += 1;
            }

            if (record.conflict === true) {
                current.conflicts += 1;
            }

            const confidence =
                normalizeConfidence(
                    record.confidence
                );

            if (
                Number.isFinite(
                    confidence
                )
            ) {
                current.confidenceTotal +=
                    confidence;

                current.confidenceCount += 1;
            }

            groups.set(
                group,
                current
            );
        }

        return [
            ...groups.values()
        ]
            .map(group => ({
                key:
                    group.key,
                count:
                    group.count,
                accepted:
                    group.accepted,
                conflicts:
                    group.conflicts,
                averageConfidence:
                    group.confidenceCount
                        ? group.confidenceTotal /
                          group.confidenceCount
                        : null
            }))
            .sort(
                (left, right) =>
                    right.count -
                    left.count
            );
    }

    function normalizeResponse(payload) {
        if (Array.isArray(payload)) {
            const records =
                payload.map(
                    normalizeRecord
                );

            return {
                records,
                total:
                    records.length,
                limit:
                    records.length,
                offset: 0,
                summary:
                    summarize(records),
                raw: payload
            };
        }

        if (
            payload &&
            typeof payload === "object"
        ) {
            const values =
                Array.isArray(payload.records)
                    ? payload.records
                    : (
                        Array.isArray(payload.items)
                            ? payload.items
                            : (
                                Array.isArray(
                                    payload.assertions
                                )
                                    ? payload.assertions
                                    : []
                            )
                    );

            const records =
                values.map(
                    normalizeRecord
                );

            return {
                records,
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
                summary:
                    payload.summary &&
                    typeof payload.summary === "object"
                        ? {
                            ...summarize(records),
                            ...payload.summary
                        }
                        : summarize(records),
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
            summary:
                summarize([]),
            raw: payload
        };
    }

    class SourceAssertionsService extends EventTarget {
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
                    "Source-assertions service has been destroyed."
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
                    `source-assertions:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                ----------------------------------------------------------------
                Observer failures must not break assertion requests.
                ----------------------------------------------------------------
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-source-assertions-${name}`,
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
                    parameters:
                        normalized
                }
            );

            try {
                const payload =
                    await this.context.api.get(
                        "archive/assertions",
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

        async forTaxon(
            taxon,
            parameters = {},
            options = {}
        ) {
            const normalizedTaxon =
                normalizeText(taxon);

            if (!normalizedTaxon) {
                throw new TypeError(
                    "A taxon name or identifier is required."
                );
            }

            return this.list(
                {
                    ...parameters,
                    taxon:
                        normalizedTaxon
                },
                options
            );
        }

        async forProvider(
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

        async conflicts(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        status:
                            parameters.status ??
                            "conflict"
                    },
                    options
                );

            return {
                ...result,
                records:
                    result.records.filter(
                        record =>
                            record.conflict === true ||
                            String(
                                record.status
                            ).toLowerCase() ===
                            "conflict"
                    )
            };
        }

        async summary(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        limit:
                            parameters.limit ??
                            MAX_LIMIT
                    },
                    options
                );

            return {
                parameters:
                    result.parameters,
                summary:
                    summarize(
                        result.records
                    ),
                byProvider:
                    groupBy(
                        result.records,
                        "provider"
                    ),
                byStatus:
                    groupBy(
                        result.records,
                        "status"
                    ),
                byRank:
                    groupBy(
                        result.records,
                        "rank"
                    )
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "archive/assertions",
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
            SourceAssertionsService &&
            !existing.destroyed
        ) {
            context.sourceAssertions =
                existing;

            return existing;
        }

        if (
            context.sourceAssertions instanceof
            SourceAssertionsService &&
            !context.sourceAssertions.destroyed
        ) {
            return context.sourceAssertions;
        }

        const service =
            new SourceAssertionsService(
                context
            );

        context.sourceAssertions =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "sourceAssertions",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-source-assertions-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.sourceAssertions ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                SourceAssertionsService
            )
        ) {
            throw new Error(
                "Source-assertions service is unavailable."
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
                    "--taxon="
                )
            ) {
                parameters.taxon =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--rank="
                )
            ) {
                parameters.rank =
                    argument.slice(7);
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
                    "--authority="
                )
            ) {
                parameters.authority =
                    argument.slice(12);
                continue;
            }

            if (
                argument.startsWith(
                    "--source="
                )
            ) {
                parameters.source =
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
                    "--dataset="
                )
            ) {
                parameters.dataset =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--release="
                )
            ) {
                parameters.release =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-confidence="
                )
            ) {
                parameters.min_confidence =
                    argument.slice(17);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-confidence="
                )
            ) {
                parameters.max_confidence =
                    argument.slice(17);
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
            name: "source-assertions",
            aliases: [
                "assertions"
            ],
            category: "archive",
            description:
                "Inspect source assertion records.",
            usage:
                "source-assertions [query] [limit] [--provider=NAME] [--taxon=NAME] [--rank=RANK] [--status=STATUS] [--authority=NAME] [--source=NAME] [--type=TYPE] [--dataset=NAME] [--release=ID] [--min-confidence=N] [--max-confidence=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
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
            name: "source-assertions-summary",
            aliases: [
                "assertions-summary"
            ],
            category: "archive",
            description:
                "Summarize source assertions by provider, status, and rank.",
            usage:
                "source-assertions-summary [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).summary(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "source-assertion-conflicts",
            aliases: [
                "assertion-conflicts"
            ],
            category: "archive",
            description:
                "Display conflicting source assertions.",
            usage:
                "source-assertion-conflicts [query] [limit]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).conflicts(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "source-assertions-status",
            category: "archive",
            description:
                "Show source-assertions service status.",
            usage:
                "source-assertions-status",
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
        SourceAssertionsService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeConfidence,
        summarize,
        groupBy,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalSourceAssertions =
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
