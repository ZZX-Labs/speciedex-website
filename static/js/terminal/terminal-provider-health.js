/*
========================================================================
Speciedex.org
Terminal Provider Health
========================================================================

Provider health aggregation, diagnostics, and monitoring for
SpeciedexTerminal.

Provides:

    • provider availability tracking
    • latency statistics
    • error-rate statistics
    • data freshness analysis
    • assertion and species coverage counts
    • provider overlap measurements
    • uptime calculations
    • weighted health scoring
    • configurable health thresholds
    • periodic monitoring
    • runtime event ingestion
    • library collection ingestion
    • filtering, sorting, summaries, and diagnostics
    • JSON and CSV export
    • terminal commands
    • clean teardown

Expected provider-related library collections may include:

    providers
    provider-health
    provider-errors
    provider-latency
    provider-statistics
    provider-assertions
    provider-species
    enabled-providers
    eligible-providers

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "ProviderHealth";

    const VERSION =
        "2.0.0";

    const DEFAULT_OPTIONS =
        Object.freeze({
            interval:
                60000,

            historyLimit:
                1000,

            staleAfter:
                24 * 60 * 60 * 1000,

            unhealthyAfter:
                72 * 60 * 60 * 1000,

            latencyWarning:
                1500,

            latencyCritical:
                5000,

            errorRateWarning:
                0.05,

            errorRateCritical:
                0.20,

            uptimeWarning:
                0.98,

            uptimeCritical:
                0.90,

            minimumAssertions:
                1,

            scoreWeights: {
                availability:
                    0.30,

                latency:
                    0.15,

                errors:
                    0.20,

                freshness:
                    0.20,

                coverage:
                    0.10,

                overlap:
                    0.05
            },

            autoStart:
                false,

            emitNotifications:
                true
        });

    const HEALTH_STATES =
        Object.freeze([
            "healthy",
            "degraded",
            "warning",
            "critical",
            "offline",
            "unknown"
        ]);

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

    function parseNumber(
        value,
        fallback = 0
    ) {
        const parsed =
            Number(value);

        return Number.isFinite(
            parsed
        )
            ? parsed
            : fallback;
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

    function normalizeProviderID(
        value
    ) {
        const normalized =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase()
                .replace(/\s+/g, "-");

        if (!normalized) {
            return "unknown";
        }

        return normalized;
    }

    function normalizeText(
        value
    ) {
        return String(
            value ?? ""
        ).trim();
    }

    function normalizeTimestamp(
        value
    ) {
        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return null;
        }

        if (
            value instanceof
            Date
        ) {
            const timestamp =
                value.getTime();

            return Number.isFinite(
                timestamp
            )
                ? timestamp
                : null;
        }

        if (
            typeof value ===
            "number"
        ) {
            return Number.isFinite(
                value
            )
                ? value
                : null;
        }

        const timestamp =
            Date.parse(
                value
            );

        return Number.isFinite(
            timestamp
        )
            ? timestamp
            : null;
    }

    function firstValue(
        record,
        fields
    ) {
        for (const field of fields) {
            const value =
                record?.[
                    field
                ];

            if (
                value !== undefined &&
                value !== null &&
                value !== ""
            ) {
                return value;
            }
        }

        return null;
    }

    function mean(
        values
    ) {
        const filtered =
            values.filter(
                value =>
                    Number.isFinite(
                        value
                    )
            );

        if (!filtered.length) {
            return null;
        }

        return filtered.reduce(
            (
                total,
                value
            ) =>
                total +
                value,
            0
        ) /
        filtered.length;
    }

    function percentile(
        values,
        percentileValue
    ) {
        const filtered =
            values
                .filter(
                    value =>
                        Number.isFinite(
                            value
                        )
                )
                .sort(
                    (
                        left,
                        right
                    ) =>
                        left -
                        right
                );

        if (!filtered.length) {
            return null;
        }

        const position =
            (
                filtered.length -
                1
            ) *
            percentileValue;

        const lower =
            Math.floor(
                position
            );

        const upper =
            Math.ceil(
                position
            );

        if (
            lower ===
            upper
        ) {
            return filtered[
                lower
            ];
        }

        const weight =
            position -
            lower;

        return (
            filtered[
                lower
            ] *
            (
                1 -
                weight
            )
        ) +
        (
            filtered[
                upper
            ] *
            weight
        );
    }

    function formatDuration(
        milliseconds
    ) {
        const value =
            Math.max(
                0,
                Number(
                    milliseconds
                ) ||
                0
            );

        if (value < 1000) {
            return `${Math.round(value)}ms`;
        }

        if (
            value <
            60 * 1000
        ) {
            return `${(
                value /
                1000
            ).toFixed(1)}s`;
        }

        if (
            value <
            60 * 60 * 1000
        ) {
            return `${(
                value /
                (
                    60 *
                    1000
                )
            ).toFixed(1)}m`;
        }

        if (
            value <
            24 * 60 * 60 * 1000
        ) {
            return `${(
                value /
                (
                    60 *
                    60 *
                    1000
                )
            ).toFixed(1)}h`;
        }

        return `${(
            value /
            (
                24 *
                60 *
                60 *
                1000
            )
        ).toFixed(1)}d`;
    }

    function healthRank(
        state
    ) {
        return {
            healthy:
                0,

            degraded:
                1,

            warning:
                2,

            critical:
                3,

            offline:
                4,

            unknown:
                5
        }[
            state
        ] ??
        6;
    }

    function escapeCSV(
        value
    ) {
        const text =
            String(
                value ?? ""
            );

        if (
            /[",\n\r]/.test(
                text
            )
        ) {
            return `"${text.replace(/"/g, '""')}"`;
        }

        return text;
    }

    /*
    ==========================================================================
    Provider Health Service
    ==========================================================================
    */

    class ProviderHealthService
        extends EventTarget {
        constructor(
            context,
            options = {}
        ) {
            super();

            this.context =
                context;

            this.options = {
                ...DEFAULT_OPTIONS,
                ...options,

                scoreWeights: {
                    ...DEFAULT_OPTIONS.scoreWeights,
                    ...(options.scoreWeights || {})
                }
            };

            this.providers =
                new Map();

            this.samples =
                new Map();

            this.errors =
                new Map();

            this.latencies =
                new Map();

            this.assertions =
                new Map();

            this.species =
                new Map();

            this.overlap =
                new Map();

            this.history =
                [];

            this.timer =
                0;

            this.running =
                false;

            this.destroyed =
                false;

            this.boundHandlers =
                [];

            this.ingestLibrary();
            this.bindRuntimeEvents();

            if (
                this.options.autoStart
            ) {
                this.start();
            }
        }

        /*
        ======================================================================
        Provider Registration
        ======================================================================
        */

        ensureProvider(
            provider,
            metadata = {}
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            if (
                !this.providers.has(
                    id
                )
            ) {
                this.providers.set(
                    id,
                    {
                        id,

                        name:
                            normalizeText(
                                metadata.name ||
                                provider
                            ) ||
                            id,

                        enabled:
                            metadata.enabled !==
                            false,

                        eligible:
                            metadata.eligible !==
                            false,

                        endpoint:
                            normalizeText(
                                metadata.endpoint ||
                                metadata.url ||
                                ""
                            ),

                        documentation:
                            normalizeText(
                                metadata.documentation ||
                                metadata.docs ||
                                ""
                            ),

                        createdAt:
                            new Date().toISOString(),

                        updatedAt:
                            new Date().toISOString(),

                        metadata: {
                            ...metadata
                        }
                    }
                );
            } else if (
                metadata &&
                typeof metadata ===
                "object"
            ) {
                const current =
                    this.providers.get(
                        id
                    );

                current.name =
                    normalizeText(
                        metadata.name ||
                        current.name
                    );

                current.enabled =
                    metadata.enabled ??
                    current.enabled;

                current.eligible =
                    metadata.eligible ??
                    current.eligible;

                current.endpoint =
                    normalizeText(
                        metadata.endpoint ||
                        metadata.url ||
                        current.endpoint
                    );

                current.documentation =
                    normalizeText(
                        metadata.documentation ||
                        metadata.docs ||
                        current.documentation
                    );

                current.updatedAt =
                    new Date().toISOString();

                current.metadata = {
                    ...current.metadata,
                    ...metadata
                };
            }

            return this.providers.get(
                id
            );
        }

        registerProvider(
            provider,
            metadata = {}
        ) {
            const result =
                this.ensureProvider(
                    provider,
                    metadata
                );

            this.emit(
                "provider",
                {
                    provider:
                        result
                }
            );

            return result;
        }

        /*
        ======================================================================
        Sample Recording
        ======================================================================
        */

        recordSample(
            provider,
            sample = {}
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id,
                sample
            );

            const timestamp =
                normalizeTimestamp(
                    sample.timestamp ||
                    sample.checkedAt ||
                    sample.updatedAt
                ) ??
                Date.now();

            const success =
                sample.success ??
                sample.ok ??
                (
                    String(
                        sample.status ||
                        ""
                    ).toLowerCase() ===
                    "healthy"
                );

            const statusCode =
                parseNumber(
                    sample.statusCode ||
                    sample.status_code ||
                    sample.httpStatus,
                    success
                        ? 200
                        : 0
                );

            const latency =
                parseNumber(
                    sample.latency ||
                    sample.latencyMs ||
                    sample.latency_ms ||
                    sample.duration,
                    null
                );

            const record = {
                provider:
                    id,

                timestamp,

                success:
                    Boolean(
                        success
                    ),

                statusCode,

                latency,

                assertions:
                    parseNumber(
                        sample.assertions ||
                        sample.sourceAssertions,
                        null
                    ),

                species:
                    parseNumber(
                        sample.species ||
                        sample.speciesCount,
                        null
                    ),

                lastUpdated:
                    normalizeTimestamp(
                        sample.lastUpdated ||
                        sample.last_updated ||
                        sample.freshnessTimestamp
                    ),

                error:
                    sample.error
                        ? normalizeText(
                            sample.error.message ||
                            sample.error
                        )
                        : "",

                metadata: {
                    ...sample
                }
            };

            if (
                !this.samples.has(
                    id
                )
            ) {
                this.samples.set(
                    id,
                    []
                );
            }

            this.samples.get(
                id
            ).push(
                record
            );

            this.samples.set(
                id,
                this.samples.get(
                    id
                ).slice(
                    -this.options.historyLimit
                )
            );

            if (
                latency !==
                null
            ) {
                this.recordLatency(
                    id,
                    latency,
                    timestamp,
                    false
                );
            }

            if (
                !record.success ||
                record.error
            ) {
                this.recordError(
                    id,
                    record.error ||
                    `Provider check failed with status ${statusCode}.`,
                    timestamp,
                    false
                );
            }

            if (
                record.assertions !==
                null
            ) {
                this.assertions.set(
                    id,
                    record.assertions
                );
            }

            if (
                record.species !==
                null
            ) {
                this.species.set(
                    id,
                    record.species
                );
            }

            const health =
                this.evaluate(
                    id
                );

            this.archive(
                health
            );

            this.emit(
                "sample",
                {
                    sample:
                        record,

                    health
                }
            );

            this.notifyTransition(
                health
            );

            return health;
        }

        recordLatency(
            provider,
            latency,
            timestamp =
                Date.now(),
            emit =
                true
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id
            );

            const value =
                parseNumber(
                    latency,
                    null
                );

            if (
                value ===
                null
            ) {
                return null;
            }

            if (
                !this.latencies.has(
                    id
                )
            ) {
                this.latencies.set(
                    id,
                    []
                );
            }

            const record = {
                timestamp:
                    normalizeTimestamp(
                        timestamp
                    ) ??
                    Date.now(),

                value
            };

            this.latencies.get(
                id
            ).push(
                record
            );

            this.latencies.set(
                id,
                this.latencies.get(
                    id
                ).slice(
                    -this.options.historyLimit
                )
            );

            if (emit) {
                this.emit(
                    "latency",
                    {
                        provider:
                            id,

                        latency:
                            record
                    }
                );
            }

            return record;
        }

        recordError(
            provider,
            error,
            timestamp =
                Date.now(),
            emit =
                true
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id
            );

            if (
                !this.errors.has(
                    id
                )
            ) {
                this.errors.set(
                    id,
                    []
                );
            }

            const record = {
                timestamp:
                    normalizeTimestamp(
                        timestamp
                    ) ??
                    Date.now(),

                message:
                    error instanceof
                    Error
                        ? error.message
                        : normalizeText(
                            error
                        ),

                name:
                    error instanceof
                    Error
                        ? error.name
                        : "Error"
            };

            this.errors.get(
                id
            ).push(
                record
            );

            this.errors.set(
                id,
                this.errors.get(
                    id
                ).slice(
                    -this.options.historyLimit
                )
            );

            if (emit) {
                this.emit(
                    "error",
                    {
                        provider:
                            id,

                        error:
                            record
                    }
                );
            }

            return record;
        }

        setAssertions(
            provider,
            count
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id
            );

            this.assertions.set(
                id,
                Math.max(
                    0,
                    parseNumber(
                        count,
                        0
                    )
                )
            );

            return this.assertions.get(
                id
            );
        }

        setSpecies(
            provider,
            count
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id
            );

            this.species.set(
                id,
                Math.max(
                    0,
                    parseNumber(
                        count,
                        0
                    )
                )
            );

            return this.species.get(
                id
            );
        }

        setOverlap(
            provider,
            value
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            this.ensureProvider(
                id
            );

            this.overlap.set(
                id,
                clamp(
                    parseNumber(
                        value,
                        0
                    ),
                    0,
                    1
                )
            );

            return this.overlap.get(
                id
            );
        }

        /*
        ======================================================================
        Health Calculation
        ======================================================================
        */

        evaluate(
            provider
        ) {
            const id =
                normalizeProviderID(
                    provider
                );

            const metadata =
                this.ensureProvider(
                    id
                );

            const samples =
                this.samples.get(
                    id
                ) ||
                [];

            const latencyRecords =
                this.latencies.get(
                    id
                ) ||
                [];

            const errorRecords =
                this.errors.get(
                    id
                ) ||
                [];

            const now =
                Date.now();

            const recentWindow =
                now -
                this.options.unhealthyAfter;

            const recentSamples =
                samples.filter(
                    sample =>
                        sample.timestamp >=
                        recentWindow
                );

            const recentErrors =
                errorRecords.filter(
                    error =>
                        error.timestamp >=
                        recentWindow
                );

            const recentLatencies =
                latencyRecords
                    .filter(
                        record =>
                            record.timestamp >=
                            recentWindow
                    )
                    .map(
                        record =>
                            record.value
                    );

            const successful =
                recentSamples.filter(
                    sample =>
                        sample.success
                ).length;

            const uptime =
                recentSamples.length
                    ? successful /
                    recentSamples.length
                    : null;

            const errorRate =
                recentSamples.length
                    ? Math.min(
                        1,
                        recentErrors.length /
                        recentSamples.length
                    )
                    : recentErrors.length
                        ? 1
                        : null;

            const lastSample =
                samples.length
                    ? samples[
                        samples.length -
                        1
                    ]
                    : null;

            const freshnessTimestamp =
                lastSample?.lastUpdated ??
                lastSample?.timestamp ??
                null;

            const age =
                freshnessTimestamp ===
                null
                    ? null
                    : now -
                    freshnessTimestamp;

            const latencyAverage =
                mean(
                    recentLatencies
                );

            const latencyP50 =
                percentile(
                    recentLatencies,
                    0.50
                );

            const latencyP95 =
                percentile(
                    recentLatencies,
                    0.95
                );

            const assertions =
                this.assertions.get(
                    id
                ) ??
                lastSample?.assertions ??
                0;

            const species =
                this.species.get(
                    id
                ) ??
                lastSample?.species ??
                0;

            const overlap =
                this.overlap.get(
                    id
                ) ??
                0;

            const availabilityScore =
                uptime ===
                    null
                    ? 0.5
                    : clamp(
                        uptime,
                        0,
                        1
                    );

            const latencyScore =
                latencyAverage ===
                    null
                    ? 0.5
                    : latencyAverage <=
                        this.options.latencyWarning
                        ? 1
                        : latencyAverage >=
                            this.options.latencyCritical
                            ? 0
                            : 1 -
                                (
                                    (
                                        latencyAverage -
                                        this.options.latencyWarning
                                    ) /
                                    (
                                        this.options.latencyCritical -
                                        this.options.latencyWarning
                                    )
                                );

            const errorScore =
                errorRate ===
                    null
                    ? 0.5
                    : errorRate <=
                        this.options.errorRateWarning
                        ? 1
                        : errorRate >=
                            this.options.errorRateCritical
                            ? 0
                            : 1 -
                                (
                                    (
                                        errorRate -
                                        this.options.errorRateWarning
                                    ) /
                                    (
                                        this.options.errorRateCritical -
                                        this.options.errorRateWarning
                                    )
                                );

            const freshnessScore =
                age ===
                    null
                    ? 0.5
                    : age <=
                        this.options.staleAfter
                        ? 1
                        : age >=
                            this.options.unhealthyAfter
                            ? 0
                            : 1 -
                                (
                                    (
                                        age -
                                        this.options.staleAfter
                                    ) /
                                    (
                                        this.options.unhealthyAfter -
                                        this.options.staleAfter
                                    )
                                );

            const coverageScore =
                assertions >=
                    this.options.minimumAssertions
                    ? clamp(
                        Math.log10(
                            assertions +
                            1
                        ) /
                        6,
                        0,
                        1
                    )
                    : 0;

            const overlapScore =
                clamp(
                    overlap,
                    0,
                    1
                );

            const weights =
                this.options.scoreWeights;

            const weightTotal =
                Object.values(
                    weights
                ).reduce(
                    (
                        total,
                        value
                    ) =>
                        total +
                        value,
                    0
                ) ||
                1;

            const score =
                (
                    availabilityScore *
                        weights.availability +
                    latencyScore *
                        weights.latency +
                    errorScore *
                        weights.errors +
                    freshnessScore *
                        weights.freshness +
                    coverageScore *
                        weights.coverage +
                    overlapScore *
                        weights.overlap
                ) /
                weightTotal *
                100;

            let state =
                "unknown";

            if (
                metadata.enabled ===
                false
            ) {
                state =
                    "offline";
            } else if (
                lastSample &&
                lastSample.success ===
                false &&
                age !==
                    null &&
                age >=
                    this.options.unhealthyAfter
            ) {
                state =
                    "offline";
            } else if (
                score >=
                85
            ) {
                state =
                    "healthy";
            } else if (
                score >=
                70
            ) {
                state =
                    "degraded";
            } else if (
                score >=
                50
            ) {
                state =
                    "warning";
            } else if (
                samples.length ||
                errors.length
            ) {
                state =
                    "critical";
            }

            return {
                provider:
                    id,

                name:
                    metadata.name,

                state,

                score:
                    Number(
                        score.toFixed(
                            2
                        )
                    ),

                enabled:
                    metadata.enabled,

                eligible:
                    metadata.eligible,

                endpoint:
                    metadata.endpoint,

                lastCheck:
                    lastSample
                        ? new Date(
                            lastSample.timestamp
                        ).toISOString()
                        : null,

                lastUpdated:
                    freshnessTimestamp ===
                        null
                        ? null
                        : new Date(
                            freshnessTimestamp
                        ).toISOString(),

                age,

                freshness:
                    age ===
                        null
                        ? "unknown"
                        : formatDuration(
                            age
                        ),

                uptime,

                availability:
                    uptime ===
                        null
                        ? null
                        : Number(
                            (
                                uptime *
                                100
                            ).toFixed(
                                3
                            )
                        ),

                errorRate,

                errors:
                    recentErrors.length,

                samples:
                    recentSamples.length,

                latency: {
                    average:
                        latencyAverage,

                    p50:
                        latencyP50,

                    p95:
                        latencyP95,

                    samples:
                        recentLatencies.length
                },

                assertions,

                species,

                overlap,

                components: {
                    availability:
                        availabilityScore,

                    latency:
                        latencyScore,

                    errors:
                        errorScore,

                    freshness:
                        freshnessScore,

                    coverage:
                        coverageScore,

                    overlap:
                        overlapScore
                },

                metadata: {
                    ...metadata.metadata
                }
            };
        }

        evaluateAll() {
            return [
                ...this.providers.keys()
            ]
                .map(
                    provider =>
                        this.evaluate(
                            provider
                        )
                )
                .sort(
                    (
                        left,
                        right
                    ) => {
                        const stateDifference =
                            healthRank(
                                left.state
                            ) -
                            healthRank(
                                right.state
                            );

                        if (
                            stateDifference
                        ) {
                            return stateDifference;
                        }

                        return (
                            right.score -
                            left.score
                        );
                    }
                );
        }

        summary() {
            const providers =
                this.evaluateAll();

            const byState =
                Object.fromEntries(
                    HEALTH_STATES.map(
                        state => [
                            state,
                            0
                        ]
                    )
                );

            for (const provider of providers) {
                byState[
                    provider.state
                ] =
                    (
                        byState[
                            provider.state
                        ] ||
                        0
                    ) +
                    1;
            }

            const scores =
                providers
                    .map(
                        provider =>
                            provider.score
                    )
                    .filter(
                        score =>
                            Number.isFinite(
                                score
                            )
                    );

            const latencies =
                providers
                    .map(
                        provider =>
                            provider.latency.average
                    )
                    .filter(
                        latency =>
                            Number.isFinite(
                                latency
                            )
                    );

            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                providers:
                    providers.length,

                enabled:
                    providers.filter(
                        provider =>
                            provider.enabled
                    ).length,

                eligible:
                    providers.filter(
                        provider =>
                            provider.eligible
                    ).length,

                byState,

                averageScore:
                    mean(
                        scores
                    ),

                averageLatency:
                    mean(
                        latencies
                    ),

                assertions:
                    providers.reduce(
                        (
                            total,
                            provider
                        ) =>
                            total +
                            provider.assertions,
                        0
                    ),

                species:
                    providers.reduce(
                        (
                            total,
                            provider
                        ) =>
                            total +
                            provider.species,
                        0
                    ),

                providersData:
                    providers
            };
        }

        /*
        ======================================================================
        Library Ingestion
        ======================================================================
        */

        ingestLibrary() {
            const library =
                this.context.library;

            if (!library) {
                return;
            }

            const providerCollections = [
                "providers",
                "enabled-providers",
                "eligible-providers",
                "provider-statistics",
                "provider-health"
            ];

            for (const collection of providerCollections) {
                const records =
                    library.get?.(
                        collection
                    ) ||
                    [];

                if (!Array.isArray(records)) {
                    continue;
                }

                for (const record of records) {
                    const provider =
                        firstValue(
                            record,
                            [
                                "provider",
                                "provider_id",
                                "providerId",
                                "id",
                                "name",
                                "key"
                            ]
                        );

                    if (!provider) {
                        continue;
                    }

                    this.ensureProvider(
                        provider,
                        record
                    );

                    if (
                        collection ===
                        "provider-health"
                    ) {
                        this.recordSample(
                            provider,
                            record
                        );
                    }

                    const assertions =
                        firstValue(
                            record,
                            [
                                "assertions",
                                "source_assertions",
                                "sourceAssertions"
                            ]
                        );

                    if (
                        assertions !==
                        null
                    ) {
                        this.setAssertions(
                            provider,
                            assertions
                        );
                    }

                    const species =
                        firstValue(
                            record,
                            [
                                "species",
                                "species_count",
                                "speciesCount"
                            ]
                        );

                    if (
                        species !==
                        null
                    ) {
                        this.setSpecies(
                            provider,
                            species
                        );
                    }

                    const overlap =
                        firstValue(
                            record,
                            [
                                "overlap",
                                "overlap_ratio",
                                "overlapRatio"
                            ]
                        );

                    if (
                        overlap !==
                        null
                    ) {
                        this.setOverlap(
                            provider,
                            overlap
                        );
                    }
                }
            }

            const latencyRecords =
                library.get?.(
                    "provider-latency"
                ) ||
                [];

            for (const record of latencyRecords) {
                const provider =
                    firstValue(
                        record,
                        [
                            "provider",
                            "provider_id",
                            "providerId",
                            "id"
                        ]
                    );

                if (!provider) {
                    continue;
                }

                this.recordLatency(
                    provider,
                    firstValue(
                        record,
                        [
                            "latency",
                            "latency_ms",
                            "latencyMs",
                            "duration"
                        ]
                    ),
                    firstValue(
                        record,
                        [
                            "timestamp",
                            "checkedAt",
                            "date"
                        ]
                    ),
                    false
                );
            }

            const errorRecords =
                library.get?.(
                    "provider-errors"
                ) ||
                [];

            for (const record of errorRecords) {
                const provider =
                    firstValue(
                        record,
                        [
                            "provider",
                            "provider_id",
                            "providerId",
                            "id"
                        ]
                    );

                if (!provider) {
                    continue;
                }

                this.recordError(
                    provider,
                    firstValue(
                        record,
                        [
                            "error",
                            "message",
                            "detail"
                        ]
                    ) ||
                    "Provider error",
                    firstValue(
                        record,
                        [
                            "timestamp",
                            "date",
                            "occurredAt"
                        ]
                    ),
                    false
                );
            }
        }

        /*
        ======================================================================
        Runtime Event Ingestion
        ======================================================================
        */

        bindEvent(
            target,
            name,
            handler
        ) {
            if (
                !target ||
                typeof target.addEventListener !==
                "function"
            ) {
                return;
            }

            target.addEventListener(
                name,
                handler
            );

            this.boundHandlers.push({
                target,
                name,
                handler
            });
        }

        bindRuntimeEvents() {
            const sampleHandler =
                event => {
                    const detail =
                        event.detail ||
                        {};

                    const provider =
                        detail.provider ||
                        detail.id ||
                        detail.name;

                    if (provider) {
                        this.recordSample(
                            provider,
                            detail
                        );
                    }
                };

            const latencyHandler =
                event => {
                    const detail =
                        event.detail ||
                        {};

                    if (detail.provider) {
                        this.recordLatency(
                            detail.provider,
                            detail.latency ||
                            detail.value ||
                            detail.duration,
                            detail.timestamp
                        );
                    }
                };

            const errorHandler =
                event => {
                    const detail =
                        event.detail ||
                        {};

                    if (detail.provider) {
                        this.recordError(
                            detail.provider,
                            detail.error ||
                            detail.message ||
                            "Provider error",
                            detail.timestamp
                        );
                    }
                };

            const libraryHandler =
                event => {
                    const detail =
                        event.detail ||
                        {};

                    if (
                        String(
                            detail.collection ||
                            ""
                        ).startsWith(
                            "provider"
                        ) ||
                        [
                            "providers",
                            "enabled-providers",
                            "eligible-providers"
                        ].includes(
                            detail.collection
                        )
                    ) {
                        this.ingestLibrary();
                    }
                };

            this.bindEvent(
                document,
                "speciedex:provider-health",
                sampleHandler
            );

            this.bindEvent(
                document,
                "speciedex:provider-latency",
                latencyHandler
            );

            this.bindEvent(
                document,
                "speciedex:provider-error",
                errorHandler
            );

            this.bindEvent(
                document,
                "speciedex:terminal-library-updated",
                libraryHandler
            );
        }

        /*
        ======================================================================
        Monitoring
        ======================================================================
        */

        async run(
            parameters = {}
        ) {
            if (
                parameters.refresh !==
                false
            ) {
                this.ingestLibrary();
            }

            const provider =
                parameters.provider ||
                parameters.args?.[0] ||
                null;

            if (provider) {
                return this.evaluate(
                    provider
                );
            }

            return this.summary();
        }

        async checkProvider(
            provider,
            options = {}
        ) {
            const metadata =
                this.ensureProvider(
                    provider
                );

            if (!metadata.endpoint) {
                throw new Error(
                    `Provider "${metadata.id}" has no configured endpoint.`
                );
            }

            const controller =
                new AbortController();

            const timeout =
                window.setTimeout(
                    () =>
                        controller.abort(),
                    parseNumber(
                        options.timeout,
                        15000
                    )
                );

            const started =
                performance.now();

            try {
                const response =
                    await fetch(
                        metadata.endpoint,
                        {
                            method:
                                options.method ||
                                "HEAD",

                            cache:
                                "no-store",

                            signal:
                                controller.signal,

                            headers:
                                options.headers ||
                                {}
                        }
                    );

                const latency =
                    performance.now() -
                    started;

                return this.recordSample(
                    metadata.id,
                    {
                        success:
                            response.ok,

                        statusCode:
                            response.status,

                        latency,

                        timestamp:
                            Date.now(),

                        lastUpdated:
                            response.headers.get(
                                "last-modified"
                            ) ||
                            Date.now()
                    }
                );
            } catch (error) {
                const latency =
                    performance.now() -
                    started;

                return this.recordSample(
                    metadata.id,
                    {
                        success:
                            false,

                        latency,

                        timestamp:
                            Date.now(),

                        error
                    }
                );
            } finally {
                window.clearTimeout(
                    timeout
                );
            }
        }

        async checkAll(
            options = {}
        ) {
            const providers =
                [
                    ...this.providers.values()
                ].filter(
                    provider =>
                        provider.enabled &&
                        provider.endpoint
                );

            const concurrency =
                clamp(
                    parseNumber(
                        options.concurrency,
                        4
                    ),
                    1,
                    16
                );

            const results =
                [];

            let index =
                0;

            const worker =
                async () => {
                    while (
                        index <
                        providers.length
                    ) {
                        const current =
                            providers[
                                index++
                            ];

                        results.push(
                            await this.checkProvider(
                                current.id,
                                options
                            )
                        );
                    }
                };

            await Promise.all(
                Array.from(
                    {
                        length:
                            Math.min(
                                concurrency,
                                providers.length
                            )
                    },
                    () =>
                        worker()
                )
            );

            return results;
        }

        start(
            interval =
                this.options.interval
        ) {
            if (this.running) {
                return false;
            }

            this.running =
                true;

            const delay =
                Math.max(
                    5000,
                    parseNumber(
                        interval,
                        this.options.interval
                    )
                );

            const tick =
                async () => {
                    if (
                        !this.running ||
                        this.destroyed
                    ) {
                        return;
                    }

                    try {
                        await this.checkAll();
                    } catch (error) {
                        this.emit(
                            "monitor-error",
                            {
                                error
                            }
                        );
                    }
                };

            tick();

            this.timer =
                window.setInterval(
                    tick,
                    delay
                );

            this.emit(
                "monitor-start",
                {
                    interval:
                        delay
                }
            );

            return true;
        }

        stop() {
            if (!this.running) {
                return false;
            }

            this.running =
                false;

            if (this.timer) {
                window.clearInterval(
                    this.timer
                );

                this.timer =
                    0;
            }

            this.emit(
                "monitor-stop",
                {}
            );

            return true;
        }

        /*
        ======================================================================
        History and Notifications
        ======================================================================
        */

        archive(
            health
        ) {
            const entry = {
                timestamp:
                    new Date().toISOString(),

                health
            };

            this.history.push(
                entry
            );

            this.history =
                this.history.slice(
                    -this.options.historyLimit
                );

            return entry;
        }

        notifyTransition(
            health
        ) {
            if (
                !this.options.emitNotifications ||
                !this.context.notifications
            ) {
                return;
            }

            const previous =
                this.history.length >=
                    2
                    ? this.history[
                        this.history.length -
                        2
                    ]?.health
                    : null;

            if (
                previous?.provider !==
                    health.provider ||
                previous?.state ===
                    health.state
            ) {
                return;
            }

            if (
                [
                    "warning",
                    "critical",
                    "offline"
                ].includes(
                    health.state
                )
            ) {
                const type =
                    health.state ===
                    "warning"
                        ? "warning"
                        : "error";

                this.context.notifications.notify(
                    `${health.name} provider health changed to ${health.state}.`,
                    type,
                    health.state ===
                        "offline"
                        ? 0
                        : 7000,
                    {
                        title:
                            "Provider Health",

                        priority:
                            health.state ===
                                "offline"
                                ? "urgent"
                                : "high",

                        persistent:
                            health.state ===
                            "offline"
                    }
                );
            }
        }

        /*
        ======================================================================
        Filtering and Export
        ======================================================================
        */

        list(
            options = {}
        ) {
            const state =
                options.state
                    ? String(
                        options.state
                    ).toLowerCase()
                    : null;

            const enabled =
                options.enabled;

            const eligible =
                options.eligible;

            const contains =
                normalizeText(
                    options.contains ||
                    options.text
                ).toLowerCase();

            const minimumScore =
                options.minimumScore !==
                    undefined
                    ? parseNumber(
                        options.minimumScore,
                        0
                    )
                    : null;

            const maximumScore =
                options.maximumScore !==
                    undefined
                    ? parseNumber(
                        options.maximumScore,
                        100
                    )
                    : null;

            let results =
                this.evaluateAll()
                    .filter(
                        provider =>
                            (
                                !state ||
                                provider.state ===
                                state
                            ) &&
                            (
                                enabled ===
                                    undefined ||
                                provider.enabled ===
                                enabled
                            ) &&
                            (
                                eligible ===
                                    undefined ||
                                provider.eligible ===
                                eligible
                            ) &&
                            (
                                !contains ||
                                [
                                    provider.provider,
                                    provider.name,
                                    provider.endpoint,
                                    provider.state
                                ]
                                    .join(" ")
                                    .toLowerCase()
                                    .includes(
                                        contains
                                    )
                            ) &&
                            (
                                minimumScore ===
                                    null ||
                                provider.score >=
                                minimumScore
                            ) &&
                            (
                                maximumScore ===
                                    null ||
                                provider.score <=
                                maximumScore
                            )
                    );

            const sort =
                String(
                    options.sort ||
                    "state"
                );

            results.sort(
                (
                    left,
                    right
                ) => {
                    switch (sort) {
                        case "score":
                            return (
                                right.score -
                                left.score
                            );

                        case "latency":
                            return (
                                (
                                    left.latency.average ??
                                    Number.POSITIVE_INFINITY
                                ) -
                                (
                                    right.latency.average ??
                                    Number.POSITIVE_INFINITY
                                )
                            );

                        case "name":
                            return left.name.localeCompare(
                                right.name
                            );

                        case "state":
                        default:
                            return (
                                healthRank(
                                    left.state
                                ) -
                                healthRank(
                                    right.state
                                )
                            );
                    }
                }
            );

            const limit =
                clamp(
                    parseNumber(
                        options.limit,
                        results.length ||
                        1
                    ),
                    1,
                    10000
                );

            return results.slice(
                0,
                limit
            );
        }

        exportJSON() {
            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                options:
                    this.options,

                summary:
                    this.summary(),

                history:
                    this.history
            };
        }

        exportCSV() {
            const rows =
                this.evaluateAll();

            const header = [
                "provider",
                "name",
                "state",
                "score",
                "enabled",
                "eligible",
                "availability_percent",
                "error_rate",
                "latency_average_ms",
                "latency_p50_ms",
                "latency_p95_ms",
                "assertions",
                "species",
                "overlap",
                "last_check",
                "last_updated",
                "freshness"
            ];

            const lines = [
                header.join(",")
            ];

            for (const provider of rows) {
                lines.push(
                    [
                        provider.provider,
                        provider.name,
                        provider.state,
                        provider.score,
                        provider.enabled,
                        provider.eligible,
                        provider.availability,
                        provider.errorRate,
                        provider.latency.average,
                        provider.latency.p50,
                        provider.latency.p95,
                        provider.assertions,
                        provider.species,
                        provider.overlap,
                        provider.lastCheck,
                        provider.lastUpdated,
                        provider.freshness
                    ]
                        .map(
                            escapeCSV
                        )
                        .join(",")
                );
            }

            return lines.join(
                "\n"
            );
        }

        status() {
            return {
                version:
                    VERSION,

                running:
                    this.running,

                interval:
                    this.options.interval,

                providers:
                    this.providers.size,

                samples:
                    [
                        ...this.samples.values()
                    ].reduce(
                        (
                            total,
                            values
                        ) =>
                            total +
                            values.length,
                        0
                    ),

                latencySamples:
                    [
                        ...this.latencies.values()
                    ].reduce(
                        (
                            total,
                            values
                        ) =>
                            total +
                            values.length,
                        0
                    ),

                errors:
                    [
                        ...this.errors.values()
                    ].reduce(
                        (
                            total,
                            values
                        ) =>
                            total +
                            values.length,
                        0
                    ),

                history:
                    this.history.length,

                summary:
                    this.summary()
            };
        }

        /*
        ======================================================================
        Events and Teardown
        ======================================================================
        */

        emit(
            type,
            detail = {}
        ) {
            this.dispatchEvent(
                new CustomEvent(
                    type,
                    {
                        detail
                    }
                )
            );

            this.context.events?.emit?.(
                `provider-health:${type}`,
                detail
            );

            this.context.root?.
                dispatchEvent?.(
                    new CustomEvent(
                        `speciedex:terminal-provider-health-${type}`,
                        {
                            bubbles:
                                true,

                            detail
                        }
                    )
                );

            document.dispatchEvent(
                new CustomEvent(
                    `speciedex:terminal-provider-health-${type}`,
                    {
                        detail
                    }
                )
            );
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.stop();

            for (const binding of this.boundHandlers) {
                binding.target.removeEventListener(
                    binding.name,
                    binding.handler
                );
            }

            this.boundHandlers =
                [];

            this.providers.clear();
            this.samples.clear();
            this.errors.clear();
            this.latencies.clear();
            this.assertions.clear();
            this.species.clear();
            this.overlap.clear();

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
            context.providerHealth instanceof
            ProviderHealthService
        ) {
            return context.providerHealth;
        }

        const root =
            context.root;

        const service =
            new ProviderHealthService(
                context,
                {
                    interval:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderHealthInterval,
                            DEFAULT_OPTIONS.interval
                        ),

                    historyLimit:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderHealthHistory,
                            DEFAULT_OPTIONS.historyLimit
                        ),

                    staleAfter:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderStaleAfter,
                            DEFAULT_OPTIONS.staleAfter
                        ),

                    unhealthyAfter:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderUnhealthyAfter,
                            DEFAULT_OPTIONS.unhealthyAfter
                        ),

                    latencyWarning:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderLatencyWarning,
                            DEFAULT_OPTIONS.latencyWarning
                        ),

                    latencyCritical:
                        parseNumber(
                            root?.
                                dataset.
                                terminalProviderLatencyCritical,
                            DEFAULT_OPTIONS.latencyCritical
                        ),

                    autoStart:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderHealthAutoStart,
                            false
                        ),

                    emitNotifications:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderHealthNotifications,
                            true
                        )
                }
            );

        context.providerHealth =
            service;

        context.providerhealth =
            service;

        context.registerService?.(
            "provider-health",
            service
        );

        return service;
    }

    /*
    ==========================================================================
    Commands
    ==========================================================================
    */

    function download(
        content,
        filename,
        mime
    ) {
        const blob =
            new Blob(
                [
                    content
                ],
                {
                    type:
                        mime
                }
            );

        const url =
            URL.createObjectURL(
                blob
            );

        const anchor =
            document.createElement(
                "a"
            );

        anchor.href =
            url;

        anchor.download =
            filename;

        anchor.click();

        window.setTimeout(
            () =>
                URL.revokeObjectURL(
                    url
                ),
            1000
        );

        return filename;
    }

    const commands =
        [
            {
                name:
                    "provider-health",

                category:
                    "data",

                description:
                    "Inspect provider health summaries or one provider.",

                usage:
                    "provider-health [provider] [--state STATE] [--sort state|score|latency|name]",

                handler: async ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const service =
                        context.services?.get?.(
                            "provider-health"
                        ) ||
                        context.providerHealth;

                    if (!service) {
                        throw new Error(
                            "Provider health service is unavailable."
                        );
                    }

                    if (
                        args[0]
                    ) {
                        return writeJSON(
                            service.evaluate(
                                args[0]
                            )
                        );
                    }

                    if (
                        parsed.options.state ||
                        parsed.options.sort ||
                        parsed.options.contains ||
                        parsed.options.limit
                    ) {
                        return writeJSON(
                            service.list({
                                state:
                                    parsed.options.state,

                                sort:
                                    parsed.options.sort,

                                contains:
                                    parsed.options.contains,

                                limit:
                                    parsed.options.limit,

                                enabled:
                                    parsed.flags.enabled
                                        ? true
                                        : undefined,

                                eligible:
                                    parsed.flags.eligible
                                        ? true
                                        : undefined
                            })
                        );
                    }

                    return writeJSON(
                        await service.run()
                    );
                }
            },

            {
                name:
                    "provider-health-status",

                category:
                    "data",

                description:
                    "Display provider-health service status.",

                usage:
                    "provider-health-status",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.providerHealth.status()
                    )
            },

            {
                name:
                    "provider-health-check",

                category:
                    "data",

                description:
                    "Check one configured provider endpoint.",

                usage:
                    "provider-health-check <provider> [--timeout MS] [--method HEAD|GET]",

                handler: async ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const provider =
                        args[0];

                    if (!provider) {
                        throw new Error(
                            "A provider ID is required."
                        );
                    }

                    return writeJSON(
                        await context.providerHealth.checkProvider(
                            provider,
                            {
                                timeout:
                                    parsed.options.timeout,

                                method:
                                    parsed.options.method
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-health-check-all",

                category:
                    "data",

                description:
                    "Check all enabled providers with configured endpoints.",

                usage:
                    "provider-health-check-all [--concurrency N] [--timeout MS]",

                handler: async ({
                    parsed,
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        await context.providerHealth.checkAll({
                            concurrency:
                                parsed.options.concurrency,

                            timeout:
                                parsed.options.timeout,

                            method:
                                parsed.options.method
                        })
                    )
            },

            {
                name:
                    "provider-health-start",

                category:
                    "data",

                description:
                    "Start periodic provider-health monitoring.",

                usage:
                    "provider-health-start [interval-ms]",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const started =
                        context.providerHealth.start(
                            args[0]
                        );

                    return write(
                        started
                            ? "Provider-health monitoring started."
                            : "Provider-health monitoring is already running.",
                        started
                            ? "success"
                            : "warning"
                    );
                }
            },

            {
                name:
                    "provider-health-stop",

                category:
                    "data",

                description:
                    "Stop periodic provider-health monitoring.",

                usage:
                    "provider-health-stop",

                handler: ({
                    context,
                    write
                }) => {
                    const stopped =
                        context.providerHealth.stop();

                    return write(
                        stopped
                            ? "Provider-health monitoring stopped."
                            : "Provider-health monitoring was not running.",
                        stopped
                            ? "success"
                            : "warning"
                    );
                }
            },

            {
                name:
                    "provider-health-record",

                category:
                    "data",

                description:
                    "Record a manual provider-health sample.",

                usage:
                    "provider-health-record <provider> <success|failure> [latency-ms]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const provider =
                        args[0];

                    const state =
                        String(
                            args[1] ||
                            ""
                        ).toLowerCase();

                    if (
                        !provider ||
                        ![
                            "success",
                            "failure",
                            "ok",
                            "error"
                        ].includes(
                            state
                        )
                    ) {
                        throw new Error(
                            "Usage: provider-health-record <provider> <success|failure> [latency-ms]"
                        );
                    }

                    return writeJSON(
                        context.providerHealth.recordSample(
                            provider,
                            {
                                success:
                                    [
                                        "success",
                                        "ok"
                                    ].includes(
                                        state
                                    ),

                                latency:
                                    args[2],

                                timestamp:
                                    Date.now()
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-health-export",

                category:
                    "data",

                description:
                    "Export provider health as JSON or CSV.",

                usage:
                    "provider-health-export [json|csv] [filename]",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const format =
                        String(
                            args[0] ||
                            "json"
                        ).toLowerCase();

                    if (
                        format ===
                        "csv"
                    ) {
                        const filename =
                            args[1] ||
                            "speciedex-provider-health.csv";

                        download(
                            context.providerHealth.exportCSV(),
                            filename,
                            "text/csv"
                        );

                        return write(
                            `Provider health exported to ${filename}.`,
                            "success"
                        );
                    }

                    const filename =
                        args[1] ||
                        "speciedex-provider-health.json";

                    download(
                        JSON.stringify(
                            context.providerHealth.exportJSON(),
                            null,
                            2
                        ),
                        filename,
                        "application/json"
                    );

                    return write(
                        `Provider health exported to ${filename}.`,
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

            DEFAULT_OPTIONS,
            HEALTH_STATES,
            ProviderHealthService,

            clamp,
            parseNumber,
            parseBoolean,
            normalizeProviderID,
            normalizeTimestamp,
            mean,
            percentile,
            formatDuration,
            healthRank,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalProviderHealth =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] =
        api;

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
