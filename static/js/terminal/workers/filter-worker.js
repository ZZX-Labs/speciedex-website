/*
========================================================================
Speciedex.org
Filter Worker
========================================================================

High-performance worker-side filtering engine for SpeciedexTerminal.

Supports:

    • Nested-field filters
    • Equality, inequality, range, membership, contains, prefix, suffix,
      existence, regular-expression, and wildcard operators
    • Boolean AND, OR, and NOT filter groups
    • Sorting, pagination, field projection, and facets
    • Request cancellation and progress events
    • Structured worker responses and safe error serialization

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

"use strict";

const WORKER_VERSION = "2.0.0";
const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 10000;
const MAX_RECORDS = 1000000;
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
        const error =
            new Error(
                "Filter worker request cancelled."
            );

        error.name = "AbortError";
        error.code =
            "FILTER_WORKER_CANCELLED";

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
        case "filter":
            return filterRecords(
                payload,
                id
            );

        case "explain":
            return {
                normalized:
                    normalizeFilter(
                        payload.filters ??
                        payload.filter ??
                        {}
                    )
            };

        case "facets":
            return {
                facets:
                    buildFacets(
                        Array.isArray(
                            payload.records
                        )
                            ? payload.records
                            : [],
                        payload.fields,
                        payload.limit
                    )
            };

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
                `Unsupported filter operation: ${type || "(empty)"}`
            );
    }
}

async function filterRecords(
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
            `Filter record limit exceeded: ${records.length} > ${MAX_RECORDS}.`
        );
    }

    const filter =
        normalizeFilter(
            payload.filters ??
            payload.filter ??
            {}
        );

    const matches = [];

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        assertActive(id);

        const record =
            records[index];

        if (
            evaluateFilter(
                record,
                filter
            )
        ) {
            matches.push({
                record,
                index
            });
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
                        "filter",
                    completed:
                        index,
                    total:
                        records.length
                }
            );

            await Promise.resolve();
        }
    }

    if (payload.sort) {
        matches.sort(
            (left, right) =>
                compareRecords(
                    left.record,
                    right.record,
                    payload.sort,
                    payload.order
                ) ||
                left.index -
                right.index
        );
    }

    const total =
        matches.length;

    const limit =
        clampInteger(
            payload.limit,
            DEFAULT_LIMIT,
            1,
            MAX_LIMIT
        );

    const offset =
        clampInteger(
            payload.offset,
            0,
            0,
            Number.MAX_SAFE_INTEGER
        );

    const page =
        clampInteger(
            payload.page,
            1,
            1,
            Number.MAX_SAFE_INTEGER
        );

    const effectiveOffset =
        offset > 0
            ? offset
            : (
                page - 1
            ) * limit;

    const selected =
        payload.all === true
            ? matches
            : matches.slice(
                effectiveOffset,
                effectiveOffset +
                    limit
            );

    const facetRecords =
        payload.facetsScope ===
            "page"
            ? selected.map(
                item =>
                    item.record
            )
            : matches.map(
                item =>
                    item.record
            );

    return {
        total,
        offset:
            payload.all === true
                ? 0
                : effectiveOffset,
        limit:
            payload.all === true
                ? total
                : limit,
        page:
            payload.all === true
                ? 1
                : Math.floor(
                    effectiveOffset /
                    limit
                ) + 1,
        pages:
            payload.all === true
                ? 1
                : Math.ceil(
                    total /
                    limit
                ),
        records:
            selected.map(
                item =>
                    projectRecord(
                        item.record,
                        payload.select
                    )
            ),
        indexes:
            payload.includeIndexes ===
            true
                ? selected.map(
                    item =>
                        item.index
                )
                : undefined,
        facets:
            buildFacets(
                facetRecords,
                payload.facets,
                payload.facetLimit
            ),
        elapsed_ms:
            performance.now() -
            startedAt
    };
}

function normalizeFilter(filter) {
    if (
        filter === null ||
        filter === undefined
    ) {
        return {
            type: "all"
        };
    }

    if (Array.isArray(filter)) {
        return {
            type: "and",
            values:
                filter.map(
                    normalizeFilter
                )
        };
    }

    if (
        typeof filter !== "object"
    ) {
        throw new TypeError(
            "Filter must be an object or array."
        );
    }

    if (
        "$and" in filter
    ) {
        return {
            type: "and",
            values:
                asArray(
                    filter.$and
                ).map(
                    normalizeFilter
                )
        };
    }

    if (
        "$or" in filter
    ) {
        return {
            type: "or",
            values:
                asArray(
                    filter.$or
                ).map(
                    normalizeFilter
                )
        };
    }

    if (
        "$not" in filter
    ) {
        return {
            type: "not",
            value:
                normalizeFilter(
                    filter.$not
                )
        };
    }

    const values = [];

    for (
        const [
            field,
            condition
        ] of Object.entries(
            filter
        )
    ) {
        if (
            field === "$and" ||
            field === "$or" ||
            field === "$not"
        ) {
            continue;
        }

        values.push({
            type: "condition",
            field,
            condition:
                normalizeCondition(
                    condition
                )
        });
    }

    if (!values.length) {
        return {
            type: "all"
        };
    }

    if (values.length === 1) {
        return values[0];
    }

    return {
        type: "and",
        values
    };
}

function normalizeCondition(
    condition
) {
    if (Array.isArray(condition)) {
        return {
            operator: "$in",
            value:
                condition
        };
    }

    if (
        condition &&
        typeof condition ===
            "object" &&
        !(condition instanceof RegExp)
    ) {
        const entries =
            Object.entries(
                condition
            );

        if (
            entries.some(
                ([key]) =>
                    key.startsWith("$")
            )
        ) {
            return {
                operator:
                    "$group",
                value:
                    entries.map(
                        ([
                            operator,
                            value
                        ]) => ({
                            operator,
                            value
                        })
                    )
            };
        }

        if (
            "min" in condition ||
            "max" in condition
        ) {
            return {
                operator:
                    "$group",
                value: [
                    ...(
                        "min" in condition
                            ? [{
                                operator:
                                    "$gte",
                                value:
                                    condition.min
                            }]
                            : []
                    ),
                    ...(
                        "max" in condition
                            ? [{
                                operator:
                                    "$lte",
                                value:
                                    condition.max
                            }]
                            : []
                    )
                ]
            };
        }
    }

    return {
        operator:
            "$contains",
        value:
            condition
    };
}

function evaluateFilter(
    record,
    filter
) {
    switch (filter.type) {
        case "all":
            return true;

        case "and":
            return filter.values.every(
                value =>
                    evaluateFilter(
                        record,
                        value
                    )
            );

        case "or":
            return filter.values.some(
                value =>
                    evaluateFilter(
                        record,
                        value
                    )
            );

        case "not":
            return !evaluateFilter(
                record,
                filter.value
            );

        case "condition":
            return evaluateCondition(
                record,
                filter.field,
                filter.condition
            );

        default:
            return false;
    }
}

function evaluateCondition(
    record,
    field,
    condition
) {
    const values =
        fieldValues(
            record,
            field
        );

    if (
        condition.operator ===
        "$group"
    ) {
        return condition.value.every(
            item =>
                values.some(
                    value =>
                        compare(
                            value,
                            item.operator,
                            item.value
                        )
                )
        );
    }

    if (
        condition.operator ===
        "$exists"
    ) {
        const exists =
            values.some(
                value =>
                    value !==
                        undefined &&
                    value !== null &&
                    value !== ""
            );

        return Boolean(
            condition.value
        )
            ? exists
            : !exists;
    }

    if (
        condition.operator ===
        "$all"
    ) {
        const expected =
            asArray(
                condition.value
            );

        return expected.every(
            expectedValue =>
                values.some(
                    value =>
                        compare(
                            value,
                            "$eq",
                            expectedValue
                        )
                )
        );
    }

    if (
        condition.operator ===
        "$none"
    ) {
        const expected =
            asArray(
                condition.value
            );

        return expected.every(
            expectedValue =>
                !values.some(
                    value =>
                        compare(
                            value,
                            "$eq",
                            expectedValue
                        )
                )
        );
    }

    return values.some(
        value =>
            compare(
                value,
                condition.operator,
                condition.value
            )
    );
}

function compare(
    actual,
    operator,
    expected
) {
    switch (operator) {
        case "$eq":
            return equal(
                actual,
                expected
            );

        case "$ne":
            return !equal(
                actual,
                expected
            );

        case "$gt":
            return comparable(
                actual,
                expected
            ) > 0;

        case "$gte":
            return comparable(
                actual,
                expected
            ) >= 0;

        case "$lt":
            return comparable(
                actual,
                expected
            ) < 0;

        case "$lte":
            return comparable(
                actual,
                expected
            ) <= 0;

        case "$in":
            return asArray(
                expected
            ).some(
                value =>
                    equal(
                        actual,
                        value
                    )
            );

        case "$nin":
            return !asArray(
                expected
            ).some(
                value =>
                    equal(
                        actual,
                        value
                    )
            );

        case "$contains":
            return normalizeText(
                actual
            )
                .toLowerCase()
                .includes(
                    normalizeText(
                        expected
                    ).toLowerCase()
                );

        case "$startsWith":
            return normalizeText(
                actual
            )
                .toLowerCase()
                .startsWith(
                    normalizeText(
                        expected
                    ).toLowerCase()
                );

        case "$endsWith":
            return normalizeText(
                actual
            )
                .toLowerCase()
                .endsWith(
                    normalizeText(
                        expected
                    ).toLowerCase()
                );

        case "$regex":
            return regexFrom(
                expected
            ).test(
                normalizeText(
                    actual
                )
            );

        case "$wildcard":
            return wildcardRegex(
                expected
            ).test(
                normalizeText(
                    actual
                )
            );

        case "$between": {
            const range =
                asArray(
                    expected
                );

            if (
                range.length < 2
            ) {
                return false;
            }

            return (
                comparable(
                    actual,
                    range[0]
                ) >= 0 &&
                comparable(
                    actual,
                    range[1]
                ) <= 0
            );
        }

        case "$size":
            return (
                (
                    Array.isArray(actual) ||
                    typeof actual ===
                        "string"
                ) &&
                actual.length ===
                    Number(expected)
            );

        case "$type":
            return typeOf(
                actual
            ) ===
                normalizeText(
                    expected
                ).toLowerCase();

        default:
            throw new Error(
                `Unsupported filter operator: ${operator}`
            );
    }
}

function equal(
    left,
    right
) {
    if (left === right) {
        return true;
    }

    if (
        left === null ||
        left === undefined ||
        right === null ||
        right === undefined
    ) {
        return false;
    }

    if (
        Number.isFinite(
            Number(left)
        ) &&
        Number.isFinite(
            Number(right)
        )
    ) {
        return (
            Number(left) ===
            Number(right)
        );
    }

    if (
        typeof left ===
            "boolean" ||
        typeof right ===
            "boolean"
    ) {
        return (
            Boolean(left) ===
            Boolean(right)
        );
    }

    return (
        normalizeText(left)
            .toLowerCase() ===
        normalizeText(right)
            .toLowerCase()
    );
}

function comparable(
    left,
    right
) {
    if (
        Number.isFinite(
            Number(left)
        ) &&
        Number.isFinite(
            Number(right)
        )
    ) {
        return (
            Number(left) -
            Number(right)
        );
    }

    const leftDate =
        Date.parse(left);

    const rightDate =
        Date.parse(right);

    if (
        Number.isFinite(leftDate) &&
        Number.isFinite(rightDate)
    ) {
        return (
            leftDate -
            rightDate
        );
    }

    return normalizeText(left)
        .localeCompare(
            normalizeText(right),
            undefined,
            {
                numeric: true,
                sensitivity:
                    "base"
            }
        );
}

function typeOf(value) {
    if (value === null) {
        return "null";
    }

    if (Array.isArray(value)) {
        return "array";
    }

    return typeof value;
}

function regexFrom(value) {
    if (
        value instanceof RegExp
    ) {
        return new RegExp(
            value.source,
            value.flags.replace(
                /g/g,
                ""
            )
        );
    }

    if (
        value &&
        typeof value ===
            "object" &&
        value.pattern
    ) {
        return new RegExp(
            value.pattern,
            normalizeText(
                value.flags
            ).replace(
                /g/g,
                ""
            )
        );
    }

    const text =
        normalizeText(value);

    const match =
        text.match(
            /^\/((?:\\.|[^/])+)\/([gimsuy]*)$/
        );

    if (match) {
        return new RegExp(
            match[1],
            match[2].replace(
                /g/g,
                ""
            )
        );
    }

    return new RegExp(
        text,
        "i"
    );
}

function wildcardRegex(value) {
    const escaped =
        normalizeText(value)
            .replace(
                /[.+^${}()|[\]\\]/g,
                "\\$&"
            )
            .replace(
                /\*/g,
                ".*"
            )
            .replace(
                /\?/g,
                "."
            );

    return new RegExp(
        `^${escaped}$`,
        "i"
    );
}

function compareRecords(
    left,
    right,
    field,
    order = "asc"
) {
    const direction =
        String(order)
            .toLowerCase() ===
        "desc"
            ? -1
            : 1;

    const a =
        fieldValues(
            left,
            field
        )[0];

    const b =
        fieldValues(
            right,
            field
        )[0];

    if (a === b) {
        return 0;
    }

    if (
        a === undefined ||
        a === null
    ) {
        return 1;
    }

    if (
        b === undefined ||
        b === null
    ) {
        return -1;
    }

    return (
        comparable(
            a,
            b
        ) * direction
    );
}

function buildFacets(
    records,
    requested,
    limit = 100
) {
    const fields =
        Array.isArray(requested)
            ? [
                ...new Set(
                    requested
                        .map(
                            normalizeText
                        )
                        .filter(Boolean)
                )
            ]
            : [];

    if (!fields.length) {
        return {};
    }

    const facetLimit =
        clampInteger(
            limit,
            100,
            1,
            1000
        );

    const facets = {};

    for (const field of fields) {
        const counts =
            new Map();

        for (const record of records) {
            const seen =
                new Set();

            for (
                const value of
                fieldValues(
                    record,
                    field
                )
            ) {
                const key =
                    normalizeText(value);

                if (
                    !key ||
                    seen.has(key)
                ) {
                    continue;
                }

                seen.add(key);

                counts.set(
                    key,
                    (
                        counts.get(key) ||
                        0
                    ) + 1
                );
            }
        }

        facets[field] =
            [...counts.entries()]
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
                    facetLimit
                )
                .map(
                    ([
                        value,
                        count
                    ]) => ({
                        value,
                        count
                    })
                );
    }

    return facets;
}

function projectRecord(
    record,
    fields
) {
    if (
        !Array.isArray(fields) ||
        !fields.length
    ) {
        return record;
    }

    const output = {};

    for (const field of fields) {
        const values =
            fieldValues(
                record,
                field
            );

        output[field] =
            values.length <= 1
                ? values[0] ?? null
                : values;
    }

    return output;
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
        output.push(value);
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

function asArray(value) {
    return Array.isArray(value)
        ? value
        : [value];
}
