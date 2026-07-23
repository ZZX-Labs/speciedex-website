/*
========================================================================
Speciedex.org
Terminal ProviderOverlap Module
========================================================================

Provider record-overlap comparison service for SpeciedexTerminal.

Provides:

    • Validated provider-overlap API requests
    • Provider-pair, rank, field, record, threshold, metric, and date filters
    • Normalized overlap comparison records
    • Jaccard, Dice, containment, shared, unique, and coverage metrics
    • Pairwise matrix, provider, rank, metric, and threshold summaries
    • Single-comparison retrieval
    • High-overlap, low-overlap, duplicate, and asymmetric views
    • Lifecycle events, caching, and resilient service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "ProviderOverlap";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "provider-overlap";

    const DEFAULT_LIMIT = 50;
    const MIN_LIMIT = 1;
    const MAX_LIMIT = 1000;
    const DEFAULT_HIGH_THRESHOLD = 0.75;
    const DEFAULT_LOW_THRESHOLD = 0.25;

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

    function normalizeRatio(value, fallback = null) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return fallback;
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

    function normalizeSort(value) {
        const normalized = normalizeText(
            value || "jaccard"
        ).toLowerCase();

        const allowed = new Set([
            "jaccard",
            "dice",
            "containment",
            "containment_a",
            "containment_b",
            "shared",
            "unique_a",
            "unique_b",
            "total_a",
            "total_b",
            "provider_a",
            "provider_b",
            "rank",
            "metric",
            "updated_at",
            "created_at",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported provider-overlap sort field: ${value}`
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
                "provider_a",
                "provider_b",
                "rank",
                "field",
                "record",
                "record_id",
                "metric",
                "comparison",
                "comparison_id",
                "status",
                "region",
                "country",
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
                "duplicate",
                "asymmetric",
                "active",
                "verified"
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

        const ratioFields = [
            ["min_jaccard", source.min_jaccard ?? source.minJaccard],
            ["max_jaccard", source.max_jaccard ?? source.maxJaccard],
            ["min_dice", source.min_dice ?? source.minDice],
            ["max_dice", source.max_dice ?? source.maxDice],
            ["min_containment", source.min_containment ?? source.minContainment],
            ["max_containment", source.max_containment ?? source.maxContainment],
            ["threshold", source.threshold]
        ];

        for (const [key, value] of ratioFields) {
            if (
                value !== undefined &&
                value !== null &&
                value !== ""
            ) {
                normalized[key] =
                    normalizeRatio(value, 0);
            }
        }

        for (
            const [minimum, maximum, label] of
            [
                ["min_jaccard", "max_jaccard", "Jaccard"],
                ["min_dice", "max_dice", "Dice"],
                ["min_containment", "max_containment", "Containment"]
            ]
        ) {
            if (
                normalized[minimum] !== undefined &&
                normalized[maximum] !== undefined &&
                normalized[minimum] >
                normalized[maximum]
            ) {
                throw new RangeError(
                    `Minimum ${label.toLowerCase()} value must not exceed maximum ${label.toLowerCase()} value.`
                );
            }
        }

        const countFields = [
            ["min_shared", source.min_shared ?? source.minShared],
            ["max_shared", source.max_shared ?? source.maxShared],
            ["min_total", source.min_total ?? source.minTotal],
            ["max_total", source.max_total ?? source.maxTotal]
        ];

        for (const [key, value] of countFields) {
            if (
                value !== undefined &&
                value !== null &&
                value !== ""
            ) {
                normalized[key] =
                    clampInteger(
                        value,
                        0,
                        0,
                        Number.MAX_SAFE_INTEGER
                    );
            }
        }

        if (
            normalized.min_shared !== undefined &&
            normalized.max_shared !== undefined &&
            normalized.min_shared >
            normalized.max_shared
        ) {
            throw new RangeError(
                "Minimum shared count must not exceed maximum shared count."
            );
        }

        if (
            normalized.min_total !== undefined &&
            normalized.max_total !== undefined &&
            normalized.min_total >
            normalized.max_total
        ) {
            throw new RangeError(
                "Minimum total count must not exceed maximum total count."
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
                "Provider-overlap start date must not be later than the end date."
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

    function safeDivide(numerator, denominator) {
        if (
            !Number.isFinite(numerator) ||
            !Number.isFinite(denominator) ||
            denominator <= 0
        ) {
            return 0;
        }

        return numerator / denominator;
    }

    function canonicalPair(providerA, providerB) {
        const left = normalizeText(providerA);
        const right = normalizeText(providerB);

        return left.localeCompare(right) <= 0
            ? [left, right]
            : [right, left];
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
                provider_a: "",
                provider_b: "",
                pair: "",
                shared: 0,
                unique_a: 0,
                unique_b: 0,
                total_a: 0,
                total_b: 0,
                union: 0,
                jaccard: 0,
                dice: 0,
                containment_a: 0,
                containment_b: 0,
                containment: 0,
                duplicate: false,
                asymmetric: false,
                active: true,
                verified: false,
                status: "unknown"
            };
        }

        const providerA = normalizeText(
            record.provider_a ??
            record.providerA ??
            record.left_provider ??
            record.leftProvider ??
            record.source_provider ??
            record.sourceProvider ??
            ""
        );

        const providerB = normalizeText(
            record.provider_b ??
            record.providerB ??
            record.right_provider ??
            record.rightProvider ??
            record.target_provider ??
            record.targetProvider ??
            ""
        );

        const [canonicalA, canonicalB] =
            canonicalPair(
                providerA,
                providerB
            );

        const shared = numericValue(
            record.shared ??
            record.overlap ??
            record.shared_records ??
            record.sharedRecords ??
            record.intersection,
            0
        );

        const totalA = numericValue(
            record.total_a ??
            record.totalA ??
            record.records_a ??
            record.recordsA ??
            record.left_total ??
            record.leftTotal,
            shared +
            numericValue(
                record.unique_a ??
                record.uniqueA ??
                record.left_unique ??
                record.leftUnique,
                0
            )
        );

        const totalB = numericValue(
            record.total_b ??
            record.totalB ??
            record.records_b ??
            record.recordsB ??
            record.right_total ??
            record.rightTotal,
            shared +
            numericValue(
                record.unique_b ??
                record.uniqueB ??
                record.right_unique ??
                record.rightUnique,
                0
            )
        );

        const uniqueA = numericValue(
            record.unique_a ??
            record.uniqueA ??
            record.left_unique ??
            record.leftUnique,
            Math.max(0, totalA - shared)
        );

        const uniqueB = numericValue(
            record.unique_b ??
            record.uniqueB ??
            record.right_unique ??
            record.rightUnique,
            Math.max(0, totalB - shared)
        );

        const union = numericValue(
            record.union ??
            record.union_count ??
            record.unionCount,
            shared + uniqueA + uniqueB
        );

        const jaccard = normalizeRatio(
            record.jaccard ??
            record.jaccard_index ??
            record.jaccardIndex ??
            safeDivide(shared, union),
            0
        );

        const dice = normalizeRatio(
            record.dice ??
            record.dice_coefficient ??
            record.diceCoefficient ??
            safeDivide(
                2 * shared,
                totalA + totalB
            ),
            0
        );

        const containmentA = normalizeRatio(
            record.containment_a ??
            record.containmentA ??
            record.left_containment ??
            record.leftContainment ??
            safeDivide(shared, totalA),
            0
        );

        const containmentB = normalizeRatio(
            record.containment_b ??
            record.containmentB ??
            record.right_containment ??
            record.rightContainment ??
            safeDivide(shared, totalB),
            0
        );

        const containment = normalizeRatio(
            record.containment ??
            record.containment_score ??
            record.containmentScore ??
            Math.max(
                containmentA,
                containmentB
            ),
            0
        );

        const asymmetry = Math.abs(
            containmentA -
            containmentB
        );

        const status = normalizeText(
            record.status ??
            "active"
        ).toLowerCase();

        const duplicate =
            record.duplicate === true ||
            record.is_duplicate === true ||
            record.isDuplicate === true ||
            jaccard >= 0.99;

        const asymmetric =
            record.asymmetric === true ||
            record.is_asymmetric === true ||
            record.isAsymmetric === true ||
            asymmetry >= 0.25;

        return {
            ...record,
            index:
                record.index ??
                index,
            id: normalizeText(
                record.id ??
                record.comparison_id ??
                record.comparisonId ??
                record.uuid ??
                `${canonicalA || "provider-a"}::${canonicalB || "provider-b"}`
            ),
            provider_a:
                providerA,
            provider_b:
                providerB,
            canonical_provider_a:
                canonicalA,
            canonical_provider_b:
                canonicalB,
            pair:
                `${canonicalA}::${canonicalB}`,
            shared,
            unique_a:
                uniqueA,
            unique_b:
                uniqueB,
            total_a:
                totalA,
            total_b:
                totalB,
            union,
            jaccard,
            dice,
            containment_a:
                containmentA,
            containment_b:
                containmentB,
            containment,
            asymmetry,
            coverage_a:
                containmentA,
            coverage_b:
                containmentB,
            duplicate,
            asymmetric,
            active:
                record.active !== false &&
                ![
                    "inactive",
                    "deleted"
                ].includes(status),
            verified:
                record.verified === true ||
                [
                    "verified",
                    "confirmed"
                ].includes(status),
            status,
            rank: normalizeText(
                record.rank ??
                record.taxon_rank ??
                record.taxonRank ??
                ""
            ).toLowerCase(),
            field: normalizeText(
                record.field ??
                record.property ??
                record.attribute ??
                ""
            ),
            metric: normalizeText(
                record.metric ??
                record.method ??
                "jaccard"
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
                record.comparison_type ??
                record.comparisonType ??
                ""
            ),
            created_at:
                record.created_at ??
                record.createdAt ??
                "",
            updated_at:
                record.updated_at ??
                record.updatedAt ??
                record.measured_at ??
                record.measuredAt ??
                record.timestamp ??
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
                .filter(Number.isFinite);

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

    function buildMatrix(records) {
        const matrix = {};

        for (const item of records) {
            const providerA =
                item.provider_a ||
                "unknown";

            const providerB =
                item.provider_b ||
                "unknown";

            matrix[providerA] =
                matrix[providerA] ||
                {};

            matrix[providerB] =
                matrix[providerB] ||
                {};

            matrix[providerA][providerB] = {
                shared:
                    item.shared,
                jaccard:
                    item.jaccard,
                dice:
                    item.dice,
                containment:
                    item.containment,
                coverage:
                    item.coverage_a
            };

            matrix[providerB][providerA] = {
                shared:
                    item.shared,
                jaccard:
                    item.jaccard,
                dice:
                    item.dice,
                containment:
                    item.containment,
                coverage:
                    item.coverage_b
            };
        }

        return matrix;
    }

    function summarize(records) {
        const values =
            Array.isArray(records)
                ? records
                : [];

        const providers = new Map();
        const ranks = new Map();
        const metrics = new Map();
        const statuses = new Map();
        const categories = new Map();

        let shared = 0;
        let uniqueA = 0;
        let uniqueB = 0;
        let union = 0;

        for (const item of values) {
            incrementMap(
                providers,
                item.provider_a
            );

            incrementMap(
                providers,
                item.provider_b
            );

            incrementMap(
                ranks,
                item.rank
            );

            incrementMap(
                metrics,
                item.metric
            );

            incrementMap(
                statuses,
                item.status
            );

            incrementMap(
                categories,
                item.category
            );

            shared += numericValue(
                item.shared,
                0
            );

            uniqueA += numericValue(
                item.unique_a,
                0
            );

            uniqueB += numericValue(
                item.unique_b,
                0
            );

            union += numericValue(
                item.union,
                0
            );
        }

        return {
            total:
                values.length,
            active:
                values.filter(
                    item =>
                        item.active
                ).length,
            verified:
                values.filter(
                    item =>
                        item.verified
                ).length,
            duplicate:
                values.filter(
                    item =>
                        item.duplicate
                ).length,
            asymmetric:
                values.filter(
                    item =>
                        item.asymmetric
                ).length,
            shared,
            uniqueA,
            uniqueB,
            union,
            jaccard:
                metricSummary(
                    values.map(
                        item =>
                            item.jaccard
                    )
                ),
            dice:
                metricSummary(
                    values.map(
                        item =>
                            item.dice
                    )
                ),
            containment:
                metricSummary(
                    values.map(
                        item =>
                            item.containment
                    )
                ),
            asymmetry:
                metricSummary(
                    values.map(
                        item =>
                            item.asymmetry
                    )
                ),
            sharedCounts:
                metricSummary(
                    values.map(
                        item =>
                            item.shared
                    )
                ),
            providers:
                mapToSortedObject(
                    providers
                ),
            ranks:
                mapToSortedObject(
                    ranks
                ),
            metrics:
                mapToSortedObject(
                    metrics
                ),
            statuses:
                mapToSortedObject(
                    statuses
                ),
            categories:
                mapToSortedObject(
                    categories
                ),
            matrix:
                buildMatrix(values)
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
                                Array.isArray(payload.overlap)
                                    ? payload.overlap
                                    : (
                                        Array.isArray(payload.comparisons)
                                            ? payload.comparisons
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

    class ProviderOverlapService extends EventTarget {
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
                    "Provider-overlap service has been destroyed."
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
                    `provider-overlap:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not break overlap operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-provider-overlap-${name}`,
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
                        "providers/overlap",
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
                    "An overlap comparison ID is required."
                );
            }

            try {
                const payload =
                    await this.context.api.get(
                        `providers/overlap/${encodeURIComponent(normalizedId)}`,
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
                                normalizedId ||
                            item.pair ===
                                normalizedId
                    );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async compare(providerA, providerB, parameters = {}, options = {}) {
            const left =
                normalizeText(providerA);

            const right =
                normalizeText(providerB);

            if (!left || !right) {
                throw new TypeError(
                    "Two provider IDs or names are required."
                );
            }

            const result =
                await this.list(
                    {
                        ...parameters,
                        provider_a:
                            left,
                        provider_b:
                            right
                    },
                    options
                );

            const match =
                result.records.find(
                    item =>
                        (
                            item.provider_a === left &&
                            item.provider_b === right
                        ) ||
                        (
                            item.provider_a === right &&
                            item.provider_b === left
                        )
                );

            return match || result;
        }

        async high(threshold = DEFAULT_HIGH_THRESHOLD, parameters = {}, options = {}) {
            const normalizedThreshold =
                normalizeRatio(
                    threshold,
                    DEFAULT_HIGH_THRESHOLD
                );

            const result =
                await this.list(
                    {
                        ...parameters,
                        min_jaccard:
                            normalizedThreshold,
                        threshold:
                            normalizedThreshold
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.jaccard >=
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

        async low(threshold = DEFAULT_LOW_THRESHOLD, parameters = {}, options = {}) {
            const normalizedThreshold =
                normalizeRatio(
                    threshold,
                    DEFAULT_LOW_THRESHOLD
                );

            const result =
                await this.list(
                    {
                        ...parameters,
                        max_jaccard:
                            normalizedThreshold,
                        threshold:
                            normalizedThreshold
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.jaccard <=
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

        async duplicates(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        duplicate: true,
                        min_jaccard:
                            parameters.min_jaccard ??
                            0.99
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.duplicate ||
                        item.jaccard >= 0.99
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async asymmetric(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        asymmetric: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.asymmetric
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async matrix(parameters = {}, options = {}) {
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
                providers:
                    [
                        ...new Set(
                            result.records.flatMap(
                                item => [
                                    item.provider_a,
                                    item.provider_b
                                ]
                            ).filter(Boolean)
                        )
                    ].sort(),
                matrix:
                    buildMatrix(
                        result.records
                    ),
                summary:
                    summarize(
                        result.records
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
                comparisons:
                    result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "providers/overlap",
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
            ProviderOverlapService &&
            !existing.destroyed
        ) {
            context.providerOverlap =
                existing;

            return existing;
        }

        if (
            context.providerOverlap instanceof
            ProviderOverlapService &&
            !context.providerOverlap.destroyed
        ) {
            return context.providerOverlap;
        }

        const service =
            new ProviderOverlapService(
                context
            );

        context.providerOverlap =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "providerOverlap",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-provider-overlap-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.providerOverlap ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                ProviderOverlapService
            )
        ) {
            throw new Error(
                "Provider-overlap service is unavailable."
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
                    "--provider-a="
                )
            ) {
                parameters.provider_a =
                    argument.slice(13);
                continue;
            }

            if (
                argument.startsWith(
                    "--provider-b="
                )
            ) {
                parameters.provider_b =
                    argument.slice(13);
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
                    "--field="
                )
            ) {
                parameters.field =
                    argument.slice(8);
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
                    "--metric="
                )
            ) {
                parameters.metric =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--comparison="
                )
            ) {
                parameters.comparison =
                    argument.slice(13);
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
                    "--duplicate="
                )
            ) {
                parameters.duplicate =
                    argument.slice(12);
                continue;
            }

            if (
                argument.startsWith(
                    "--asymmetric="
                )
            ) {
                parameters.asymmetric =
                    argument.slice(13);
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
                    "--verified="
                )
            ) {
                parameters.verified =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-jaccard="
                )
            ) {
                parameters.min_jaccard =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-jaccard="
                )
            ) {
                parameters.max_jaccard =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-dice="
                )
            ) {
                parameters.min_dice =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-dice="
                )
            ) {
                parameters.max_dice =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-containment="
                )
            ) {
                parameters.min_containment =
                    argument.slice(18);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-containment="
                )
            ) {
                parameters.max_containment =
                    argument.slice(18);
                continue;
            }

            if (
                argument.startsWith(
                    "--min-shared="
                )
            ) {
                parameters.min_shared =
                    argument.slice(13);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-shared="
                )
            ) {
                parameters.max_shared =
                    argument.slice(13);
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
            name: "provider-overlap",
            aliases: [
                "providers-overlap"
            ],
            category: "providers",
            description:
                "Compare record overlap between providers.",
            usage:
                "provider-overlap [query] [limit] [--provider=ID] [--provider-a=ID] [--provider-b=ID] [--rank=RANK] [--field=FIELD] [--record=ID] [--metric=METRIC] [--comparison=ID] [--status=STATUS] [--region=REGION] [--country=COUNTRY] [--category=CATEGORY] [--type=TYPE] [--duplicate=true|false] [--asymmetric=true|false] [--active=true|false] [--verified=true|false] [--min-jaccard=N] [--max-jaccard=N] [--min-dice=N] [--max-dice=N] [--min-containment=N] [--max-containment=N] [--min-shared=N] [--max-shared=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
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
            name: "provider-overlap-get",
            aliases: [
                "provider-overlap-comparison"
            ],
            category: "providers",
            description:
                "Retrieve one provider-overlap comparison by ID or canonical pair.",
            usage:
                "provider-overlap-get <id|provider-a::provider-b>",
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
                        "An overlap comparison ID is required."
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
            name: "provider-overlap-compare",
            aliases: [
                "compare-providers"
            ],
            category: "providers",
            description:
                "Compare overlap between two providers.",
            usage:
                "provider-overlap-compare <provider-a> <provider-b> [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                if (args.length < 2) {
                    throw new Error(
                        "Two provider IDs or names are required."
                    );
                }

                const providerA =
                    args[0];

                const providerB =
                    args[1];

                const parameters =
                    parseCommandArguments(
                        args.slice(2)
                    );

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).compare(
                        providerA,
                        providerB,
                        parameters
                    )
                );
            }
        },
        {
            name: "provider-overlap-high",
            aliases: [
                "high-provider-overlap"
            ],
            category: "providers",
            description:
                "List provider comparisons at or above a Jaccard threshold.",
            usage:
                "provider-overlap-high [threshold] [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                let threshold =
                    DEFAULT_HIGH_THRESHOLD;

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
                    ).high(
                        threshold,
                        parseCommandArguments(
                            filters
                        )
                    )
                );
            }
        },
        {
            name: "provider-overlap-low",
            aliases: [
                "low-provider-overlap"
            ],
            category: "providers",
            description:
                "List provider comparisons at or below a Jaccard threshold.",
            usage:
                "provider-overlap-low [threshold] [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                let threshold =
                    DEFAULT_LOW_THRESHOLD;

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
                    ).low(
                        threshold,
                        parseCommandArguments(
                            filters
                        )
                    )
                );
            }
        },
        {
            name: "provider-overlap-duplicates",
            aliases: [
                "duplicate-provider-overlap"
            ],
            category: "providers",
            description:
                "List near-duplicate provider comparisons.",
            usage:
                "provider-overlap-duplicates [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).duplicates(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "provider-overlap-asymmetric",
            aliases: [
                "asymmetric-provider-overlap"
            ],
            category: "providers",
            description:
                "List provider comparisons with asymmetric containment.",
            usage:
                "provider-overlap-asymmetric [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).asymmetric(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "provider-overlap-matrix",
            aliases: [
                "provider-similarity-matrix"
            ],
            category: "providers",
            description:
                "Build a pairwise provider-overlap matrix.",
            usage:
                "provider-overlap-matrix [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).matrix(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "provider-overlap-summary",
            aliases: [
                "provider-similarity-summary"
            ],
            category: "providers",
            description:
                "Summarize provider overlap, similarity, containment, duplication, asymmetry, ranks, metrics, and pairwise matrix values.",
            usage:
                "provider-overlap-summary [filters]",
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
            name: "provider-overlap-status",
            category: "providers",
            description:
                "Show provider-overlap service status.",
            usage:
                "provider-overlap-status",
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
        ProviderOverlapService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeRatio,
        canonicalPair,
        safeDivide,
        percentile,
        metricSummary,
        buildMatrix,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalProviderOverlap =
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
