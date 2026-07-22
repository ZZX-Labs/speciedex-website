/*
========================================================================
Speciedex.org
Search Worker
========================================================================

High-performance worker-side search engine for SpeciedexTerminal.

Supports:

    • Raw query strings or pre-parsed query plans
    • Scientific and common names
    • Taxonomic ranks and lineages
    • Speciedex IDs, provider IDs, hashes, UUIDs, DOIs, and accessions
    • Countries, regions, localities, coordinates, habitats, and biomes
    • Boolean AND / OR / NOT operators
    • Comparisons: =, !=, >, >=, <, <=
    • Wildcards and regular expressions
    • Fuzzy matching
    • Sorting, offsets, pages, and limits
    • Field facets and structured result metadata
    • Reusable in-worker indexes

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

"use strict";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 1000;

const FIELD_ALIASES = Object.freeze({
    id: "speciedex_id",
    key: "speciedex_id",
    sid: "speciedex_id",
    speciedex: "speciedex_id",
    speciedex_id: "speciedex_id",

    scientific: "scientific_name",
    scientific_name: "scientific_name",
    canonical: "scientific_name",
    accepted: "scientific_name",
    name: "name",

    common: "common_name",
    common_name: "common_name",
    vernacular: "common_name",

    synonym: "synonyms",
    synonyms: "synonyms",

    rank: "rank",
    domain: "domain",
    kingdom: "kingdom",
    phylum: "phylum",
    class: "class",
    order: "order",
    family: "family",
    tribe: "tribe",
    genus: "genus",
    species: "species",
    subspecies: "subspecies",
    variety: "variety",
    form: "form",
    clade: "clade",

    provider: "provider",
    source: "provider",
    provider_id: "provider_id",

    country: "country",
    nation: "country",
    continent: "continent",
    state: "state",
    province: "state",
    county: "county",
    city: "city",
    locality: "locality",
    location: "location",
    island: "island",
    ocean: "ocean",
    sea: "sea",
    river: "river",
    lake: "lake",

    habitat: "habitat",
    biome: "biome",
    ecosystem: "ecosystem",

    conservation: "conservation_status",
    status: "conservation_status",
    iucn: "iucn_status",

    author: "authority",
    authority: "authority",
    year: "year",

    hash: "hash",
    checksum: "checksum",
    sha256: "sha256",
    sha512: "sha512",
    md5: "md5",
    cid: "cid",
    uuid: "uuid",
    doi: "doi",

    taxid: "taxid",
    gbif: "gbif_id",
    ncbi: "ncbi_id",
    itis: "itis_id",
    worms: "worms_id",
    col: "col_id",
    inat: "inat_id",
    iucn_id: "iucn_id",
    eol: "eol_id",
    bold: "bold_id",
    wikidata: "wikidata_id",
    wikipedia: "wikipedia",

    genome: "genome",
    gene: "gene",
    accession: "accession",

    volume: "volume",
    release: "release",
    created: "created_at",
    updated: "updated_at",

    confidence: "confidence",
    overlap: "overlap",
    latitude: "latitude",
    longitude: "longitude",
    elevation: "elevation",
    depth: "depth",

    has: "has"
});

const DEFAULT_TEXT_FIELDS = Object.freeze([
    "speciedex_id",
    "scientific_name",
    "common_name",
    "name",
    "canonical_name",
    "accepted_name",
    "synonyms",
    "authority",
    "description",
    "keywords",
    "tags",
    "rank",
    "domain",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "tribe",
    "genus",
    "species",
    "subspecies",
    "country",
    "state",
    "locality",
    "continent",
    "habitat",
    "biome",
    "ecosystem",
    "provider"
]);

const IDENTIFIER_PATTERNS = Object.freeze([
    ["sha256", /^[a-f0-9]{64}$/i],
    ["sha512", /^[a-f0-9]{128}$/i],
    ["md5", /^[a-f0-9]{32}$/i],
    [
        "uuid",
        /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    ],
    ["doi", /^10\.\d{4,9}\/[-._;()/:a-z0-9]+$/i],
    ["wikidata_id", /^Q\d+$/i],
    ["speciedex_id", /^(?:spx|speciedex)[-_:][a-z0-9._:-]+$/i]
]);

const state = {
    records: [],
    fields: [],
    fieldIndexes: new Map(),
    fullText: [],
    version: 0
};

function respond(id, result, error = null) {
    self.postMessage(
        error
            ? {
                id,
                error: {
                    name: error.name || "Error",
                    message: error.message || String(error),
                    stack: error.stack || null
                }
            }
            : {
                id,
                result
            }
    );
}

self.addEventListener("message", async event => {
    const message = event.data || {};
    const id = message.id ?? null;

    try {
        const result = await handle(
            message.type,
            message.payload || {}
        );

        respond(id, result);
    } catch (error) {
        respond(id, null, error);
    }
});

async function handle(type, payload) {
    switch (type) {
        case "build":
        case "index":
            return buildIndex(payload);

        case "search":
            return search(payload);

        case "explain":
            return parseQuery(
                payload.query || "",
                payload
            );

        case "fields":
            return {
                aliases: FIELD_ALIASES,
                defaultTextFields: DEFAULT_TEXT_FIELDS,
                indexedFields: [...state.fields],
                recordCount: state.records.length,
                version: state.version
            };

        case "clear":
            clearIndex();
            return {
                cleared: true,
                version: state.version
            };

        case "status":
            return {
                ready: true,
                records: state.records.length,
                fields: state.fields.length,
                indexes: state.fieldIndexes.size,
                version: state.version
            };

        default:
            throw new Error(
                `Unsupported search operation: ${type}`
            );
    }
}

function buildIndex(payload = {}) {
    const records = Array.isArray(payload.records)
        ? payload.records
        : [];

    const fields =
        Array.isArray(payload.fields) &&
        payload.fields.length
            ? payload.fields.map(normalizeField)
            : discoverFields(records);

    state.records = records;
    state.fields = fields;
    state.fieldIndexes = new Map();
    state.fullText = new Array(records.length);

    for (const field of fields) {
        state.fieldIndexes.set(
            field,
            new Map()
        );
    }

    records.forEach((record, index) => {
        const fullTextParts = [];

        for (const field of fields) {
            const values = fieldValues(
                record,
                field
            );

            const indexMap =
                state.fieldIndexes.get(field);

            for (const value of values) {
                const normalized =
                    normalizeText(value)
                        .toLowerCase();

                if (!normalized) {
                    continue;
                }

                fullTextParts.push(
                    normalized
                );

                if (!indexMap.has(normalized)) {
                    indexMap.set(
                        normalized,
                        []
                    );
                }

                indexMap.get(normalized).push(
                    index
                );
            }
        }

        state.fullText[index] =
            fullTextParts.join(" ");
    });

    state.version += 1;

    return {
        records: state.records.length,
        fields: [...state.fields],
        indexes: state.fieldIndexes.size,
        version: state.version
    };
}

function clearIndex() {
    state.records = [];
    state.fields = [];
    state.fieldIndexes = new Map();
    state.fullText = [];
    state.version += 1;
}

async function search(payload = {}) {
    const started = performance.now();

    const records = Array.isArray(payload.records)
        ? payload.records
        : state.records;

    const fields =
        Array.isArray(payload.fields) &&
        payload.fields.length
            ? payload.fields.map(normalizeField)
            : (
                state.fields.length
                    ? state.fields
                    : discoverFields(records)
            );

    const plan =
        payload.plan &&
        typeof payload.plan === "object"
            ? normalizePlan(payload.plan)
            : parseQuery(
                payload.query || "",
                payload
            );

    const source =
        records === state.records
            ? "worker-index"
            : "worker-records";

    const matches = [];

    for (
        let index = 0;
        index < records.length;
        index += 1
    ) {
        const record = records[index];

        if (
            evaluateRecord(
                record,
                plan,
                fields,
                index,
                records === state.records
            )
        ) {
            matches.push({
                record,
                score: scoreRecord(
                    record,
                    plan,
                    fields,
                    index,
                    records === state.records
                ),
                index
            });
        }
    }

    if (plan.sort) {
        matches.sort((left, right) =>
            compareRecords(
                left.record,
                right.record,
                normalizeField(plan.sort),
                plan.order
            )
        );
    } else {
        matches.sort((left, right) =>
            right.score - left.score ||
            left.index - right.index
        );
    }

    const total = matches.length;

    const offset =
        plan.offset ||
        (
            (plan.page - 1) *
            plan.limit
        );

    const selected =
        matches.slice(
            offset,
            offset + plan.limit
        );

    const facets =
        buildFacets(
            matches.map(item => item.record),
            payload.facets
        );

    return {
        source,
        query: plan.raw,
        plan,
        total,
        offset,
        limit: plan.limit,
        page: plan.page,
        pages:
            plan.limit > 0
                ? Math.ceil(total / plan.limit)
                : 1,
        records:
            selected.map(item => item.record),
        scores:
            payload.includeScores
                ? selected.map(item => ({
                    index: item.index,
                    score: item.score
                }))
                : undefined,
        facets,
        elapsed_ms:
            performance.now() - started,
        index_version:
            state.version
    };
}

function discoverFields(records) {
    const fields = new Set(
        DEFAULT_TEXT_FIELDS
    );

    for (const record of records) {
        if (
            !record ||
            typeof record !== "object"
        ) {
            continue;
        }

        for (const key of Object.keys(record)) {
            fields.add(
                normalizeField(key)
            );
        }
    }

    return [...fields];
}

function normalizePlan(plan) {
    return {
        raw:
            normalizeText(plan.raw || ""),
        clauses:
            Array.isArray(plan.clauses)
                ? plan.clauses
                : [],
        limit:
            clampInteger(
                plan.limit,
                DEFAULT_LIMIT,
                1,
                MAX_LIMIT
            ),
        offset:
            clampInteger(
                plan.offset,
                0,
                0,
                Number.MAX_SAFE_INTEGER
            ),
        page:
            clampInteger(
                plan.page,
                1,
                1,
                Number.MAX_SAFE_INTEGER
            ),
        sort:
            plan.sort
                ? normalizeField(plan.sort)
                : null,
        order:
            String(plan.order || "asc")
                .toLowerCase() === "desc"
                    ? "desc"
                    : "asc",
        fuzzy:
            plan.fuzzy !== false,
        explain:
            plan.explain === true
    };
}

function parseQuery(input, options = {}) {
    const raw =
        normalizeText(input);

    const tokens =
        tokenize(raw);

    const clauses = [];

    let join = "AND";
    let negated = false;

    for (const token of tokens) {
        const upper =
            token.toUpperCase();

        if (
            upper === "AND" ||
            upper === "OR"
        ) {
            join = upper;
            continue;
        }

        if (upper === "NOT") {
            negated = !negated;
            continue;
        }

        if (token.startsWith("--")) {
            continue;
        }

        if (
            token.startsWith("-") &&
            token.length > 1
        ) {
            clauses.push({
                join,
                ...parseTerm(
                    token.slice(1),
                    true
                )
            });

            join = "AND";
            continue;
        }

        clauses.push({
            join,
            ...parseTerm(
                token,
                negated
            )
        });

        join = "AND";
        negated = false;
    }

    return normalizePlan({
        raw,
        clauses,
        limit:
            options.limit ||
            DEFAULT_LIMIT,
        offset:
            options.offset || 0,
        page:
            options.page || 1,
        sort:
            options.sort || null,
        order:
            options.order || "asc",
        fuzzy:
            options.fuzzy !== false,
        explain:
            options.explain === true
    });
}

function parseTerm(token, negated = false) {
    const comparison =
        token.match(
            /^([a-zA-Z_][a-zA-Z0-9_-]*)(>=|<=|!=|=|>|<|:)(.+)$/
        );

    if (comparison) {
        return {
            type: "term",
            field:
                normalizeField(
                    comparison[1]
                ),
            operator:
                comparison[2] === ":"
                    ? "contains"
                    : comparison[2],
            value:
                parseValue(
                    comparison[3]
                ),
            negated
        };
    }

    const raw =
        unquote(token);

    const identifier =
        detectIdentifier(raw);

    if (identifier) {
        return {
            type: "term",
            field:
                identifier.field,
            operator: "=",
            value:
                parseValue(
                    identifier.value
                ),
            negated,
            inferred: true
        };
    }

    return {
        type: "text",
        fields:
            DEFAULT_TEXT_FIELDS,
        operator: "contains",
        value:
            parseValue(token),
        negated
    };
}

function parseValue(value) {
    const raw =
        unquote(value);

    return {
        raw,
        regex:
            parseRegex(raw),
        wildcard:
            raw.includes("*") ||
            raw.includes("?"),
        number:
            raw !== "" &&
            Number.isFinite(Number(raw))
                ? Number(raw)
                : null
    };
}

function parseRegex(value) {
    const match =
        normalizeText(value)
            .match(
                /^\/(.+)\/([gimsuy]*)$/
            );

    if (!match) {
        return null;
    }

    try {
        return new RegExp(
            match[1],
            match[2]
        );
    } catch (error) {
        throw new Error(
            `Invalid regular expression: ${value}`
        );
    }
}

function detectIdentifier(value) {
    const text =
        normalizeText(value);

    for (const [field, pattern] of IDENTIFIER_PATTERNS) {
        if (pattern.test(text)) {
            return {
                field,
                value: text
            };
        }
    }

    return null;
}

function evaluateRecord(
    record,
    plan,
    fields,
    index,
    indexed
) {
    if (!plan.clauses.length) {
        return true;
    }

    let result = null;

    for (const clause of plan.clauses) {
        const matched =
            evaluateClause(
                record,
                clause,
                plan.fuzzy,
                fields,
                index,
                indexed
            );

        if (result === null) {
            result = matched;
        } else if (clause.join === "OR") {
            result =
                result ||
                matched;
        } else {
            result =
                result &&
                matched;
        }
    }

    return Boolean(result);
}

function evaluateClause(
    record,
    clause,
    fuzzy,
    fields,
    index,
    indexed
) {
    let matched = false;

    if (clause.type === "text") {
        if (
            indexed &&
            state.fullText[index] &&
            !clause.value.regex &&
            !clause.value.wildcard
        ) {
            matched =
                compareText(
                    state.fullText[index],
                    clause.value.raw,
                    fuzzy
                );
        } else {
            const selectedFields =
                clause.fields?.length
                    ? clause.fields
                    : fields;

            matched =
                selectedFields.some(field =>
                    fieldValues(
                        record,
                        field
                    ).some(value =>
                        compareScalar(
                            value,
                            clause,
                            fuzzy
                        )
                    )
                );
        }
    } else if (clause.field === "has") {
        const requested =
            normalizeField(
                clause.value.raw
            );

        matched =
            fieldValues(
                record,
                requested
            ).some(value =>
                value !== null &&
                value !== undefined &&
                value !== "" &&
                !(
                    Array.isArray(value) &&
                    value.length === 0
                )
            );
    } else {
        matched =
            fieldValues(
                record,
                clause.field
            ).some(value =>
                compareScalar(
                    value,
                    clause,
                    fuzzy
                )
            );
    }

    return clause.negated
        ? !matched
        : matched;
}

function compareScalar(
    candidate,
    clause,
    fuzzy = true
) {
    const value =
        clause.value;

    const operator =
        clause.operator;

    const candidateText =
        normalizeText(candidate);

    const queryText =
        normalizeText(value.raw);

    if (value.regex) {
        value.regex.lastIndex = 0;

        return value.regex.test(
            candidateText
        );
    }

    if (value.wildcard) {
        return wildcardRegex(
            queryText
        ).test(
            candidateText
        );
    }

    if (
        [">", ">=", "<", "<=", "=", "!="]
            .includes(operator) &&
        value.number !== null &&
        Number.isFinite(Number(candidate))
    ) {
        const left =
            Number(candidate);

        const right =
            value.number;

        if (operator === ">") {
            return left > right;
        }

        if (operator === ">=") {
            return left >= right;
        }

        if (operator === "<") {
            return left < right;
        }

        if (operator === "<=") {
            return left <= right;
        }

        if (operator === "=") {
            return left === right;
        }

        if (operator === "!=") {
            return left !== right;
        }
    }

    const left =
        candidateText.toLowerCase();

    const right =
        queryText.toLowerCase();

    if (operator === "=") {
        return left === right;
    }

    if (operator === "!=") {
        return left !== right;
    }

    if (operator === "contains") {
        return compareText(
            left,
            right,
            fuzzy
        );
    }

    return false;
}

function compareText(
    candidate,
    query,
    fuzzy
) {
    const left =
        normalizeText(candidate)
            .toLowerCase();

    const right =
        normalizeText(query)
            .toLowerCase();

    if (!right) {
        return true;
    }

    if (left.includes(right)) {
        return true;
    }

    if (!fuzzy || right.length < 4) {
        return false;
    }

    const words =
        left.split(
            /[^a-z0-9._:-]+/i
        ).filter(Boolean);

    const threshold =
        right.length <= 6
            ? 1
            : 2;

    return words.some(word =>
        Math.abs(
            word.length -
            right.length
        ) <= threshold &&
        levenshtein(
            word,
            right
        ) <= threshold
    );
}

function scoreRecord(
    record,
    plan,
    fields,
    index,
    indexed
) {
    if (!plan.clauses.length) {
        return 0;
    }

    let score = 0;

    for (const clause of plan.clauses) {
        if (clause.negated) {
            continue;
        }

        const targetFields =
            clause.type === "text"
                ? clause.fields || fields
                : [clause.field];

        for (const field of targetFields) {
            for (
                const value of fieldValues(
                    record,
                    field
                )
            ) {
                const candidate =
                    normalizeText(value)
                        .toLowerCase();

                const query =
                    normalizeText(
                        clause.value.raw
                    ).toLowerCase();

                if (
                    candidate === query
                ) {
                    score +=
                        fieldWeight(field) * 10;
                } else if (
                    candidate.startsWith(query)
                ) {
                    score +=
                        fieldWeight(field) * 6;
                } else if (
                    candidate.includes(query)
                ) {
                    score +=
                        fieldWeight(field) * 3;
                } else if (
                    compareText(
                        candidate,
                        query,
                        plan.fuzzy
                    )
                ) {
                    score +=
                        fieldWeight(field);
                }
            }
        }
    }

    return score;
}

function fieldWeight(field) {
    const normalized =
        normalizeField(field);

    if (
        normalized === "speciedex_id" ||
        normalized.endsWith("_id") ||
        normalized === "uuid" ||
        normalized === "sha256"
    ) {
        return 8;
    }

    if (
        normalized === "scientific_name"
    ) {
        return 7;
    }

    if (
        normalized === "common_name"
    ) {
        return 6;
    }

    if (
        [
            "genus",
            "species",
            "family",
            "order",
            "class",
            "phylum",
            "kingdom"
        ].includes(normalized)
    ) {
        return 4;
    }

    return 1;
}

function compareRecords(
    left,
    right,
    field,
    order
) {
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

    const direction =
        order === "desc"
            ? -1
            : 1;

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

    if (
        Number.isFinite(Number(a)) &&
        Number.isFinite(Number(b))
    ) {
        return (
            Number(a) -
            Number(b)
        ) * direction;
    }

    return String(a)
        .localeCompare(
            String(b),
            undefined,
            {
                numeric: true,
                sensitivity: "base"
            }
        ) * direction;
}

function buildFacets(
    records,
    requested
) {
    const fields =
        Array.isArray(requested)
            ? requested.map(normalizeField)
            : [];

    if (!fields.length) {
        return {};
    }

    const facets = {};

    for (const field of fields) {
        const counts = new Map();

        for (const record of records) {
            for (
                const value of fieldValues(
                    record,
                    field
                )
            ) {
                const key =
                    normalizeText(value);

                if (!key) {
                    continue;
                }

                counts.set(
                    key,
                    (counts.get(key) || 0) + 1
                );
            }
        }

        facets[field] =
            [...counts.entries()]
                .sort(
                    (left, right) =>
                        right[1] - left[1] ||
                        left[0].localeCompare(
                            right[0]
                        )
                )
                .map(
                    ([value, count]) => ({
                        value,
                        count
                    })
                );
    }

    return facets;
}

function fieldValues(
    record,
    field
) {
    const normalized =
        normalizeField(field);

    const direct =
        record?.[normalized];

    if (direct !== undefined) {
        return flatten(direct);
    }

    const camel =
        normalized.replace(
            /_([a-z])/g,
            (_, character) =>
                character.toUpperCase()
        );

    if (record?.[camel] !== undefined) {
        return flatten(
            record[camel]
        );
    }

    if (normalized === "name") {
        return flatten([
            record?.scientific_name,
            record?.scientificName,
            record?.common_name,
            record?.commonName,
            record?.canonical_name,
            record?.canonicalName,
            record?.accepted_name,
            record?.acceptedName
        ]);
    }

    if (normalized === "location") {
        return flatten([
            record?.continent,
            record?.country,
            record?.state,
            record?.province,
            record?.county,
            record?.city,
            record?.locality,
            record?.island,
            record?.ocean,
            record?.sea,
            record?.river,
            record?.lake
        ]);
    }

    if (
        normalized === "scientific_name"
    ) {
        return flatten([
            record?.scientific_name,
            record?.scientificName,
            record?.canonical_name,
            record?.canonicalName,
            record?.accepted_name,
            record?.acceptedName
        ]);
    }

    if (
        normalized === "common_name"
    ) {
        return flatten([
            record?.common_name,
            record?.commonName,
            record?.vernacular_name,
            record?.vernacularName,
            record?.preferred_common_name,
            record?.preferredCommonName
        ]);
    }

    if (
        normalized === "speciedex_id"
    ) {
        return flatten([
            record?.speciedex_id,
            record?.speciedexId,
            record?.speciedex_key,
            record?.speciedexKey,
            record?.canonical_id,
            record?.canonicalId,
            record?.id,
            record?.key
        ]);
    }

    return [];
}

function flatten(value) {
    if (Array.isArray(value)) {
        return value.flatMap(
            flatten
        );
    }

    if (
        value &&
        typeof value === "object"
    ) {
        return Object.values(value)
            .flatMap(
                flatten
            );
    }

    return [value];
}

function normalizeField(field) {
    const key =
        normalizeText(field)
            .toLowerCase()
            .replace(/-/g, "_");

    return FIELD_ALIASES[key] || key;
}

function normalizeText(value) {
    return String(
        value ?? ""
    ).trim();
}

function unquote(value) {
    const text =
        normalizeText(value);

    if (
        text.length >= 2 &&
        (
            (
                text.startsWith('"') &&
                text.endsWith('"')
            ) ||
            (
                text.startsWith("'") &&
                text.endsWith("'")
            )
        )
    ) {
        return text.slice(
            1,
            -1
        );
    }

    return text;
}

function tokenize(input) {
    const tokens = [];

    let current = "";
    let quote = null;
    let escaped = false;
    let regex = false;

    for (
        let index = 0;
        index < input.length;
        index += 1
    ) {
        const character =
            input[index];

        if (escaped) {
            current += character;
            escaped = false;
            continue;
        }

        if (character === "\\") {
            current += character;
            escaped = true;
            continue;
        }

        if (quote) {
            current += character;

            if (character === quote) {
                quote = null;
            }

            continue;
        }

        if (regex) {
            current += character;

            if (
                character === "/" &&
                input[index - 1] !== "\\"
            ) {
                regex = false;

                while (
                    /[gimsuy]/.test(
                        input[index + 1] || ""
                    )
                ) {
                    current += input[
                        ++index
                    ];
                }
            }

            continue;
        }

        if (
            character === '"' ||
            character === "'"
        ) {
            quote = character;
            current += character;
            continue;
        }

        if (
            character === "/" &&
            !current
        ) {
            regex = true;
            current += character;
            continue;
        }

        if (/\s/.test(character)) {
            if (current) {
                tokens.push(current);
                current = "";
            }

            continue;
        }

        current += character;
    }

    if (current) {
        tokens.push(current);
    }

    return tokens;
}

function wildcardRegex(value) {
    const escaped =
        value
            .replace(
                /[.+^${}()|[\]\\]/g,
                "\\$&"
            )
            .replace(/\*/g, ".*")
            .replace(/\?/g, ".");

    return new RegExp(
        `^${escaped}$`,
        "i"
    );
}

function levenshtein(left, right) {
    const a =
        left.toLowerCase();

    const b =
        right.toLowerCase();

    const previous =
        new Array(
            a.length + 1
        );

    const current =
        new Array(
            a.length + 1
        );

    for (
        let column = 0;
        column <= a.length;
        column += 1
    ) {
        previous[column] =
            column;
    }

    for (
        let row = 1;
        row <= b.length;
        row += 1
    ) {
        current[0] = row;

        for (
            let column = 1;
            column <= a.length;
            column += 1
        ) {
            const substitution =
                previous[column - 1] +
                (
                    b[row - 1] ===
                    a[column - 1]
                        ? 0
                        : 1
                );

            const insertion =
                current[column - 1] + 1;

            const deletion =
                previous[column] + 1;

            current[column] =
                Math.min(
                    substitution,
                    insertion,
                    deletion
                );
        }

        for (
            let column = 0;
            column <= a.length;
            column += 1
        ) {
            previous[column] =
                current[column];
        }
    }

    return previous[a.length];
}

function clampInteger(
    value,
    fallback,
    minimum,
    maximum
) {
    const parsed =
        Number.parseInt(
            value,
            10
        );

    if (!Number.isFinite(parsed)) {
        return fallback;
    }

    return Math.min(
        maximum,
        Math.max(
            minimum,
            parsed
        )
    );
}
