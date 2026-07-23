/*
========================================================================
Speciedex.org
Statistics Worker
========================================================================

High-performance worker-side statistics engine for SpeciedexTerminal.

Supports:

    • Record and field summaries
    • Distinct counts and null analysis
    • Numeric min, max, sum, mean, median, variance, and standard deviation
    • Percentiles and histograms
    • Grouped statistics
    • Pairwise numeric correlations
    • Request cancellation and progress events
    • Structured worker responses and safe error serialization

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

"use strict";

const WORKER_VERSION = "2.0.0";
const MAX_RECORDS = 1000000;
const MAX_FIELDS = 512;
const DEFAULT_PERCENTILES = Object.freeze([
    0,
    0.25,
    0.5,
    0.75,
    1
]);
const DEFAULT_BINS = 10;
const MAX_BINS = 1000;
const PROGRESS_INTERVAL = 5000;

const activeRequests = new Map();

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

function serializeError(error) {
    return {
        name: error?.name || "Error",
        message: error?.message || String(error),
        stack: error?.stack || null,
        code: error?.code || null
    };
}

function post(type, id, payload = {}) {
    self.postMessage({
        type,
        id,
        ...payload
    });
}

function respond(id, result, error = null) {
    post(
        "response",
        id,
        error
            ? {
                error: serializeError(error)
            }
            : {
                result
            }
    );
}

function assertActive(id) {
    if (
        id !== null &&
        activeRequests.get(id)?.cancelled
    ) {
        const error = new Error(
            "Statistics worker request cancelled."
        );

        error.name = "AbortError";
        error.code = "STATISTICS_WORKER_CANCELLED";

        throw error;
    }
}

self.addEventListener("message", async event => {
    const message = event.data || {};
    const id = message.id ?? null;
    const type = normalizeText(
        message.type
    ).toLowerCase();

    if (type === "cancel") {
        const targetId =
            message.payload?.id ??
            message.targetId ??
            id;

        if (activeRequests.has(targetId)) {
            activeRequests.get(
                targetId
            ).cancelled = true;
        }

        respond(id, {
            cancelled: true,
            targetId
        });

        return;
    }

    activeRequests.set(id, {
        cancelled: false,
        startedAt: performance.now()
    });

    try {
        const result = await handle(
            type,
            message.payload || {},
            id
        );

        respond(id, result);
    } catch (error) {
        respond(id, null, error);
    } finally {
        activeRequests.delete(id);
    }
});

async function handle(type, payload, id) {
    switch (type) {
        case "calculate":
        case "summary":
            return calculateStatistics(
                payload,
                id
            );

        case "field":
            return calculateFieldStatistics(
                payload,
                id
            );

        case "group":
            return calculateGroupedStatistics(
                payload,
                id
            );

        case "correlation":
        case "correlations":
            return calculateCorrelations(
                payload,
                id
            );

        case "histogram":
            return calculateHistogramOperation(
                payload,
                id
            );

        case "status":
            return {
                ready: true,
                workerVersion:
                    WORKER_VERSION,
                activeRequests:
                    activeRequests.size
            };

        case "ping":
            return {
                pong: true,
                version:
                    WORKER_VERSION
            };

        default:
            throw new Error(
                `Unsupported statistics operation: ${type || "(empty)"}`
            );
    }
}

async function calculateStatistics(
    payload = {},
    id = null
) {
    const startedAt = performance.now();

    const records = normalizeRecords(
        payload.records
    );

    const fields = normalizeFields(
        payload.fields,
        records
    );

    const percentiles = normalizePercentiles(
        payload.percentiles
    );

    const bins = clampInteger(
        payload.bins,
        DEFAULT_BINS,
        1,
        MAX_BINS
    );

    const result = {
        records: records.length,
        fields: fields.length,
        fieldNames: fields,
        distinct: {},
        nulls: {},
        types: {},
        numeric: {},
        categorical: {},
        elapsed_ms: 0
    };

    for (
        let index = 0;
        index < fields.length;
        index += 1
    ) {
        assertActive(id);

        const field = fields[index];

        const values = records.flatMap(
            record => fieldValues(
                record,
                field
            )
        );

        const summary = summarizeValues(
            values,
            {
                percentiles,
                bins,
                includeHistogram:
                    payload.histograms === true,
                topValues:
                    payload.topValues
            }
        );

        result.distinct[field] =
            summary.distinct;

        result.nulls[field] = {
            count:
                summary.nulls,
            rate:
                values.length
                    ? summary.nulls /
                      values.length
                    : 0
        };

        result.types[field] =
            summary.types;

        if (summary.numeric) {
            result.numeric[field] =
                summary.numeric;
        }

        if (summary.categorical) {
            result.categorical[field] =
                summary.categorical;
        }

        if (
            payload.progress === true &&
            index > 0
        ) {
            post("progress", id, {
                phase: "calculate",
                completed: index,
                total: fields.length
            });

            await Promise.resolve();
        }
    }

    result.elapsed_ms =
        performance.now() -
        startedAt;

    return result;
}

async function calculateFieldStatistics(
    payload = {},
    id = null
) {
    const records = normalizeRecords(
        payload.records
    );

    const field = normalizeText(
        payload.field
    );

    if (!field) {
        throw new TypeError(
            "A statistics field is required."
        );
    }

    const values = [];

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        values.push(
            ...fieldValues(
                records[index],
                field
            )
        );

        if (
            payload.progress === true &&
            index > 0 &&
            index %
                PROGRESS_INTERVAL ===
                0
        ) {
            post("progress", id, {
                phase: "field",
                completed: index,
                total: records.length
            });

            await Promise.resolve();
        }
    }

    return {
        field,
        records:
            records.length,
        values:
            values.length,
        summary:
            summarizeValues(
                values,
                {
                    percentiles:
                        normalizePercentiles(
                            payload.percentiles
                        ),
                    bins:
                        clampInteger(
                            payload.bins,
                            DEFAULT_BINS,
                            1,
                            MAX_BINS
                        ),
                    includeHistogram:
                        payload.histogram !==
                        false,
                    topValues:
                        payload.topValues
                }
            )
    };
}

async function calculateGroupedStatistics(
    payload = {},
    id = null
) {
    const records = normalizeRecords(
        payload.records
    );

    const groupBy = normalizeText(
        payload.groupBy ??
        payload.group
    );

    if (!groupBy) {
        throw new TypeError(
            "A groupBy field is required."
        );
    }

    const valueFields = normalizeFields(
        payload.fields,
        records
    ).filter(
        field =>
            field !== groupBy
    );

    const groups = new Map();

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        const record = records[index];

        const groupValues =
            fieldValues(
                record,
                groupBy
            );

        const normalizedGroups =
            groupValues.length
                ? groupValues
                : [null];

        for (
            const groupValue of
            normalizedGroups
        ) {
            const key =
                canonicalKey(
                    groupValue
                );

            if (!groups.has(key)) {
                groups.set(key, {
                    value:
                        groupValue,
                    records: []
                });
            }

            groups.get(key)
                .records.push(record);
        }

        if (
            payload.progress === true &&
            index > 0 &&
            index %
                PROGRESS_INTERVAL ===
                0
        ) {
            post("progress", id, {
                phase: "group",
                completed: index,
                total: records.length
            });

            await Promise.resolve();
        }
    }

    const results = [];

    for (
        const group of
        groups.values()
    ) {
        const fields = {};

        for (
            const field of
            valueFields
        ) {
            const values =
                group.records.flatMap(
                    record =>
                        fieldValues(
                            record,
                            field
                        )
                );

            fields[field] =
                summarizeValues(
                    values,
                    {
                        percentiles:
                            normalizePercentiles(
                                payload.percentiles
                            ),
                        bins:
                            clampInteger(
                                payload.bins,
                                DEFAULT_BINS,
                                1,
                                MAX_BINS
                            ),
                        includeHistogram:
                            payload.histograms ===
                            true,
                        topValues:
                            payload.topValues
                    }
                );
        }

        results.push({
            group:
                group.value,
            records:
                group.records.length,
            fields
        });
    }

    results.sort(
        (left, right) =>
            right.records -
            left.records ||
            normalizeText(
                left.group
            ).localeCompare(
                normalizeText(
                    right.group
                )
            )
    );

    return {
        groupBy,
        groups:
            results.length,
        records:
            records.length,
        results
    };
}

async function calculateCorrelations(
    payload = {},
    id = null
) {
    const records = normalizeRecords(
        payload.records
    );

    const fields = normalizeFields(
        payload.fields,
        records
    );

    if (fields.length < 2) {
        throw new RangeError(
            "At least two fields are required for correlation."
        );
    }

    const pairs = [];

    for (
        let leftIndex = 0;
        leftIndex <
            fields.length;
        leftIndex += 1
    ) {
        for (
            let rightIndex =
                leftIndex + 1;
            rightIndex <
                fields.length;
            rightIndex += 1
        ) {
            assertActive(id);

            const leftField =
                fields[leftIndex];

            const rightField =
                fields[rightIndex];

            const leftValues = [];
            const rightValues = [];

            for (
                const record of
                records
            ) {
                const left =
                    firstNumericValue(
                        record,
                        leftField
                    );

                const right =
                    firstNumericValue(
                        record,
                        rightField
                    );

                if (
                    left === null ||
                    right === null
                ) {
                    continue;
                }

                leftValues.push(left);
                rightValues.push(right);
            }

            pairs.push({
                left:
                    leftField,
                right:
                    rightField,
                count:
                    leftValues.length,
                correlation:
                    pearsonCorrelation(
                        leftValues,
                        rightValues
                    )
            });
        }

        if (
            payload.progress === true
        ) {
            post("progress", id, {
                phase:
                    "correlation",
                completed:
                    leftIndex + 1,
                total:
                    fields.length
            });

            await Promise.resolve();
        }
    }

    pairs.sort(
        (left, right) =>
            Math.abs(
                right.correlation ?? 0
            ) -
            Math.abs(
                left.correlation ?? 0
            )
    );

    return {
        records:
            records.length,
        fields,
        pairs
    };
}

async function calculateHistogramOperation(
    payload = {},
    id = null
) {
    const records = normalizeRecords(
        payload.records
    );

    const field = normalizeText(
        payload.field
    );

    if (!field) {
        throw new TypeError(
            "A histogram field is required."
        );
    }

    const values = [];

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        for (
            const value of
            fieldValues(
                records[index],
                field
            )
        ) {
            const number =
                numericValue(value);

            if (number !== null) {
                values.push(number);
            }
        }

        if (
            payload.progress === true &&
            index > 0 &&
            index %
                PROGRESS_INTERVAL ===
                0
        ) {
            post("progress", id, {
                phase:
                    "histogram",
                completed:
                    index,
                total:
                    records.length
            });

            await Promise.resolve();
        }
    }

    return {
        field,
        records:
            records.length,
        values:
            values.length,
        histogram:
            buildHistogram(
                values,
                clampInteger(
                    payload.bins,
                    DEFAULT_BINS,
                    1,
                    MAX_BINS
                )
            )
    };
}

function normalizeRecords(records) {
    const values =
        Array.isArray(records)
            ? records
            : [];

    if (
        values.length >
        MAX_RECORDS
    ) {
        throw new RangeError(
            `Statistics record limit exceeded: ${values.length} > ${MAX_RECORDS}.`
        );
    }

    return values;
}

function normalizeFields(
    requested,
    records
) {
    const fields =
        Array.isArray(requested) &&
        requested.length
            ? [
                ...new Set(
                    requested
                        .map(
                            normalizeText
                        )
                        .filter(Boolean)
                )
            ]
            : discoverFields(records);

    if (
        fields.length >
        MAX_FIELDS
    ) {
        throw new RangeError(
            `Statistics field limit exceeded: ${fields.length} > ${MAX_FIELDS}.`
        );
    }

    return fields;
}

function discoverFields(records) {
    const fields = new Set();

    for (const record of records) {
        if (
            !record ||
            typeof record !==
                "object" ||
            Array.isArray(record)
        ) {
            continue;
        }

        for (
            const key of
            Object.keys(record)
        ) {
            fields.add(key);
        }
    }

    return [...fields].sort();
}

function normalizePercentiles(values) {
    const input =
        Array.isArray(values) &&
        values.length
            ? values
            : DEFAULT_PERCENTILES;

    return [
        ...new Set(
            input.map(value =>
                clampNumber(
                    value,
                    0.5,
                    0,
                    1
                )
            )
        )
    ].sort(
        (left, right) =>
            left - right
    );
}

function summarizeValues(
    values,
    options = {}
) {
    const typeCounts = {
        null: 0,
        number: 0,
        string: 0,
        boolean: 0,
        date: 0,
        object: 0,
        array: 0,
        other: 0
    };

    const distinct =
        new Set();

    const numeric = [];
    const categorical =
        new Map();

    for (const value of values) {
        const type =
            valueType(value);

        typeCounts[type] =
            (
                typeCounts[type] ||
                0
            ) + 1;

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            continue;
        }

        distinct.add(
            canonicalKey(value)
        );

        const number =
            numericValue(value);

        if (number !== null) {
            numeric.push(number);
        }

        const key =
            normalizeText(value);

        categorical.set(
            key,
            (
                categorical.get(key) ||
                0
            ) + 1
        );
    }

    const result = {
        count:
            values.length,
        nonNull:
            values.length -
            typeCounts.null,
        nulls:
            typeCounts.null,
        distinct:
            distinct.size,
        types:
            typeCounts
    };

    if (numeric.length) {
        result.numeric =
            summarizeNumeric(
                numeric,
                options
            );
    }

    if (categorical.size) {
        result.categorical = {
            top:
                [...categorical.entries()]
                    .sort(
                        (left, right) =>
                            right[1] -
                            left[1] ||
                            left[0]
                                .localeCompare(
                                    right[0]
                                )
                    )
                    .slice(
                        0,
                        clampInteger(
                            options.topValues,
                            20,
                            1,
                            1000
                        )
                    )
                    .map(
                        ([
                            value,
                            count
                        ]) => ({
                            value,
                            count
                        })
                    )
        };
    }

    return result;
}

function summarizeNumeric(
    values,
    options
) {
    const sorted =
        [...values].sort(
            (left, right) =>
                left - right
        );

    const count =
        sorted.length;

    const sum =
        sorted.reduce(
            (total, value) =>
                total + value,
            0
        );

    const mean =
        sum / count;

    const variance =
        count > 1
            ? sorted.reduce(
                (total, value) =>
                    total +
                    (
                        value - mean
                    ) ** 2,
                0
            ) /
              (
                  count - 1
              )
            : 0;

    const percentileValues = {};

    for (
        const percentile of
        options.percentiles ||
        DEFAULT_PERCENTILES
    ) {
        percentileValues[
            percentileLabel(
                percentile
            )
        ] =
            percentileValue(
                sorted,
                percentile
            );
    }

    return {
        count,
        sum,
        minimum:
            sorted[0],
        maximum:
            sorted[
                sorted.length - 1
            ],
        mean,
        median:
            percentileValue(
                sorted,
                0.5
            ),
        variance,
        standardDeviation:
            Math.sqrt(variance),
        percentiles:
            percentileValues,
        histogram:
            options.includeHistogram ===
            true
                ? buildHistogram(
                    sorted,
                    options.bins ||
                    DEFAULT_BINS
                )
                : undefined
    };
}

function percentileValue(
    sorted,
    percentile
) {
    if (!sorted.length) {
        return null;
    }

    if (
        sorted.length === 1
    ) {
        return sorted[0];
    }

    const position =
        percentile *
        (
            sorted.length - 1
        );

    const lower =
        Math.floor(position);

    const upper =
        Math.ceil(position);

    if (lower === upper) {
        return sorted[lower];
    }

    const weight =
        position - lower;

    return (
        sorted[lower] *
            (
                1 - weight
            ) +
        sorted[upper] *
            weight
    );
}

function percentileLabel(
    percentile
) {
    return `p${Math.round(
        percentile * 100
    )}`;
}

function buildHistogram(
    values,
    bins
) {
    if (!values.length) {
        return {
            bins: [],
            minimum: null,
            maximum: null,
            width: null
        };
    }

    const sorted =
        [...values].sort(
            (left, right) =>
                left - right
        );

    const minimum =
        sorted[0];

    const maximum =
        sorted[
            sorted.length - 1
        ];

    if (minimum === maximum) {
        return {
            bins: [{
                start:
                    minimum,
                end:
                    maximum,
                count:
                    sorted.length
            }],
            minimum,
            maximum,
            width: 0
        };
    }

    const count =
        clampInteger(
            bins,
            DEFAULT_BINS,
            1,
            MAX_BINS
        );

    const width =
        (
            maximum -
            minimum
        ) /
        count;

    const output =
        Array.from(
            {
                length:
                    count
            },
            (_value, index) => ({
                start:
                    minimum +
                    index * width,
                end:
                    index ===
                    count - 1
                        ? maximum
                        : minimum +
                          (
                              index + 1
                          ) * width,
                count: 0
            })
        );

    for (const value of sorted) {
        const index =
            Math.min(
                count - 1,
                Math.floor(
                    (
                        value -
                        minimum
                    ) /
                    width
                )
            );

        output[index].count += 1;
    }

    return {
        bins:
            output,
        minimum,
        maximum,
        width
    };
}

function pearsonCorrelation(
    left,
    right
) {
    if (
        left.length !==
            right.length ||
        left.length < 2
    ) {
        return null;
    }

    const count =
        left.length;

    const leftMean =
        left.reduce(
            (sum, value) =>
                sum + value,
            0
        ) /
        count;

    const rightMean =
        right.reduce(
            (sum, value) =>
                sum + value,
            0
        ) /
        count;

    let numerator = 0;
    let leftVariance = 0;
    let rightVariance = 0;

    for (
        let index = 0;
        index < count;
        index += 1
    ) {
        const leftDelta =
            left[index] -
            leftMean;

        const rightDelta =
            right[index] -
            rightMean;

        numerator +=
            leftDelta *
            rightDelta;

        leftVariance +=
            leftDelta ** 2;

        rightVariance +=
            rightDelta ** 2;
    }

    const denominator =
        Math.sqrt(
            leftVariance *
            rightVariance
        );

    return denominator
        ? numerator /
          denominator
        : null;
}

function firstNumericValue(
    record,
    field
) {
    for (
        const value of
        fieldValues(
            record,
            field
        )
    ) {
        const number =
            numericValue(value);

        if (number !== null) {
            return number;
        }
    }

    return null;
}

function numericValue(value) {
    if (
        value === null ||
        value === undefined ||
        value === "" ||
        typeof value ===
            "boolean"
    ) {
        return null;
    }

    const number =
        Number(value);

    return Number.isFinite(number)
        ? number
        : null;
}

function valueType(value) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "null";
    }

    if (Array.isArray(value)) {
        return "array";
    }

    if (
        typeof value ===
        "number"
    ) {
        return "number";
    }

    if (
        typeof value ===
        "boolean"
    ) {
        return "boolean";
    }

    if (
        value instanceof Date
    ) {
        return "date";
    }

    if (
        typeof value ===
        "string"
    ) {
        return Number.isFinite(
            Date.parse(value)
        ) &&
        /[-T:/]/.test(value)
            ? "date"
            : "string";
    }

    if (
        typeof value ===
        "object"
    ) {
        return "object";
    }

    return "other";
}

function fieldValues(
    record,
    path
) {
    if (
        !record ||
        typeof record !==
            "object"
    ) {
        return [];
    }

    const parts =
        normalizeText(path)
            .split(".")
            .filter(Boolean);

    let value =
        record;

    for (const part of parts) {
        if (
            value === null ||
            value === undefined
        ) {
            return [];
        }

        value =
            value[part];
    }

    return flatten(value);
}

function flatten(
    value,
    output = []
) {
    if (Array.isArray(value)) {
        for (const item of value) {
            flatten(
                item,
                output
            );
        }

        return output;
    }

    output.push(value);

    return output;
}

function canonicalKey(value) {
    if (
        value === null ||
        value === undefined
    ) {
        return "null";
    }

    if (
        typeof value ===
        "object"
    ) {
        try {
            return JSON.stringify(
                value
            );
        } catch (_error) {
            return String(value);
        }
    }

    return `${typeof value}:${String(value)}`;
}
