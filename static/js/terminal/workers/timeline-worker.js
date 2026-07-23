/*
========================================================================
Speciedex.org
Timeline Worker
========================================================================

High-performance worker-side timeline aggregation for SpeciedexTerminal.

Supports:

    • Nested date-field extraction
    • Year, quarter, month, week, day, hour, and minute buckets
    • Date-range filtering
    • Grouped and cumulative series
    • Optional empty-bucket filling
    • Request cancellation and progress events
    • Structured worker responses and safe error serialization

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

"use strict";

const WORKER_VERSION = "2.0.0";
const MAX_RECORDS = 1000000;
const MAX_GROUPS = 10000;
const PROGRESS_INTERVAL = 5000;

const SUPPORTED_BUCKETS = Object.freeze([
    "year",
    "quarter",
    "month",
    "week",
    "day",
    "hour",
    "minute"
]);

const activeRequests = new Map();

function normalizeText(value) {
    return String(value ?? "").trim();
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
                error:
                    serializeError(error)
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
            "Timeline worker request cancelled."
        );

        error.name = "AbortError";
        error.code =
            "TIMELINE_WORKER_CANCELLED";

        throw error;
    }
}

self.addEventListener(
    "message",
    async event => {
        const message =
            event.data || {};

        const id =
            message.id ?? null;

        const type =
            normalizeText(
                message.type
            ).toLowerCase();

        if (type === "cancel") {
            const targetId =
                message.payload?.id ??
                message.targetId ??
                id;

            if (
                activeRequests.has(
                    targetId
                )
            ) {
                activeRequests.get(
                    targetId
                ).cancelled = true;
            }

            respond(
                id,
                {
                    cancelled: true,
                    targetId
                }
            );

            return;
        }

        activeRequests.set(
            id,
            {
                cancelled: false,
                startedAt:
                    performance.now()
            }
        );

        try {
            const result =
                await handle(
                    type,
                    message.payload || {},
                    id
                );

            respond(
                id,
                result
            );
        } catch (error) {
            respond(
                id,
                null,
                error
            );
        } finally {
            activeRequests.delete(id);
        }
    }
);

async function handle(
    type,
    payload,
    id
) {
    switch (type) {
        case "timeline":
        case "aggregate":
            return buildTimeline(
                payload,
                id
            );

        case "range":
            return calculateRange(
                payload,
                id
            );

        case "status":
            return {
                ready: true,
                workerVersion:
                    WORKER_VERSION,
                buckets:
                    [...SUPPORTED_BUCKETS],
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
                `Unsupported timeline operation: ${type || "(empty)"}`
            );
    }
}

async function buildTimeline(
    payload = {},
    id = null
) {
    const startedAt =
        performance.now();

    const records =
        Array.isArray(
            payload.records
        )
            ? payload.records
            : [];

    if (
        records.length >
        MAX_RECORDS
    ) {
        throw new RangeError(
            `Timeline record limit exceeded: ${records.length} > ${MAX_RECORDS}.`
        );
    }

    const field =
        normalizeText(
            payload.field ||
            "date"
        );

    const groupBy =
        normalizeText(
            payload.groupBy ??
            payload.group
        );

    const bucket =
        normalizeBucket(
            payload.bucket ||
            "year"
        );

    const from =
        normalizeBoundary(
            payload.from ??
            payload.start,
            false
        );

    const to =
        normalizeBoundary(
            payload.to ??
            payload.end,
            true
        );

    if (
        from !== null &&
        to !== null &&
        from > to
    ) {
        throw new RangeError(
            "Timeline start date must not be later than the end date."
        );
    }

    const groups =
        new Map();

    let valid = 0;
    let invalid = 0;
    let excluded = 0;

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        const record =
            records[index];

        const values =
            fieldValues(
                record,
                field
            );

        if (!values.length) {
            invalid += 1;
            continue;
        }

        let matchedDate = false;

        for (const rawValue of values) {
            const timestamp =
                parseTimestamp(
                    rawValue
                );

            if (timestamp === null) {
                continue;
            }

            matchedDate = true;

            if (
                (
                    from !== null &&
                    timestamp < from
                ) ||
                (
                    to !== null &&
                    timestamp > to
                )
            ) {
                excluded += 1;
                continue;
            }

            const date =
                new Date(timestamp);

            const bucketInfo =
                bucketForDate(
                    date,
                    bucket
                );

            const groupValues =
                groupBy
                    ? fieldValues(
                        record,
                        groupBy
                    )
                    : ["all"];

            const normalizedGroups =
                groupValues.length
                    ? groupValues
                    : ["unknown"];

            for (
                const groupValue of
                normalizedGroups
            ) {
                const groupKey =
                    normalizeText(
                        groupValue
                    ) || "unknown";

                if (
                    !groups.has(
                        groupKey
                    )
                ) {
                    if (
                        groups.size >=
                        MAX_GROUPS
                    ) {
                        throw new RangeError(
                            `Timeline group limit exceeded: ${MAX_GROUPS}.`
                        );
                    }

                    groups.set(
                        groupKey,
                        new Map()
                    );
                }

                const series =
                    groups.get(
                        groupKey
                    );

                const current =
                    series.get(
                        bucketInfo.key
                    ) || {
                        key:
                            bucketInfo.key,
                        start:
                            bucketInfo.start,
                        end:
                            bucketInfo.end,
                        count: 0,
                        records: []
                    };

                current.count += 1;

                if (
                    payload.includeRecords ===
                    true
                ) {
                    current.records.push(
                        record
                    );
                }

                series.set(
                    bucketInfo.key,
                    current
                );
            }

            valid += 1;
        }

        if (!matchedDate) {
            invalid += 1;
        }

        if (
            payload.progress === true &&
            index > 0 &&
            index %
                PROGRESS_INTERVAL ===
                0
        ) {
            post(
                "progress",
                id,
                {
                    phase:
                        "timeline",
                    completed:
                        index,
                    total:
                        records.length
                }
            );

            await Promise.resolve();
        }
    }

    const series =
        [...groups.entries()]
            .map(
                ([
                    group,
                    values
                ]) => {
                    let points =
                        [...values.values()]
                            .sort(
                                (left, right) =>
                                    Date.parse(
                                        left.start
                                    ) -
                                    Date.parse(
                                        right.start
                                    )
                            );

                    if (
                        payload.fill ===
                        true &&
                        points.length
                    ) {
                        points =
                            fillMissingBuckets(
                                points,
                                bucket,
                                payload.includeRecords ===
                                true
                            );
                    }

                    if (
                        payload.cumulative ===
                        true
                    ) {
                        let total = 0;

                        points =
                            points.map(
                                point => {
                                    total +=
                                        point.count;

                                    return {
                                        ...point,
                                        cumulative:
                                            total
                                    };
                                }
                            );
                    }

                    return {
                        group,
                        points,
                        total:
                            points.reduce(
                                (
                                    sum,
                                    point
                                ) =>
                                    sum +
                                    point.count,
                                0
                            )
                    };
                }
            )
            .sort(
                (left, right) =>
                    right.total -
                    left.total ||
                    left.group
                        .localeCompare(
                            right.group
                        )
            );

    const flat =
        groupBy
            ? undefined
            : (
                series[0]?.points ||
                []
            );

    return {
        field,
        groupBy:
            groupBy || null,
        bucket,
        records:
            records.length,
        valid,
        invalid,
        excluded,
        groups:
            series.length,
        series,
        timeline:
            flat,
        range:
            timelineRange(
                series
            ),
        elapsed_ms:
            performance.now() -
            startedAt
    };
}

async function calculateRange(
    payload = {},
    id = null
) {
    const records =
        Array.isArray(
            payload.records
        )
            ? payload.records
            : [];

    const field =
        normalizeText(
            payload.field ||
            "date"
        );

    let minimum = null;
    let maximum = null;
    let valid = 0;
    let invalid = 0;

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        const values =
            fieldValues(
                records[index],
                field
            );

        let recordValid = false;

        for (const value of values) {
            const timestamp =
                parseTimestamp(value);

            if (timestamp === null) {
                continue;
            }

            recordValid = true;
            valid += 1;

            minimum =
                minimum === null
                    ? timestamp
                    : Math.min(
                        minimum,
                        timestamp
                    );

            maximum =
                maximum === null
                    ? timestamp
                    : Math.max(
                        maximum,
                        timestamp
                    );
        }

        if (!recordValid) {
            invalid += 1;
        }

        if (
            payload.progress === true &&
            index > 0 &&
            index %
                PROGRESS_INTERVAL ===
                0
        ) {
            post(
                "progress",
                id,
                {
                    phase:
                        "range",
                    completed:
                        index,
                    total:
                        records.length
                }
            );

            await Promise.resolve();
        }
    }

    return {
        field,
        records:
            records.length,
        valid,
        invalid,
        minimum:
            minimum === null
                ? null
                : new Date(
                    minimum
                ).toISOString(),
        maximum:
            maximum === null
                ? null
                : new Date(
                    maximum
                ).toISOString(),
        duration_ms:
            minimum === null ||
            maximum === null
                ? null
                : maximum -
                  minimum
    };
}

function normalizeBucket(value) {
    const bucket =
        normalizeText(value)
            .toLowerCase();

    if (
        !SUPPORTED_BUCKETS.includes(
            bucket
        )
    ) {
        throw new TypeError(
            `Unsupported timeline bucket: ${value}`
        );
    }

    return bucket;
}

function normalizeBoundary(
    value,
    endOfRange
) {
    if (
        value === undefined ||
        value === null ||
        value === ""
    ) {
        return null;
    }

    const timestamp =
        parseTimestamp(value);

    if (timestamp === null) {
        throw new TypeError(
            `Invalid timeline boundary: ${value}`
        );
    }

    if (!endOfRange) {
        return timestamp;
    }

    const date =
        new Date(timestamp);

    if (
        typeof value ===
            "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(
            value
        )
    ) {
        date.setUTCHours(
            23,
            59,
            59,
            999
        );
    }

    return date.getTime();
}

function parseTimestamp(value) {
    if (
        value instanceof Date
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
        "number" &&
        Number.isFinite(value)
    ) {
        return value;
    }

    const timestamp =
        Date.parse(value);

    return Number.isFinite(
        timestamp
    )
        ? timestamp
        : null;
}

function bucketForDate(
    date,
    bucket
) {
    const start =
        new Date(
            date.getTime()
        );

    switch (bucket) {
        case "year":
            start.setUTCMonth(
                0,
                1
            );

            start.setUTCHours(
                0,
                0,
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                String(
                    start.getUTCFullYear()
                )
            );

        case "quarter": {
            const quarter =
                Math.floor(
                    start.getUTCMonth() /
                    3
                );

            start.setUTCMonth(
                quarter * 3,
                1
            );

            start.setUTCHours(
                0,
                0,
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                `${start.getUTCFullYear()}-Q${quarter + 1}`
            );
        }

        case "month":
            start.setUTCDate(1);

            start.setUTCHours(
                0,
                0,
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                `${start.getUTCFullYear()}-${String(
                    start.getUTCMonth() +
                    1
                ).padStart(2, "0")}`
            );

        case "week": {
            start.setUTCHours(
                0,
                0,
                0,
                0
            );

            const day =
                start.getUTCDay();

            const offset =
                day === 0
                    ? -6
                    : 1 - day;

            start.setUTCDate(
                start.getUTCDate() +
                offset
            );

            const week =
                isoWeek(
                    start
                );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                `${week.year}-W${String(
                    week.week
                ).padStart(2, "0")}`
            );
        }

        case "day":
            start.setUTCHours(
                0,
                0,
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                start
                    .toISOString()
                    .slice(0, 10)
            );

        case "hour":
            start.setUTCMinutes(
                0,
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                start
                    .toISOString()
                    .slice(0, 13) +
                    ":00"
            );

        case "minute":
            start.setUTCSeconds(
                0,
                0
            );

            return bucketDescriptor(
                start,
                addBucket(
                    start,
                    bucket,
                    1
                ),
                start
                    .toISOString()
                    .slice(0, 16)
            );

        default:
            throw new Error(
                `Unsupported timeline bucket: ${bucket}`
            );
    }
}

function bucketDescriptor(
    start,
    next,
    key
) {
    return {
        key,
        start:
            start.toISOString(),
        end:
            new Date(
                next.getTime() -
                1
            ).toISOString()
    };
}

function addBucket(
    date,
    bucket,
    amount
) {
    const output =
        new Date(
            date.getTime()
        );

    switch (bucket) {
        case "year":
            output.setUTCFullYear(
                output.getUTCFullYear() +
                amount
            );
            break;

        case "quarter":
            output.setUTCMonth(
                output.getUTCMonth() +
                amount * 3
            );
            break;

        case "month":
            output.setUTCMonth(
                output.getUTCMonth() +
                amount
            );
            break;

        case "week":
            output.setUTCDate(
                output.getUTCDate() +
                amount * 7
            );
            break;

        case "day":
            output.setUTCDate(
                output.getUTCDate() +
                amount
            );
            break;

        case "hour":
            output.setUTCHours(
                output.getUTCHours() +
                amount
            );
            break;

        case "minute":
            output.setUTCMinutes(
                output.getUTCMinutes() +
                amount
            );
            break;

        default:
            throw new Error(
                `Unsupported timeline bucket: ${bucket}`
            );
    }

    return output;
}

function fillMissingBuckets(
    points,
    bucket,
    includeRecords
) {
    if (!points.length) {
        return [];
    }

    const byStart =
        new Map(
            points.map(
                point => [
                    point.start,
                    point
                ]
            )
        );

    const output = [];

    let cursor =
        new Date(
            points[0].start
        );

    const end =
        Date.parse(
            points[
                points.length - 1
            ].start
        );

    while (
        cursor.getTime() <=
        end
    ) {
        const descriptor =
            bucketForDate(
                cursor,
                bucket
            );

        output.push(
            byStart.get(
                descriptor.start
            ) || {
                key:
                    descriptor.key,
                start:
                    descriptor.start,
                end:
                    descriptor.end,
                count: 0,
                records:
                    includeRecords
                        ? []
                        : undefined
            }
        );

        cursor =
            addBucket(
                cursor,
                bucket,
                1
            );
    }

    return output;
}

function timelineRange(
    series
) {
    let minimum = null;
    let maximum = null;

    for (const group of series) {
        for (
            const point of
            group.points
        ) {
            const start =
                Date.parse(
                    point.start
                );

            const end =
                Date.parse(
                    point.end
                );

            minimum =
                minimum === null
                    ? start
                    : Math.min(
                        minimum,
                        start
                    );

            maximum =
                maximum === null
                    ? end
                    : Math.max(
                        maximum,
                        end
                    );
        }
    }

    return {
        start:
            minimum === null
                ? null
                : new Date(
                    minimum
                ).toISOString(),
        end:
            maximum === null
                ? null
                : new Date(
                    maximum
                ).toISOString()
    };
}

function isoWeek(date) {
    const target =
        new Date(
            Date.UTC(
                date.getUTCFullYear(),
                date.getUTCMonth(),
                date.getUTCDate()
            )
        );

    const dayNumber =
        (
            target.getUTCDay() +
            6
        ) % 7;

    target.setUTCDate(
        target.getUTCDate() -
        dayNumber +
        3
    );

    const firstThursday =
        new Date(
            Date.UTC(
                target.getUTCFullYear(),
                0,
                4
            )
        );

    const firstDayNumber =
        (
            firstThursday.getUTCDay() +
            6
        ) % 7;

    firstThursday.setUTCDate(
        firstThursday.getUTCDate() -
        firstDayNumber +
        3
    );

    return {
        year:
            target.getUTCFullYear(),
        week:
            1 +
            Math.round(
                (
                    target -
                    firstThursday
                ) /
                604800000
            )
    };
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
    if (
        value === undefined ||
        value === null
    ) {
        return output;
    }

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
