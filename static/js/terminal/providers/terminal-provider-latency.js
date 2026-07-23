/*
========================================================================
Speciedex.org
Terminal ProviderLatency Module
========================================================================

Provider response and ingestion latency service for SpeciedexTerminal.

Provides:

    • Validated provider-latency API requests
    • Provider, stage, endpoint, protocol, status, threshold, and date filters
    • Normalized latency and timing records
    • Minimum, maximum, average, median, p90, p95, and p99 summaries
    • Single-measurement retrieval
    • Slow, degraded, timeout, and stage-specific latency views
    • Lifecycle events, caching, and resilient service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "ProviderLatency";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "provider-latency";

    const DEFAULT_LIMIT = 50;
    const MIN_LIMIT = 1;
    const MAX_LIMIT = 1000;
    const DEFAULT_SLOW_THRESHOLD = 1000;

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
            value || "measured_at"
        ).toLowerCase();

        const allowed = new Set([
            "measured_at",
            "created_at",
            "updated_at",
            "provider",
            "latency",
            "response_time",
            "ingestion_time",
            "queue_time",
            "network_time",
            "stage",
            "status",
            "endpoint",
            "protocol",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported provider-latency sort field: ${value}`
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
                "stage",
                "status",
                "endpoint",
                "protocol",
                "region",
                "country",
                "measurement",
                "measurement_id",
                "job",
                "job_id",
                "run",
                "run_id",
                "category",
                "type"
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
                "timeout",
                "degraded",
                "successful",
                "cached"
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

        const numericFields = [
            ["min_latency", source.min_latency ?? source.minLatency],
            ["max_latency", source.max_latency ?? source.maxLatency],
            ["min_response_time", source.min_response_time ?? source.minResponseTime],
            ["max_response_time", source.max_response_time ?? source.maxResponseTime],
            ["min_ingestion_time", source.min_ingestion_time ?? source.minIngestionTime],
            ["max_ingestion_time", source.max_ingestion_time ?? source.maxIngestionTime],
            ["threshold", source.threshold]
        ];

        for (const [key, value] of numericFields) {
            if (
                value !== undefined &&
                value !== null &&
                value !== ""
            ) {
                normalized[key] =
                    clampNumber(
                        value,
                        0,
                        0,
                        Number.MAX_SAFE_INTEGER
                    );
            }
        }

        if (
            normalized.min_latency !== undefined &&
            normalized.max_latency !== undefined &&
            normalized.min_latency >
            normalized.max_latency
        ) {
            throw new RangeError(
                "Minimum latency must not exceed maximum latency."
            );
        }

        if (
            normalized.min_response_time !== undefined &&
            normalized.max_response_time !== undefined &&
            normalized.min_response_time >
            normalized.max_response_time
        ) {
            throw new RangeError(
                "Minimum response time must not exceed maximum response time."
            );
        }

        if (
            normalized.min_ingestion_time !== undefined &&
            normalized.max_ingestion_time !== undefined &&
            normalized.min_ingestion_time >
            normalized.max_ingestion_time
        ) {
            throw new RangeError(
                "Minimum ingestion time must not exceed maximum ingestion time."
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
                "Provider-latency start date must not be later than the end date."
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
                latency: 0,
                response_time: 0,
                ingestion_time: 0,
                queue_time: 0,
                network_time: 0,
                processing_time: 0,
                status: "unknown",
                stage: "unknown",
                timeout: false,
                degraded: false,
                successful: false,
                cached: false
            };
        }

        const latency = numericValue(
            record.latency ??
            record.latency_ms ??
            record.latencyMs ??
            record.total_time ??
            record.totalTime,
            0
        );

        const responseTime = numericValue(
            record.response_time ??
            record.responseTime ??
            record.response_ms ??
            record.responseMs,
            latency
        );

        const ingestionTime = numericValue(
            record.ingestion_time ??
            record.ingestionTime ??
            record.ingestion_ms ??
            record.ingestionMs,
            0
        );

        const status = normalizeText(
            record.status ??
            (
                record.timeout === true
                    ? "timeout"
                    : (
                        record.successful === false
                            ? "failed"
                            : "ok"
                    )
            )
        ).toLowerCase();

        const timeout =
            record.timeout === true ||
            status === "timeout";

        const successful =
            record.successful !== undefined
                ? Boolean(record.successful)
                : ![
                    "failed",
                    "error",
                    "timeout",
                    "unavailable"
                ].includes(status);

        const degraded =
            record.degraded === true ||
            [
                "degraded",
                "slow",
                "warning"
            ].includes(status);

        return {
            ...record,
            index:
                record.index ??
                index,
            id: normalizeText(
                record.id ??
                record.measurement_id ??
                record.measurementId ??
                record.uuid ??
                `latency-${index + 1}`
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
            latency,
            response_time:
                responseTime,
            ingestion_time:
                ingestionTime,
            queue_time: numericValue(
                record.queue_time ??
                record.queueTime ??
                record.queue_ms ??
                record.queueMs,
                0
            ),
            network_time: numericValue(
                record.network_time ??
                record.networkTime ??
                record.network_ms ??
                record.networkMs,
                0
            ),
            processing_time: numericValue(
                record.processing_time ??
                record.processingTime ??
                record.processing_ms ??
                record.processingMs,
                0
            ),
            dns_time: numericValue(
                record.dns_time ??
                record.dnsTime ??
                record.dns_ms ??
                record.dnsMs,
                0
            ),
            connect_time: numericValue(
                record.connect_time ??
                record.connectTime ??
                record.connect_ms ??
                record.connectMs,
                0
            ),
            tls_time: numericValue(
                record.tls_time ??
                record.tlsTime ??
                record.tls_ms ??
                record.tlsMs,
                0
            ),
            time_to_first_byte: numericValue(
                record.time_to_first_byte ??
                record.timeToFirstByte ??
                record.ttfb,
                0
            ),
            stage: normalizeText(
                record.stage ??
                record.phase ??
                record.operation ??
                "unknown"
            ).toLowerCase(),
            status,
            timeout,
            degraded,
            successful,
            cached:
                record.cached === true ||
                record.cache_hit === true ||
                record.cacheHit === true,
            endpoint: normalizeText(
                record.endpoint ??
                record.url ??
                record.path ??
                ""
            ),
            protocol: normalizeText(
                record.protocol ??
                record.transport ??
                ""
            ).toLowerCase(),
            region: normalizeText(
                record.region ??
                ""
            ),
            country: normalizeText(
                record.country ??
                ""
            ),
            category: normalizeText(
                record.category ??
                ""
            ),
            type: normalizeText(
                record.type ??
                record.measurement_type ??
                record.measurementType ??
                ""
            ),
            job_id: normalizeText(
                record.job_id ??
                record.jobId ??
                ""
            ),
            run_id: normalizeText(
                record.run_id ??
                record.runId ??
                record.execution_id ??
                record.executionId ??
                ""
            ),
            measured_at:
                record.measured_at ??
                record.measuredAt ??
                record.timestamp ??
                record.created_at ??
                record.createdAt ??
                "",
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

    function percentile(values, percentage) {
        const numbers =
            values
                .map(Number)
                .filter(Number.isFinite)
                .sort(
                    (left, right) =>
                        left - right
                );

        if (!numbers.length) {
            return null;
        }

        if (numbers.length === 1) {
            return numbers[0];
        }

        const position =
            (numbers.length - 1) *
            percentage;

        const lower =
            Math.floor(position);

        const upper =
            Math.ceil(position);

        if (lower === upper) {
            return numbers[lower];
        }

        const weight =
            position - lower;

        return (
            numbers[lower] *
            (1 - weight) +
            numbers[upper] *
            weight
        );
    }

    function metricSummary(values) {
        const numbers =
            values
                .map(Number)
                .filter(
                    value =>
                        Number.isFinite(value) &&
                        value >= 0
                );

        if (!numbers.length) {
            return {
                count: 0,
                minimum: null,
                maximum: null,
                average: null,
                median: null,
                p90: null,
                p95: null,
                p99: null
            };
        }

        return {
            count:
                numbers.length,
            minimum:
                Math.min(...numbers),
            maximum:
                Math.max(...numbers),
            average:
                numbers.reduce(
                    (sum, value) =>
                        sum + value,
                    0
                ) /
                numbers.length,
            median:
                percentile(
                    numbers,
                    0.5
                ),
            p90:
                percentile(
                    numbers,
                    0.9
                ),
            p95:
                percentile(
                    numbers,
                    0.95
                ),
            p99:
                percentile(
                    numbers,
                    0.99
                )
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
        const stages = new Map();
        const statuses = new Map();
        const endpoints = new Map();
        const protocols = new Map();
        const regions = new Map();

        for (const measurement of values) {
            incrementMap(
                providers,
                measurement.provider
            );

            incrementMap(
                stages,
                measurement.stage
            );

            incrementMap(
                statuses,
                measurement.status
            );

            incrementMap(
                endpoints,
                measurement.endpoint
            );

            incrementMap(
                protocols,
                measurement.protocol
            );

            incrementMap(
                regions,
                measurement.region
            );
        }

        return {
            total:
                values.length,
            successful:
                values.filter(
                    item =>
                        item.successful
                ).length,
            failed:
                values.filter(
                    item =>
                        !item.successful
                ).length,
            timeout:
                values.filter(
                    item =>
                        item.timeout
                ).length,
            degraded:
                values.filter(
                    item =>
                        item.degraded
                ).length,
            cached:
                values.filter(
                    item =>
                        item.cached
                ).length,
            latency:
                metricSummary(
                    values.map(
                        item =>
                            item.latency
                    )
                ),
            responseTime:
                metricSummary(
                    values.map(
                        item =>
                            item.response_time
                    )
                ),
            ingestionTime:
                metricSummary(
                    values.map(
                        item =>
                            item.ingestion_time
                    )
                ),
            queueTime:
                metricSummary(
                    values.map(
                        item =>
                            item.queue_time
                    )
                ),
            networkTime:
                metricSummary(
                    values.map(
                        item =>
                            item.network_time
                    )
                ),
            processingTime:
                metricSummary(
                    values.map(
                        item =>
                            item.processing_time
                    )
                ),
            providers:
                mapToSortedObject(
                    providers
                ),
            stages:
                mapToSortedObject(
                    stages
                ),
            statuses:
                mapToSortedObject(
                    statuses
                ),
            endpoints:
                mapToSortedObject(
                    endpoints
                ),
            protocols:
                mapToSortedObject(
                    protocols
                ),
            regions:
                mapToSortedObject(
                    regions
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
                                Array.isArray(payload.latency)
                                    ? payload.latency
                                    : (
                                        Array.isArray(payload.measurements)
                                            ? payload.measurements
                                            : []
                                    )
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

    class ProviderLatencyService extends EventTarget {
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
                    "Provider-latency service has been destroyed."
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
                    `provider-latency:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not break latency operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-provider-latency-${name}`,
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
                        "providers/latency",
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
                    "A latency measurement ID is required."
                );
            }

            try {
                const payload =
                    await this.context.api.get(
                        `providers/latency/${encodeURIComponent(normalizedId)}`,
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
                        item =>
                            item.id ===
                            normalizedId
                    );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async slow(threshold = DEFAULT_SLOW_THRESHOLD, parameters = {}, options = {}) {
            const normalizedThreshold =
                clampNumber(
                    threshold,
                    DEFAULT_SLOW_THRESHOLD,
                    0,
                    Number.MAX_SAFE_INTEGER
                );

            const result =
                await this.list(
                    {
                        ...parameters,
                        min_latency:
                            normalizedThreshold,
                        threshold:
                            normalizedThreshold
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.latency >=
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

        async degraded(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        degraded: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.degraded
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async timeouts(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        timeout: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.timeout
                );

            return {
                ...result,
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

        async byStage(stage, parameters = {}, options = {}) {
            const normalizedStage =
                normalizeText(stage);

            if (!normalizedStage) {
                throw new TypeError(
                    "A latency stage is required."
                );
            }

            return this.list(
                {
                    ...parameters,
                    stage:
                        normalizedStage
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
                measurements:
                    result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "providers/latency",
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
            ProviderLatencyService &&
            !existing.destroyed
        ) {
            context.providerLatency =
                existing;

            return existing;
        }

        if (
            context.providerLatency instanceof
            ProviderLatencyService &&
            !context.providerLatency.destroyed
        ) {
            return context.providerLatency;
        }

        const service =
            new ProviderLatencyService(
                context
            );

        context.providerLatency =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "providerLatency",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-provider-latency-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.providerLatency ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                ProviderLatencyService
            )
        ) {
            throw new Error(
                "Provider-latency service is unavailable."
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
                    "--stage="
                )
            ) {
                parameters.stage =
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
                    "--endpoint="
                )
            ) {
                parameters.endpoint =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--protocol="
                )
            ) {
                parameters.protocol =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--region="
                )
            ) {
                parameters.region =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--country="
                )
            ) {
                parameters.country =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--measurement="
                )
            ) {
                parameters.measurement =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--job="
                )
            ) {
                parameters.job =
                    argument.slice(6);
                continue;
            }

            if (
                argument.startsWith(
                    "--run="
                )
            ) {
                parameters.run =
                    argument.slice(6);
                continue;
            }

            if (
                argument.startsWith(
                    "--category="
                )
            ) {
                parameters.category =
                    argument.slice(11);
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
                    "--timeout="
                )
            ) {
                parameters.timeout =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--degraded="
                )
            ) {
                parameters.degraded =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--successful="
                )
            ) {
                parameters.successful =
                    argument.slice(13);
                continue;
            }

            if (
                argument.startsWith(
                    "--cached="
                )
            ) {
                parameters.cached =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-latency="
                )
            ) {
                parameters.min_latency =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-latency="
                )
            ) {
                parameters.max_latency =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-response-time="
                )
            ) {
                parameters.min_response_time =
                    argument.slice(20);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-response-time="
                )
            ) {
                parameters.max_response_time =
                    argument.slice(20);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-ingestion-time="
                )
            ) {
                parameters.min_ingestion_time =
                    argument.slice(21);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-ingestion-time="
                )
            ) {
                parameters.max_ingestion_time =
                    argument.slice(21);
                continue;
            }

            if (
                argument.startsWith(
                    "--threshold="
                )
            ) {
                parameters.threshold =
                    argument.slice(12);
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
            name: "provider-latency",
            aliases: [
                "providers-latency"
            ],
            category: "providers",
            description:
                "Inspect provider response and ingestion latency.",
            usage:
                "provider-latency [query] [limit] [--provider=ID] [--stage=STAGE] [--status=STATUS] [--endpoint=ENDPOINT] [--protocol=PROTOCOL] [--region=REGION] [--country=COUNTRY] [--measurement=ID] [--job=ID] [--run=ID] [--category=CATEGORY] [--type=TYPE] [--timeout=true|false] [--degraded=true|false] [--successful=true|false] [--cached=true|false] [--min-latency=MS] [--max-latency=MS] [--min-response-time=MS] [--max-response-time=MS] [--min-ingestion-time=MS] [--max-ingestion-time=MS] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const result =
                    await requireService(
                        context
                    ).list(
                        parseCommandArguments(
                            args
                        )
                    );

                return writeJSONValue(
                    writeJSON,
                    result
                );
            }
        },
        {
            name: "provider-latency-measurement",
            aliases: [
                "provider-latency-get"
            ],
            category: "providers",
            description:
                "Retrieve one provider latency measurement by ID.",
            usage:
                "provider-latency-measurement <id>",
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
                        "A latency measurement ID is required."
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
            name: "provider-latency-slow",
            aliases: [
                "slow-providers"
            ],
            category: "providers",
            description:
                "List latency measurements at or above a threshold.",
            usage:
                "provider-latency-slow [threshold-ms] [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                let threshold =
                    DEFAULT_SLOW_THRESHOLD;

                let filters =
                    args;

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
                    ).slow(
                        threshold,
                        parseCommandArguments(
                            filters
                        )
                    )
                );
            }
        },
        {
            name: "provider-latency-degraded",
            aliases: [
                "degraded-provider-latency"
            ],
            category: "providers",
            description:
                "List degraded provider latency measurements.",
            usage:
                "provider-latency-degraded [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).degraded(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "provider-latency-timeouts",
            aliases: [
                "provider-timeouts"
            ],
            category: "providers",
            description:
                "List provider latency timeout measurements.",
            usage:
                "provider-latency-timeouts [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).timeouts(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "provider-latency-summary",
            aliases: [
                "provider-performance-summary"
            ],
            category: "providers",
            description:
                "Summarize latency metrics, percentiles, stages, providers, endpoints, protocols, and statuses.",
            usage:
                "provider-latency-summary [filters]",
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
            name: "provider-latency-status",
            category: "providers",
            description:
                "Show provider-latency service status.",
            usage:
                "provider-latency-status",
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
        ProviderLatencyService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        percentile,
        metricSummary,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalProviderLatency =
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
