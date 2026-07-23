/*
========================================================================
Speciedex.org
Terminal ProviderAssertions Module
========================================================================

Provider assertion inspection service for SpeciedexTerminal.

Provides:

    • Validated assertion API requests
    • Provider, field, value, status, confidence, source, rank, and date filters
    • Normalized assertion records
    • Provider, field, source, status, and confidence summaries
    • Single-assertion retrieval
    • Conflict and low-confidence views
    • Lifecycle events, caching, and resilient service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "ProviderAssertions";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "provider-assertions";

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

    function normalizeText(value) {
        return String(value ?? "").trim();
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

    function normalizeBoolean(value, fallback = null) {
        if (typeof value === "boolean") {
            return value;
        }

        if (
            value === 1 ||
            value === "1" ||
            String(value).toLowerCase() === "true"
        ) {
            return true;
        }

        if (
            value === 0 ||
            value === "0" ||
            String(value).toLowerCase() === "false"
        ) {
            return false;
        }

        return fallback;
    }

    function normalizeDate(value) {
        const text = normalizeText(value);

        if (!text) {
            return "";
        }

        const timestamp = Date.parse(text);

        if (!Number.isFinite(timestamp)) {
            throw new TypeError(
                `Invalid date value: ${value}`
            );
        }

        return new Date(timestamp).toISOString();
    }

    function normalizeSort(value) {
        const normalized = normalizeText(
            value || "updated_at"
        ).toLowerCase();

        const allowed = new Set([
            "updated_at",
            "created_at",
            "provider",
            "field",
            "status",
            "confidence",
            "rank",
            "record",
            "source",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported assertion sort field: ${value}`
            );
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized = normalizeText(
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
            q: normalizeText(
                source.q ??
                source.query ??
                ""
            ),
            limit: clampInteger(
                source.limit,
                DEFAULT_LIMIT,
                MIN_LIMIT,
                MAX_LIMIT
            ),
            offset: clampInteger(
                source.offset,
                0,
                0,
                Number.MAX_SAFE_INTEGER
            ),
            sort: normalizeSort(
                source.sort
            ),
            direction: normalizeDirection(
                source.direction ??
                source.order
            )
        };

        for (
            const key of
            [
                "provider",
                "provider_id",
                "field",
                "value",
                "status",
                "source",
                "rank",
                "record",
                "record_id",
                "taxon",
                "taxon_id",
                "assertion",
                "assertion_id",
                "license"
            ]
        ) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                normalized[key] =
                    normalizeText(source[key]);
            }
        }

        for (
            const key of
            [
                "accepted",
                "conflicting",
                "verified",
                "active"
            ]
        ) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                const value = normalizeBoolean(
                    source[key],
                    null
                );

                if (value === null) {
                    throw new TypeError(
                        `Invalid ${key} value: ${source[key]}`
                    );
                }

                normalized[key] = value;
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
                clampNumber(
                    Number(minimumConfidence) > 1
                        ? Number(minimumConfidence) / 100
                        : minimumConfidence,
                    0,
                    0,
                    1
                );
        }

        if (
            maximumConfidence !== undefined &&
            maximumConfidence !== null &&
            maximumConfidence !== ""
        ) {
            normalized.max_confidence =
                clampNumber(
                    Number(maximumConfidence) > 1
                        ? Number(maximumConfidence) / 100
                        : maximumConfidence,
                    1,
                    0,
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
                "Assertion start date must not be later than the end date."
            );
        }

        return normalized;
    }

    function numericValue(value, fallback = 0) {
        const number = Number(value);

        return Number.isFinite(number)
            ? number
            : fallback;
    }

    function normalizeStringArray(value) {
        if (Array.isArray(value)) {
            return [
                ...new Set(
                    value
                        .map(normalizeText)
                        .filter(Boolean)
                )
            ];
        }

        const text = normalizeText(value);

        return text
            ? [
                ...new Set(
                    text
                        .split(/[,\s]+/)
                        .map(normalizeText)
                        .filter(Boolean)
                )
            ]
            : [];
    }

    function normalizeConfidence(value) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return null;
        }

        return Math.min(
            1,
            Math.max(
                0,
                number > 1 &&
                number <= 100
                    ? number / 100
                    : number
            )
        );
    }

    function normalizeRecord(record, index = 0) {
        if (
            !record ||
            typeof record !== "object"
        ) {
            return {
                index,
                id:
                    normalizeText(record),
                provider: "",
                field: "",
                value: record,
                status: "unknown",
                confidence: null,
                accepted: false,
                conflicting: false,
                verified: false,
                active: true,
                sources: []
            };
        }

        const confidence = normalizeConfidence(
            record.confidence ??
            record.score ??
            record.confidence_score ??
            record.confidenceScore
        );

        const status = normalizeText(
            record.status ??
            (
                record.accepted === true
                    ? "accepted"
                    : "unknown"
            )
        ).toLowerCase();

        const accepted =
            record.accepted === true ||
            [
                "accepted",
                "resolved",
                "confirmed",
                "canonical"
            ].includes(status);

        const conflicting =
            record.conflicting === true ||
            record.conflict === true ||
            [
                "conflict",
                "conflicting",
                "disputed"
            ].includes(status);

        const verified =
            record.verified === true ||
            [
                "verified",
                "confirmed",
                "accepted"
            ].includes(status);

        const active =
            record.active !== false &&
            record.deleted !== true &&
            status !== "inactive" &&
            status !== "deleted";

        return {
            ...record,
            index:
                record.index ??
                index,
            id: normalizeText(
                record.id ??
                record.assertion_id ??
                record.assertionId ??
                record.uuid ??
                `assertion-${index + 1}`
            ),
            provider: normalizeText(
                record.provider ??
                record.provider_name ??
                record.providerName ??
                record.provider_id ??
                record.providerId ??
                ""
            ),
            provider_id: normalizeText(
                record.provider_id ??
                record.providerId ??
                record.provider ??
                ""
            ),
            record_id: normalizeText(
                record.record_id ??
                record.recordId ??
                record.taxon_id ??
                record.taxonId ??
                record.entity_id ??
                record.entityId ??
                ""
            ),
            field: normalizeText(
                record.field ??
                record.property ??
                record.attribute ??
                record.key ??
                ""
            ),
            value:
                record.value ??
                record.asserted_value ??
                record.assertedValue ??
                record.data ??
                null,
            normalized_value:
                record.normalized_value ??
                record.normalizedValue ??
                record.value ??
                null,
            status,
            confidence,
            accepted,
            conflicting,
            verified,
            active,
            rank: normalizeText(
                record.rank ??
                record.taxon_rank ??
                record.taxonRank ??
                ""
            ),
            source: normalizeText(
                record.source ??
                record.source_name ??
                record.sourceName ??
                ""
            ),
            sources: normalizeStringArray(
                record.sources ??
                record.evidence_sources ??
                record.evidenceSources ??
                record.source
            ),
            evidence:
                Array.isArray(record.evidence)
                    ? record.evidence
                    : (
                        record.evidence
                            ? [record.evidence]
                            : []
                    ),
            license: normalizeText(
                record.license ??
                record.licence ??
                ""
            ),
            created_at:
                record.created_at ??
                record.createdAt ??
                "",
            updated_at:
                record.updated_at ??
                record.updatedAt ??
                record.last_updated ??
                record.lastUpdated ??
                ""
        };
    }

    function incrementMap(map, key) {
        const normalized =
            normalizeText(key) ||
            "unknown";

        map.set(
            normalized,
            (
                map.get(normalized) ||
                0
            ) + 1
        );
    }

    function mapToSortedObject(map) {
        return Object.fromEntries(
            [...map.entries()]
                .sort(
                    (left, right) =>
                        right[1] -
                        left[1] ||
                        left[0].localeCompare(
                            right[0]
                        )
                )
        );
    }

    function summarize(records) {
        const values =
            Array.isArray(records)
                ? records
                : [];

        const providers = new Map();
        const fields = new Map();
        const statuses = new Map();
        const sources = new Map();
        const ranks = new Map();

        const confidences = [];

        for (const assertion of values) {
            incrementMap(
                providers,
                assertion.provider
            );

            incrementMap(
                fields,
                assertion.field
            );

            incrementMap(
                statuses,
                assertion.status
            );

            incrementMap(
                ranks,
                assertion.rank
            );

            for (
                const source of
                assertion.sources || []
            ) {
                incrementMap(
                    sources,
                    source
                );
            }

            if (
                Number.isFinite(
                    assertion.confidence
                )
            ) {
                confidences.push(
                    assertion.confidence
                );
            }
        }

        return {
            total:
                values.length,
            accepted:
                values.filter(
                    assertion =>
                        assertion.accepted
                ).length,
            conflicting:
                values.filter(
                    assertion =>
                        assertion.conflicting
                ).length,
            verified:
                values.filter(
                    assertion =>
                        assertion.verified
                ).length,
            active:
                values.filter(
                    assertion =>
                        assertion.active
                ).length,
            inactive:
                values.filter(
                    assertion =>
                        !assertion.active
                ).length,
            averageConfidence:
                confidences.length
                    ? confidences.reduce(
                        (sum, value) =>
                            sum + value,
                        0
                    ) /
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
                    : null,
            providers:
                mapToSortedObject(
                    providers
                ),
            fields:
                mapToSortedObject(
                    fields
                ),
            statuses:
                mapToSortedObject(
                    statuses
                ),
            sources:
                mapToSortedObject(
                    sources
                ),
            ranks:
                mapToSortedObject(
                    ranks
                )
        };
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
                                Array.isArray(payload.assertions)
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

    class ProviderAssertionsService extends EventTarget {
        constructor(context) {
            super();

            if (
                !context ||
                typeof context !== "object"
            ) {
                throw new TypeError(
                    "A terminal context is required."
                );
            }

            this.context = context;
            this.destroyed = false;
            this.cache = null;
            this.cacheTimestamp = 0;
        }

        ensureAvailable() {
            if (this.destroyed) {
                throw new Error(
                    "Provider-assertions service has been destroyed."
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
                    `provider-assertions:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not break assertion operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-provider-assertions-${name}`,
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
                        "providers/assertions",
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

                this.cache =
                    result;

                this.cacheTimestamp =
                    Date.now();

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
                    "An assertion ID is required."
                );
            }

            try {
                const payload =
                    await this.context.api.get(
                        `providers/assertions/${encodeURIComponent(normalizedId)}`,
                        {},
                        options
                    );

                return normalizeRecord(
                    payload,
                    0
                );
            } catch (error) {
                const match =
                    this.cache?.records?.find(
                        assertion =>
                            assertion.id ===
                            normalizedId
                    );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async conflicts(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        conflicting: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    assertion =>
                        assertion.conflicting
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async lowConfidence(threshold = 0.5, parameters = {}, options = {}) {
            const normalizedThreshold =
                clampNumber(
                    Number(threshold) > 1
                        ? Number(threshold) / 100
                        : threshold,
                    0.5,
                    0,
                    1
                );

            const result =
                await this.list(
                    {
                        ...parameters,
                        max_confidence:
                            normalizedThreshold
                    },
                    options
                );

            const records =
                result.records.filter(
                    assertion =>
                        assertion.confidence === null ||
                        assertion.confidence <=
                        normalizedThreshold
                );

            return {
                ...result,
                threshold:
                    normalizedThreshold,
                records,
                summary:
                    summarize(records)
            };
        }

        async byProvider(provider, parameters = {}, options = {}) {
            const normalizedProvider =
                normalizeText(provider);

            if (!normalizedProvider) {
                throw new TypeError(
                    "A provider ID or name is required."
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
                assertions:
                    result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "providers/assertions",
                service:
                    SERVICE_NAME,
                available:
                    Boolean(
                        this.context.api &&
                        typeof this.context.api.get ===
                        "function"
                    ),
                cached:
                    Boolean(this.cache),
                cacheAge:
                    this.cacheTimestamp
                        ? Date.now() -
                          this.cacheTimestamp
                        : null,
                destroyed:
                    this.destroyed
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.cache = null;
            this.cacheTimestamp = 0;
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
            ProviderAssertionsService &&
            !existing.destroyed
        ) {
            context.providerAssertions =
                existing;

            return existing;
        }

        if (
            context.providerAssertions instanceof
            ProviderAssertionsService &&
            !context.providerAssertions.destroyed
        ) {
            return context.providerAssertions;
        }

        const service =
            new ProviderAssertionsService(
                context
            );

        context.providerAssertions =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "providerAssertions",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-provider-assertions-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.providerAssertions ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                ProviderAssertionsService
            )
        ) {
            throw new Error(
                "Provider-assertions service is unavailable."
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
                    "--field="
                )
            ) {
                parameters.field =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--value="
                )
            ) {
                parameters.value =
                    argument.slice(8);
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
                    "--source="
                )
            ) {
                parameters.source =
                    argument.slice(9);
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
                    "--record="
                )
            ) {
                parameters.record =
                    argument.slice(9);
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
                    "--assertion="
                )
            ) {
                parameters.assertion =
                    argument.slice(12);
                continue;
            }

            if (
                argument.startsWith(
                    "--license="
                )
            ) {
                parameters.license =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--accepted="
                )
            ) {
                parameters.accepted =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--conflicting="
                )
            ) {
                parameters.conflicting =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--verified="
                )
            ) {
                parameters.verified =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--active="
                )
            ) {
                parameters.active =
                    argument.slice(9);
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
            name: "provider-assertions",
            aliases: [
                "assertions-by-provider"
            ],
            category: "providers",
            description:
                "Inspect assertions grouped by provider.",
            usage:
                "provider-assertions [query] [limit] [--provider=ID] [--field=NAME] [--value=VALUE] [--status=STATUS] [--source=SOURCE] [--rank=RANK] [--record=ID] [--taxon=ID] [--assertion=ID] [--license=LICENSE] [--accepted=true|false] [--conflicting=true|false] [--verified=true|false] [--active=true|false] [--min-confidence=N] [--max-confidence=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
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
            name: "provider-assertion",
            aliases: [
                "assertion-get"
            ],
            category: "providers",
            description:
                "Retrieve one provider assertion by ID.",
            usage:
                "provider-assertion <id>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id =
                    args.join(" ")
                        .trim();

                if (!id) {
                    throw new Error(
                        "An assertion ID is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).get(id)
                );
            }
        },
        {
            name: "provider-assertion-conflicts",
            aliases: [
                "assertion-conflicts"
            ],
            category: "providers",
            description:
                "List conflicting provider assertions.",
            usage:
                "provider-assertion-conflicts [filters]",
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
            name: "provider-assertion-low-confidence",
            aliases: [
                "low-confidence-assertions"
            ],
            category: "providers",
            description:
                "List provider assertions at or below a confidence threshold.",
            usage:
                "provider-assertion-low-confidence [threshold] [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                let threshold = 0.5;
                let filters = args;

                if (
                    args.length &&
                    !String(args[0]).startsWith("--") &&
                    Number.isFinite(
                        Number(args[0])
                    )
                ) {
                    threshold =
                        Number(args[0]);

                    filters =
                        args.slice(1);
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).lowConfidence(
                        threshold,
                        parseCommandArguments(
                            filters
                        )
                    )
                );
            }
        },
        {
            name: "provider-assertions-summary",
            aliases: [
                "assertion-summary"
            ],
            category: "providers",
            description:
                "Summarize provider assertions, fields, sources, statuses, ranks, and confidence.",
            usage:
                "provider-assertions-summary [filters]",
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
            name: "provider-assertions-status",
            category: "providers",
            description:
                "Show provider-assertions service status.",
            usage:
                "provider-assertions-status",
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
        ProviderAssertionsService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeConfidence,
        normalizeStringArray,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalProviderAssertions =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

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
