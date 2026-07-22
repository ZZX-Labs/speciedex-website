/*
========================================================================
Speciedex.org
Terminal Search Index
========================================================================

Reusable in-memory search index for SpeciedexTerminal.

Provides:

    • document storage
    • field discovery
    • normalized token indexing
    • exact-value indexing
    • prefix indexing
    • identifier indexing
    • weighted field scoring
    • document insertion, replacement, and removal
    • search result scoring and ranking
    • index statistics
    • index serialization
    • command-based inspection and rebuilding

This service is intentionally independent from the higher-level query parser in
terminal-search.js. The search module may use this index for accelerated lookup,
while retaining its own query language, API routing, and result formatting.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "Index";

    const VERSION =
        "2.0.0";

    const DEFAULT_LIMIT =
        50;

    const MAX_LIMIT =
        1000;

    const DEFAULT_IDENTIFIER_FIELDS =
        Object.freeze([
            "id",
            "key",
            "speciedex_id",
            "speciedexId",
            "provider_id",
            "providerId",
            "taxid",
            "gbif_id",
            "gbifId",
            "ncbi_id",
            "ncbiId",
            "itis_id",
            "itisId",
            "worms_id",
            "wormsId",
            "col_id",
            "colId",
            "iucn_id",
            "iucnId",
            "wikidata_id",
            "wikidataId",
            "uuid",
            "cid",
            "sha256",
            "sha512",
            "md5",
            "checksum",
            "hash"
        ]);

    const DEFAULT_FIELD_WEIGHTS =
        Object.freeze({
            speciedex_id:
                120,

            speciedexId:
                120,

            id:
                110,

            key:
                105,

            scientific_name:
                100,

            scientificName:
                100,

            canonical_name:
                95,

            canonicalName:
                95,

            accepted_name:
                95,

            acceptedName:
                95,

            common_name:
                85,

            commonName:
                85,

            vernacular_name:
                80,

            vernacularName:
                80,

            synonyms:
                75,

            genus:
                70,

            species:
                70,

            family:
                60,

            order:
                55,

            class:
                50,

            phylum:
                50,

            kingdom:
                50,

            domain:
                50,

            provider_id:
                65,

            providerId:
                65,

            provider:
                55,

            country:
                45,

            state:
                40,

            locality:
                40,

            location:
                40,

            habitat:
                35,

            biome:
                35,

            ecosystem:
                35,

            authority:
                30,

            tags:
                25,

            keywords:
                25,

            description:
                15
        });

    /*
    ==========================================================================
    Utilities
    ==========================================================================
    */

    function normalizeText(value) {
        return String(
            value ?? ""
        )
            .normalize("NFKC")
            .trim()
            .toLowerCase();
    }

    function normalizeToken(value) {
        return normalizeText(value)
            .replace(/[^\p{L}\p{N}_:.-]+/gu, " ")
            .replace(/\s+/g, " ")
            .trim();
    }

    function tokenizeValue(value) {
        const normalized =
            normalizeToken(
                value
            );

        if (!normalized) {
            return [];
        }

        const tokens =
            normalized
                .split(/\s+/)
                .filter(Boolean);

        const compact =
            normalized.replace(
                /\s+/g,
                ""
            );

        if (
            compact &&
            compact !== normalized
        ) {
            tokens.push(
                compact
            );
        }

        return [
            ...new Set(tokens)
        ];
    }

    function flatten(value) {
        if (
            value === null ||
            value === undefined
        ) {
            return [];
        }

        if (Array.isArray(value)) {
            return value.flatMap(
                flatten
            );
        }

        if (
            value &&
            typeof value ===
            "object"
        ) {
            return Object.values(value)
                .flatMap(
                    flatten
                );
        }

        return [
            value
        ];
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

    function uniqueStrings(values) {
        return [
            ...new Set(
                values
                    .map(
                        value =>
                            String(
                                value
                            ).trim()
                    )
                    .filter(Boolean)
            )
        ];
    }

    function resolveDocumentID(
        record,
        index
    ) {
        const candidates = [
            record?.speciedex_id,
            record?.speciedexId,
            record?.id,
            record?.key,
            record?.uuid,
            record?.provider_id,
            record?.providerId
        ];

        for (const candidate of candidates) {
            const normalized =
                normalizeText(
                    candidate
                );

            if (normalized) {
                return normalized;
            }
        }

        return `document:${index}`;
    }

    function cloneRecord(record) {
        if (
            !record ||
            typeof record !==
            "object"
        ) {
            return {};
        }

        return {
            ...record
        };
    }

    /*
    ==========================================================================
    Search Index
    ==========================================================================
    */

    class SearchIndex
        extends EventTarget {
        constructor(
            options = {}
        ) {
            super();

            this.options = {
                identifierFields:
                    uniqueStrings(
                        options.identifierFields ||
                        DEFAULT_IDENTIFIER_FIELDS
                    ),

                fieldWeights: {
                    ...DEFAULT_FIELD_WEIGHTS,
                    ...(options.fieldWeights || {})
                },

                includePrivateFields:
                    options.includePrivateFields ===
                    true
            };

            this.documents =
                [];

            this.documentMap =
                new Map();

            this.documentPositions =
                new Map();

            this.fields =
                [];

            this.inverted =
                new Map();

            this.exact =
                new Map();

            this.prefix =
                new Map();

            this.identifiers =
                new Map();

            this.documentTokens =
                new Map();

            this.built =
                false;

            this.builtAt =
                null;

            this.revision =
                0;
        }

        /*
        ======================================================================
        Index Construction
        ======================================================================
        */

        discoverFields(
            records
        ) {
            const fields =
                new Set();

            for (const record of records) {
                if (
                    !record ||
                    typeof record !==
                    "object"
                ) {
                    continue;
                }

                for (
                    const field of
                    Object.keys(record)
                ) {
                    if (
                        !this.options.includePrivateFields &&
                        field.startsWith("_")
                    ) {
                        continue;
                    }

                    fields.add(
                        field
                    );
                }
            }

            return [
                ...fields
            ].sort();
        }

        reset() {
            this.documents =
                [];

            this.documentMap.clear();
            this.documentPositions.clear();
            this.inverted.clear();
            this.exact.clear();
            this.prefix.clear();
            this.identifiers.clear();
            this.documentTokens.clear();

            this.fields =
                [];

            this.built =
                false;

            this.builtAt =
                null;

            this.revision +=
                1;

            this.dispatchEvent(
                new CustomEvent(
                    "reset",
                    {
                        detail: {
                            revision:
                                this.revision
                        }
                    }
                )
            );
        }

        build(
            records,
            fields = []
        ) {
            this.reset();

            const source =
                Array.isArray(records)
                    ? records
                    : [];

            this.fields =
                fields.length
                    ? uniqueStrings(fields)
                    : this.discoverFields(
                        source
                    );

            for (
                let index = 0;
                index < source.length;
                index += 1
            ) {
                this.add(
                    source[index],
                    {
                        rebuild:
                            false,

                        position:
                            index
                    }
                );
            }

            this.built =
                true;

            this.builtAt =
                new Date().toISOString();

            this.revision +=
                1;

            const report =
                this.stats();

            this.dispatchEvent(
                new CustomEvent(
                    "build",
                    {
                        detail:
                            report
                    }
                )
            );

            return report;
        }

        /*
        ======================================================================
        Document Mutation
        ======================================================================
        */

        add(
            record,
            options = {}
        ) {
            const cloned =
                cloneRecord(
                    record
                );

            const position =
                Number.isInteger(
                    options.position
                )
                    ? options.position
                    : this.documents.length;

            const documentID =
                resolveDocumentID(
                    cloned,
                    position
                );

            if (
                this.documentMap.has(
                    documentID
                )
            ) {
                return this.replace(
                    documentID,
                    cloned
                );
            }

            this.documents.push(
                cloned
            );

            this.documentMap.set(
                documentID,
                cloned
            );

            this.documentPositions.set(
                documentID,
                this.documents.length -
                1
            );

            this.indexDocument(
                documentID,
                cloned
            );

            if (
                options.rebuild !==
                false
            ) {
                this.built =
                    true;

                this.builtAt =
                    new Date().toISOString();

                this.revision +=
                    1;
            }

            this.dispatchEvent(
                new CustomEvent(
                    "add",
                    {
                        detail: {
                            id:
                                documentID,

                            record:
                                cloned
                        }
                    }
                )
            );

            return documentID;
        }

        replace(
            documentID,
            record
        ) {
            const normalizedID =
                normalizeText(
                    documentID
                );

            if (
                !this.documentMap.has(
                    normalizedID
                )
            ) {
                return this.add(
                    record
                );
            }

            const position =
                this.documentPositions.get(
                    normalizedID
                );

            this.removeDocumentTokens(
                normalizedID
            );

            const cloned =
                cloneRecord(
                    record
                );

            this.documents[
                position
            ] = cloned;

            this.documentMap.set(
                normalizedID,
                cloned
            );

            this.indexDocument(
                normalizedID,
                cloned
            );

            this.builtAt =
                new Date().toISOString();

            this.revision +=
                1;

            this.dispatchEvent(
                new CustomEvent(
                    "replace",
                    {
                        detail: {
                            id:
                                normalizedID,

                            record:
                                cloned
                        }
                    }
                )
            );

            return normalizedID;
        }

        remove(
            documentID
        ) {
            const normalizedID =
                normalizeText(
                    documentID
                );

            if (
                !this.documentMap.has(
                    normalizedID
                )
            ) {
                return false;
            }

            const position =
                this.documentPositions.get(
                    normalizedID
                );

            this.removeDocumentTokens(
                normalizedID
            );

            this.documentMap.delete(
                normalizedID
            );

            this.documentPositions.delete(
                normalizedID
            );

            this.documents.splice(
                position,
                1
            );

            for (
                let index = position;
                index < this.documents.length;
                index += 1
            ) {
                const id =
                    resolveDocumentID(
                        this.documents[index],
                        index
                    );

                this.documentPositions.set(
                    id,
                    index
                );
            }

            this.builtAt =
                new Date().toISOString();

            this.revision +=
                1;

            this.dispatchEvent(
                new CustomEvent(
                    "remove",
                    {
                        detail: {
                            id:
                                normalizedID
                        }
                    }
                )
            );

            return true;
        }

        /*
        ======================================================================
        Internal Indexing
        ======================================================================
        */

        ensureFieldMap(
            root,
            field
        ) {
            if (!root.has(field)) {
                root.set(
                    field,
                    new Map()
                );
            }

            return root.get(
                field
            );
        }

        ensurePosting(
            map,
            key
        ) {
            if (!map.has(key)) {
                map.set(
                    key,
                    new Set()
                );
            }

            return map.get(
                key
            );
        }

        indexDocument(
            documentID,
            record
        ) {
            const documentTokenRecords =
                [];

            for (const field of this.fields) {
                const values =
                    flatten(
                        record?.[
                            field
                        ]
                    );

                for (const value of values) {
                    const normalized =
                        normalizeText(
                            value
                        );

                    if (!normalized) {
                        continue;
                    }

                    const exactField =
                        this.ensureFieldMap(
                            this.exact,
                            field
                        );

                    this.ensurePosting(
                        exactField,
                        normalized
                    ).add(
                        documentID
                    );

                    documentTokenRecords.push({
                        type:
                            "exact",

                        field,

                        key:
                            normalized
                    });

                    const tokens =
                        tokenizeValue(
                            value
                        );

                    const invertedField =
                        this.ensureFieldMap(
                            this.inverted,
                            field
                        );

                    const prefixField =
                        this.ensureFieldMap(
                            this.prefix,
                            field
                        );

                    for (const token of tokens) {
                        this.ensurePosting(
                            invertedField,
                            token
                        ).add(
                            documentID
                        );

                        documentTokenRecords.push({
                            type:
                                "inverted",

                            field,

                            key:
                                token
                        });

                        const maximumPrefix =
                            Math.min(
                                token.length,
                                24
                            );

                        for (
                            let length = 1;
                            length <= maximumPrefix;
                            length += 1
                        ) {
                            const prefix =
                                token.slice(
                                    0,
                                    length
                                );

                            this.ensurePosting(
                                prefixField,
                                prefix
                            ).add(
                                documentID
                            );

                            documentTokenRecords.push({
                                type:
                                    "prefix",

                                field,

                                key:
                                    prefix
                            });
                        }
                    }

                    if (
                        this.options.identifierFields.includes(
                            field
                        )
                    ) {
                        this.ensurePosting(
                            this.identifiers,
                            normalized
                        ).add(
                            documentID
                        );

                        documentTokenRecords.push({
                            type:
                                "identifier",

                            field,

                            key:
                                normalized
                        });
                    }
                }
            }

            this.documentTokens.set(
                documentID,
                documentTokenRecords
            );
        }

        removeDocumentTokens(
            documentID
        ) {
            const records =
                this.documentTokens.get(
                    documentID
                ) ||
                [];

            for (const record of records) {
                let map;

                if (
                    record.type ===
                    "identifier"
                ) {
                    map =
                        this.identifiers;
                } else {
                    const root =
                        this[
                            record.type
                        ];

                    map =
                        root?.get(
                            record.field
                        );
                }

                const posting =
                    map?.get(
                        record.key
                    );

                if (!posting) {
                    continue;
                }

                posting.delete(
                    documentID
                );

                if (!posting.size) {
                    map.delete(
                        record.key
                    );
                }
            }

            this.documentTokens.delete(
                documentID
            );
        }

        /*
        ======================================================================
        Lookup
        ======================================================================
        */

        get(
            documentID
        ) {
            return (
                this.documentMap.get(
                    normalizeText(
                        documentID
                    )
                ) ||
                null
            );
        }

        has(
            documentID
        ) {
            return this.documentMap.has(
                normalizeText(
                    documentID
                )
            );
        }

        lookupIdentifier(
            identifier
        ) {
            const normalized =
                normalizeText(
                    identifier
                );

            const ids =
                this.identifiers.get(
                    normalized
                ) ||
                new Set();

            return [
                ...ids
            ]
                .map(
                    id =>
                        this.documentMap.get(
                            id
                        )
                )
                .filter(Boolean);
        }

        lookupExact(
            field,
            value
        ) {
            const normalizedField =
                String(
                    field ?? ""
                ).trim();

            const normalizedValue =
                normalizeText(
                    value
                );

            const ids =
                this.exact
                    .get(
                        normalizedField
                    )
                    ?.get(
                        normalizedValue
                    ) ||
                new Set();

            return [
                ...ids
            ]
                .map(
                    id =>
                        this.documentMap.get(
                            id
                        )
                )
                .filter(Boolean);
        }

        lookupPrefix(
            field,
            value
        ) {
            const normalizedField =
                String(
                    field ?? ""
                ).trim();

            const normalizedValue =
                normalizeText(
                    value
                );

            const ids =
                this.prefix
                    .get(
                        normalizedField
                    )
                    ?.get(
                        normalizedValue
                    ) ||
                new Set();

            return [
                ...ids
            ]
                .map(
                    id =>
                        this.documentMap.get(
                            id
                        )
                )
                .filter(Boolean);
        }

        /*
        ======================================================================
        Search
        ======================================================================
        */

        scoreDocument(
            documentID,
            terms,
            fields
        ) {
            let score =
                0;

            const matchedFields =
                new Set();

            for (const field of fields) {
                const weight =
                    this.options.fieldWeights[
                        field
                    ] ||
                    10;

                const exactField =
                    this.exact.get(
                        field
                    );

                const invertedField =
                    this.inverted.get(
                        field
                    );

                const prefixField =
                    this.prefix.get(
                        field
                    );

                for (const term of terms) {
                    if (
                        exactField
                            ?.get(
                                term
                            )
                            ?.has(
                                documentID
                            )
                    ) {
                        score +=
                            weight +
                            40;

                        matchedFields.add(
                            field
                        );

                        continue;
                    }

                    if (
                        invertedField
                            ?.get(
                                term
                            )
                            ?.has(
                                documentID
                            )
                    ) {
                        score +=
                            weight;

                        matchedFields.add(
                            field
                        );

                        continue;
                    }

                    if (
                        prefixField
                            ?.get(
                                term
                            )
                            ?.has(
                                documentID
                            )
                    ) {
                        score +=
                            Math.max(
                                1,
                                weight *
                                0.65
                            );

                        matchedFields.add(
                            field
                        );
                    }
                }
            }

            return {
                score,
                matchedFields:
                    [
                        ...matchedFields
                    ]
            };
        }

        search(
            query,
            options = {}
        ) {
            const started =
                performance.now();

            const limit =
                clampInteger(
                    options.limit,
                    DEFAULT_LIMIT,
                    1,
                    MAX_LIMIT
                );

            const offset =
                clampInteger(
                    options.offset,
                    0,
                    0,
                    Number.MAX_SAFE_INTEGER
                );

            const fields =
                options.fields?.length
                    ? uniqueStrings(
                        options.fields
                    )
                    : this.fields;

            const terms =
                tokenizeValue(
                    query
                );

            if (!terms.length) {
                return {
                    query:
                        normalizeText(
                            query
                        ),

                    total:
                        this.documents.length,

                    records:
                        this.documents.slice(
                            offset,
                            offset +
                            limit
                        ),

                    elapsed_ms:
                        performance.now() -
                        started
                };
            }

            const candidateIDs =
                new Set();

            for (const field of fields) {
                const invertedField =
                    this.inverted.get(
                        field
                    );

                const prefixField =
                    this.prefix.get(
                        field
                    );

                const exactField =
                    this.exact.get(
                        field
                    );

                for (const term of terms) {
                    for (
                        const id of
                        exactField?.get(
                            term
                        ) ||
                        []
                    ) {
                        candidateIDs.add(
                            id
                        );
                    }

                    for (
                        const id of
                        invertedField?.get(
                            term
                        ) ||
                        []
                    ) {
                        candidateIDs.add(
                            id
                        );
                    }

                    if (
                        options.prefix !==
                        false
                    ) {
                        for (
                            const id of
                            prefixField?.get(
                                term
                            ) ||
                            []
                        ) {
                            candidateIDs.add(
                                id
                            );
                        }
                    }
                }
            }

            const ranked =
                [];

            for (
                const documentID of
                candidateIDs
            ) {
                const score =
                    this.scoreDocument(
                        documentID,
                        terms,
                        fields
                    );

                if (
                    score.score <=
                    0
                ) {
                    continue;
                }

                const record =
                    this.documentMap.get(
                        documentID
                    );

                if (!record) {
                    continue;
                }

                ranked.push({
                    id:
                        documentID,

                    record,

                    score:
                        score.score,

                    matchedFields:
                        score.matchedFields
                });
            }

            ranked.sort(
                (
                    left,
                    right
                ) =>
                    right.score -
                    left.score
            );

            const total =
                ranked.length;

            const records =
                ranked
                    .slice(
                        offset,
                        offset +
                        limit
                    )
                    .map(
                        item => ({
                            ...item.record,

                            _index_id:
                                item.id,

                            _index_score:
                                item.score,

                            _index_fields:
                                item.matchedFields
                        })
                    );

            return {
                query:
                    normalizeText(
                        query
                    ),

                total,

                offset,

                limit,

                records,

                elapsed_ms:
                    performance.now() -
                    started
            };
        }

        /*
        ======================================================================
        Statistics and Serialization
        ======================================================================
        */

        stats() {
            let tokenCount =
                0;

            let exactCount =
                0;

            let prefixCount =
                0;

            for (
                const fieldMap of
                this.inverted.values()
            ) {
                tokenCount +=
                    fieldMap.size;
            }

            for (
                const fieldMap of
                this.exact.values()
            ) {
                exactCount +=
                    fieldMap.size;
            }

            for (
                const fieldMap of
                this.prefix.values()
            ) {
                prefixCount +=
                    fieldMap.size;
            }

            return {
                version:
                    VERSION,

                built:
                    this.built,

                builtAt:
                    this.builtAt,

                revision:
                    this.revision,

                documents:
                    this.documents.length,

                fields:
                    this.fields.length,

                tokens:
                    tokenCount,

                exactValues:
                    exactCount,

                prefixes:
                    prefixCount,

                identifiers:
                    this.identifiers.size
            };
        }

        export() {
            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                fields:
                    [
                        ...this.fields
                    ],

                options: {
                    identifierFields:
                        [
                            ...this.options.identifierFields
                        ],

                    fieldWeights: {
                        ...this.options.fieldWeights
                    },

                    includePrivateFields:
                        this.options.includePrivateFields
                },

                documents:
                    this.documents.map(
                        cloneRecord
                    ),

                stats:
                    this.stats()
            };
        }

        import(
            payload
        ) {
            if (
                !payload ||
                typeof payload !==
                "object" ||
                !Array.isArray(
                    payload.documents
                )
            ) {
                throw new TypeError(
                    "Search index import requires an object with a documents array."
                );
            }

            if (
                payload.options &&
                typeof payload.options ===
                "object"
            ) {
                this.options = {
                    ...this.options,

                    ...payload.options,

                    fieldWeights: {
                        ...this.options.fieldWeights,
                        ...(payload.options.fieldWeights || {})
                    },

                    identifierFields:
                        uniqueStrings(
                            payload.options.identifierFields ||
                            this.options.identifierFields
                        )
                };
            }

            return this.build(
                payload.documents,
                payload.fields || []
            );
        }

        destroy() {
            this.reset();
        }
    }

    /*
    ==========================================================================
    Service Initialization
    ==========================================================================
    */

    function initialize(
        context
    ) {
        if (
            context.index instanceof
            SearchIndex
        ) {
            return context.index;
        }

        const index =
            new SearchIndex({
                includePrivateFields:
                    context.root?.
                        dataset.
                        terminalIndexPrivate ===
                    "true"
            });

        context.index =
            index;

        context.registerService?.(
            "index",
            index
        );

        const records =
            context.library?.get?.(
                "records"
            );

        if (
            Array.isArray(records) &&
            records.length
        ) {
            index.build(
                records
            );
        }

        context.events?.on?.(
            "library:updated",
            detail => {
                if (
                    detail?.collection ===
                    "records" &&
                    Array.isArray(
                        detail.records
                    )
                ) {
                    index.build(
                        detail.records
                    );
                }
            }
        );

        return index;
    }

    /*
    ==========================================================================
    Commands
    ==========================================================================
    */

    const commands =
        [
            {
                name:
                    "index",

                category:
                    "search",

                description:
                    "Display search-index status and statistics.",

                usage:
                    "index",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.index.stats()
                    )
            },

            {
                name:
                    "index-build",

                category:
                    "search",

                description:
                    "Build or rebuild the search index from a library collection.",

                usage:
                    "index-build [collection]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const collection =
                        args[0] ||
                        "records";

                    const records =
                        context.library?.get?.(
                            collection
                        ) ||
                        [];

                    if (
                        !Array.isArray(records)
                    ) {
                        throw new Error(
                            `Library collection "${collection}" is not an array.`
                        );
                    }

                    return writeJSON(
                        context.index.build(
                            records
                        )
                    );
                }
            },

            {
                name:
                    "index-search",

                category:
                    "search",

                description:
                    "Search the local in-memory index directly.",

                usage:
                    "index-search <query> [--limit N] [--offset N]",

                handler: ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const query =
                        args.join(
                            " "
                        );

                    if (!query) {
                        throw new Error(
                            "An index search query is required."
                        );
                    }

                    return writeJSON(
                        context.index.search(
                            query,
                            {
                                limit:
                                    parsed.options.limit,

                                offset:
                                    parsed.options.offset,

                                fields:
                                    parsed.options.fields
                                        ? String(
                                            parsed.options.fields
                                        )
                                            .split(",")
                                            .map(
                                                field =>
                                                    field.trim()
                                            )
                                            .filter(Boolean)
                                        : []
                            }
                        )
                    );
                }
            },

            {
                name:
                    "index-get",

                category:
                    "search",

                description:
                    "Retrieve one indexed document by identifier.",

                usage:
                    "index-get <identifier>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const identifier =
                        args.join(
                            " "
                        );

                    if (!identifier) {
                        throw new Error(
                            "A document identifier is required."
                        );
                    }

                    const direct =
                        context.index.get(
                            identifier
                        );

                    const matches =
                        direct
                            ? [
                                direct
                            ]
                            : context.index.lookupIdentifier(
                                identifier
                            );

                    return writeJSON(
                        matches
                    );
                }
            },

            {
                name:
                    "index-fields",

                category:
                    "search",

                description:
                    "List fields currently indexed.",

                usage:
                    "index-fields",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON({
                        fields:
                            [
                                ...context.index.fields
                            ],

                        weights: {
                            ...context.index.options.fieldWeights
                        },

                        identifierFields:
                            [
                                ...context.index.options.identifierFields
                            ]
                    })
            },

            {
                name:
                    "index-reset",

                category:
                    "search",

                description:
                    "Clear the in-memory search index.",

                usage:
                    "index-reset",

                handler: ({
                    context,
                    write
                }) => {
                    context.index.reset();

                    return write(
                        "Search index cleared.",
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

            SearchIndex,

            normalizeText,
            normalizeToken,
            tokenizeValue,
            resolveDocumentID,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalIndex =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

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
