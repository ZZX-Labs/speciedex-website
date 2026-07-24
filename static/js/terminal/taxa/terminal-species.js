/*
========================================================================
Speciedex.org
Terminal Species Module
========================================================================

Canonical species search and taxonomy service for SpeciedexTerminal.

Provides:

    • Validated canonical-species API requests
    • Scientific name, common name, lineage, status, geography, provider,
      conservation, habitat, environment, source, and date filters
    • Normalized canonical species records
    • Synonym, accepted-name, lineage, distribution, and conservation helpers
    • Accepted, synonym, extinct, threatened, endemic, invasive, and verified views
    • Provider, rank, status, lineage, geography, source, and conservation summaries
    • Lifecycle events, caching, and resilient service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Species";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "species";

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

    function normalizeKey(value) {
        return normalizeText(value)
            .toLowerCase()
            .replace(/[\s-]+/g, "_")
            .replace(/[^a-z0-9_]/g, "");
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
        const normalized = normalizeKey(
            value || "scientific_name"
        );

        const allowed = new Set([
            "scientific_name",
            "canonical_name",
            "common_name",
            "rank",
            "status",
            "kingdom",
            "phylum",
            "class",
            "order",
            "family",
            "genus",
            "species",
            "conservation_status",
            "provider",
            "occurrence_count",
            "updated_at",
            "created_at",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported species sort field: ${value}`
            );
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized = normalizeText(
            value || "asc"
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
                "species",
                "species_id",
                "taxon",
                "taxon_id",
                "scientific_name",
                "canonical_name",
                "common_name",
                "authorship",
                "rank",
                "status",
                "accepted_name",
                "accepted_id",
                "kingdom",
                "phylum",
                "class",
                "order",
                "family",
                "genus",
                "subgenus",
                "specific_epithet",
                "subspecific_epithet",
                "country",
                "region",
                "continent",
                "habitat",
                "environment",
                "provider",
                "source",
                "license",
                "conservation_status",
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
                "accepted",
                "synonym",
                "extinct",
                "threatened",
                "endemic",
                "native",
                "introduced",
                "invasive",
                "marine",
                "freshwater",
                "terrestrial",
                "verified",
                "active"
            ]
        ) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                const value =
                    normalizeBoolean(
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

        const minimumOccurrences =
            source.min_occurrences ??
            source.minOccurrences;

        const maximumOccurrences =
            source.max_occurrences ??
            source.maxOccurrences;

        if (
            minimumOccurrences !== undefined &&
            minimumOccurrences !== null &&
            minimumOccurrences !== ""
        ) {
            normalized.min_occurrences =
                clampInteger(
                    minimumOccurrences,
                    0,
                    0,
                    Number.MAX_SAFE_INTEGER
                );
        }

        if (
            maximumOccurrences !== undefined &&
            maximumOccurrences !== null &&
            maximumOccurrences !== ""
        ) {
            normalized.max_occurrences =
                clampInteger(
                    maximumOccurrences,
                    Number.MAX_SAFE_INTEGER,
                    0,
                    Number.MAX_SAFE_INTEGER
                );
        }

        if (
            normalized.min_occurrences !== undefined &&
            normalized.max_occurrences !== undefined &&
            normalized.min_occurrences >
            normalized.max_occurrences
        ) {
            throw new RangeError(
                "Minimum occurrence count must not exceed maximum occurrence count."
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
                "Species start date must not be later than the end date."
            );
        }

        return normalized;
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

        if (!text) {
            return [];
        }

        return [
            ...new Set(
                text
                    .split(/[;,|]+/)
                    .map(normalizeText)
                    .filter(Boolean)
            )
        ];
    }

    function normalizeTaxonomicStatus(value) {
        const normalized =
            normalizeKey(
                value || "unknown"
            );

        const aliases = {
            valid: "accepted",
            current: "accepted",
            synonymized: "synonym",
            unaccepted: "synonym",
            doubtful: "doubtful",
            uncertain: "unresolved",
            ambiguous: "unresolved",
            deleted: "inactive"
        };

        return aliases[normalized] || normalized;
    }

    function normalizeConservationStatus(value) {
        const normalized =
            normalizeText(value)
                .toUpperCase();

        const aliases = {
            "LEAST CONCERN": "LC",
            "NEAR THREATENED": "NT",
            "VULNERABLE": "VU",
            "ENDANGERED": "EN",
            "CRITICALLY ENDANGERED": "CR",
            "EXTINCT IN THE WILD": "EW",
            "EXTINCT": "EX",
            "DATA DEFICIENT": "DD",
            "NOT EVALUATED": "NE"
        };

        return aliases[normalized] || normalized;
    }

    function normalizeSynonyms(value) {
        if (!value) {
            return [];
        }

        if (Array.isArray(value)) {
            return value
                .map((item, index) => {
                    if (
                        item &&
                        typeof item === "object"
                    ) {
                        return {
                            id: normalizeText(
                                item.id ??
                                item.taxon_id ??
                                item.taxonId ??
                                ""
                            ),
                            scientific_name: normalizeText(
                                item.scientific_name ??
                                item.scientificName ??
                                item.name ??
                                ""
                            ),
                            authorship: normalizeText(
                                item.authorship ??
                                item.scientific_name_authorship ??
                                item.scientificNameAuthorship ??
                                ""
                            ),
                            status: normalizeTaxonomicStatus(
                                item.status ??
                                "synonym"
                            ),
                            source: normalizeText(
                                item.source ??
                                ""
                            ),
                            index
                        };
                    }

                    return {
                        id: "",
                        scientific_name:
                            normalizeText(item),
                        authorship: "",
                        status: "synonym",
                        source: "",
                        index
                    };
                })
                .filter(
                    item =>
                        item.scientific_name
                );
        }

        return normalizeStringArray(value)
            .map(
                (name, index) => ({
                    id: "",
                    scientific_name:
                        name,
                    authorship: "",
                    status: "synonym",
                    source: "",
                    index
                })
            );
    }

    function normalizeLineage(record) {
        const explicit =
            Array.isArray(record.lineage)
                ? record.lineage
                : (
                    Array.isArray(record.classification)
                        ? record.classification
                        : null
                );

        if (explicit) {
            return explicit
                .map((item, index) => {
                    if (
                        item &&
                        typeof item === "object"
                    ) {
                        return {
                            id: normalizeText(
                                item.id ??
                                item.taxon_id ??
                                item.taxonId ??
                                ""
                            ),
                            rank: normalizeKey(
                                item.rank ??
                                item.taxon_rank ??
                                item.taxonRank ??
                                ""
                            ),
                            scientific_name: normalizeText(
                                item.scientific_name ??
                                item.scientificName ??
                                item.name ??
                                ""
                            ),
                            index
                        };
                    }

                    return {
                        id: "",
                        rank: "",
                        scientific_name:
                            normalizeText(item),
                        index
                    };
                })
                .filter(
                    item =>
                        item.scientific_name
                );
        }

        const lineage = [];

        for (
            const [rank, value] of
            [
                ["domain", record.domain],
                ["kingdom", record.kingdom],
                ["phylum", record.phylum],
                ["class", record.class ?? record.class_name ?? record.className],
                ["order", record.order ?? record.order_name ?? record.orderName],
                ["family", record.family],
                ["genus", record.genus],
                ["species", record.scientific_name ?? record.scientificName ?? record.name]
            ]
        ) {
            const scientificName =
                normalizeText(value);

            if (scientificName) {
                lineage.push({
                    id: "",
                    rank,
                    scientific_name:
                        scientificName,
                    index:
                        lineage.length
                });
            }
        }

        return lineage;
    }

    function normalizeRecord(record, index = 0) {
        if (
            !record ||
            typeof record !== "object"
        ) {
            const name =
                normalizeText(record);

            return {
                index,
                id:
                    name ||
                    `species-${index + 1}`,
                scientific_name:
                    name,
                canonical_name:
                    name,
                common_name: "",
                common_names: [],
                authorship: "",
                rank: "species",
                status: "unknown",
                accepted: false,
                synonym: false,
                extinct: false,
                threatened: false,
                endemic: false,
                native: false,
                introduced: false,
                invasive: false,
                marine: false,
                freshwater: false,
                terrestrial: false,
                verified: false,
                active: true,
                conservation_status: "",
                synonyms: [],
                lineage: [],
                countries: [],
                regions: [],
                continents: [],
                habitats: [],
                environments: [],
                providers: [],
                sources: []
            };
        }

        const scientificName =
            normalizeText(
                record.scientific_name ??
                record.scientificName ??
                record.name ??
                record.canonical_name ??
                record.canonicalName ??
                ""
            );

        const canonicalName =
            normalizeText(
                record.canonical_name ??
                record.canonicalName ??
                record.canonical ??
                scientificName
            );

        const status =
            normalizeTaxonomicStatus(
                record.status ??
                record.taxonomic_status ??
                record.taxonomicStatus ??
                record.acceptance_status ??
                record.acceptanceStatus
            );

        const conservationStatus =
            normalizeConservationStatus(
                record.conservation_status ??
                record.conservationStatus ??
                record.iucn_status ??
                record.iucnStatus ??
                ""
            );

        const accepted =
            record.accepted === true ||
            [
                "accepted",
                "valid"
            ].includes(status);

        const synonym =
            record.synonym === true ||
            record.is_synonym === true ||
            record.isSynonym === true ||
            status === "synonym";

        const extinct =
            record.extinct === true ||
            [
                "EX",
                "EW"
            ].includes(
                conservationStatus
            ) ||
            status === "extinct";

        const threatened =
            record.threatened === true ||
            [
                "VU",
                "EN",
                "CR",
                "EW"
            ].includes(
                conservationStatus
            );

        const habitats =
            normalizeStringArray(
                record.habitats ??
                record.habitat
            );

        const environments =
            normalizeStringArray(
                record.environments ??
                record.environment
            );

        const habitatText =
            [
                ...habitats,
                ...environments
            ]
                .join(" ")
                .toLowerCase();

        const active =
            record.active !== false &&
            record.deleted !== true &&
            ![
                "inactive",
                "deleted"
            ].includes(status);

        return {
            ...record,
            index:
                record.index ??
                index,
            id: normalizeText(
                record.id ??
                record.species_id ??
                record.speciesId ??
                record.taxon_id ??
                record.taxonId ??
                record.uuid ??
                `species-${index + 1}`
            ),
            species_id: normalizeText(
                record.species_id ??
                record.speciesId ??
                record.taxon_id ??
                record.taxonId ??
                record.id ??
                ""
            ),
            scientific_name:
                scientificName,
            canonical_name:
                canonicalName,
            common_name: normalizeText(
                record.common_name ??
                record.commonName ??
                ""
            ),
            common_names: normalizeStringArray(
                record.common_names ??
                record.commonNames ??
                record.vernacular_names ??
                record.vernacularNames ??
                record.common_name ??
                record.commonName
            ),
            authorship: normalizeText(
                record.authorship ??
                record.scientific_name_authorship ??
                record.scientificNameAuthorship ??
                ""
            ),
            rank: normalizeKey(
                record.rank ??
                record.taxon_rank ??
                record.taxonRank ??
                "species"
            ),
            status,
            accepted,
            synonym,
            accepted_name: normalizeText(
                record.accepted_name ??
                record.acceptedName ??
                ""
            ),
            accepted_id: normalizeText(
                record.accepted_id ??
                record.acceptedId ??
                record.accepted_taxon_id ??
                record.acceptedTaxonId ??
                ""
            ),
            domain: normalizeText(
                record.domain ??
                ""
            ),
            kingdom: normalizeText(
                record.kingdom ??
                ""
            ),
            phylum: normalizeText(
                record.phylum ??
                ""
            ),
            class: normalizeText(
                record.class ??
                record.class_name ??
                record.className ??
                ""
            ),
            order: normalizeText(
                record.order ??
                record.order_name ??
                record.orderName ??
                ""
            ),
            family: normalizeText(
                record.family ??
                ""
            ),
            genus: normalizeText(
                record.genus ??
                ""
            ),
            subgenus: normalizeText(
                record.subgenus ??
                ""
            ),
            specific_epithet: normalizeText(
                record.specific_epithet ??
                record.specificEpithet ??
                record.species ??
                ""
            ),
            subspecific_epithet: normalizeText(
                record.subspecific_epithet ??
                record.subspecificEpithet ??
                record.subspecies ??
                ""
            ),
            conservation_status:
                conservationStatus,
            extinct,
            threatened,
            endemic:
                record.endemic === true ||
                record.is_endemic === true ||
                record.isEndemic === true,
            native:
                record.native === true ||
                record.is_native === true ||
                record.isNative === true,
            introduced:
                record.introduced === true ||
                record.is_introduced === true ||
                record.isIntroduced === true,
            invasive:
                record.invasive === true ||
                record.is_invasive === true ||
                record.isInvasive === true,
            marine:
                record.marine === true ||
                habitatText.includes("marine") ||
                habitatText.includes("ocean"),
            freshwater:
                record.freshwater === true ||
                habitatText.includes("freshwater") ||
                habitatText.includes("river") ||
                habitatText.includes("lake"),
            terrestrial:
                record.terrestrial === true ||
                habitatText.includes("terrestrial") ||
                habitatText.includes("land"),
            verified:
                record.verified === true ||
                [
                    "verified",
                    "confirmed"
                ].includes(
                    normalizeKey(
                        record.verification_status ??
                        record.verificationStatus
                    )
                ),
            active,
            synonyms:
                normalizeSynonyms(
                    record.synonyms ??
                    record.synonym_names ??
                    record.synonymNames
                ),
            lineage:
                normalizeLineage(record),
            countries:
                normalizeStringArray(
                    record.countries ??
                    record.country_codes ??
                    record.countryCodes ??
                    record.country
                ),
            regions:
                normalizeStringArray(
                    record.regions ??
                    record.region
                ),
            continents:
                normalizeStringArray(
                    record.continents ??
                    record.continent
                ),
            habitats,
            environments,
            provider: normalizeText(
                record.provider ??
                record.provider_name ??
                record.providerName ??
                ""
            ),
            providers:
                normalizeStringArray(
                    record.providers ??
                    record.provider
                ),
            source: normalizeText(
                record.source ??
                record.source_name ??
                record.sourceName ??
                ""
            ),
            sources:
                normalizeStringArray(
                    record.sources ??
                    record.source
                ),
            license: normalizeText(
                record.license ??
                record.licence ??
                ""
            ),
            occurrence_count:
                Number.isFinite(
                    Number(
                        record.occurrence_count ??
                        record.occurrenceCount ??
                        record.occurrences
                    )
                )
                    ? Number(
                        record.occurrence_count ??
                        record.occurrenceCount ??
                        record.occurrences
                    )
                    : 0,
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

        const ranks = new Map();
        const statuses = new Map();
        const kingdoms = new Map();
        const phyla = new Map();
        const classes = new Map();
        const orders = new Map();
        const families = new Map();
        const genera = new Map();
        const providers = new Map();
        const countries = new Map();
        const regions = new Map();
        const sources = new Map();
        const conservationStatuses = new Map();
        const habitats = new Map();
        const environments = new Map();

        let occurrenceCount = 0;
        let synonymCount = 0;

        for (const item of values) {
            incrementMap(
                ranks,
                item.rank
            );

            incrementMap(
                statuses,
                item.status
            );

            incrementMap(
                kingdoms,
                item.kingdom
            );

            incrementMap(
                phyla,
                item.phylum
            );

            incrementMap(
                classes,
                item.class
            );

            incrementMap(
                orders,
                item.order
            );

            incrementMap(
                families,
                item.family
            );

            incrementMap(
                genera,
                item.genus
            );

            incrementMap(
                providers,
                item.provider
            );

            incrementMap(
                conservationStatuses,
                item.conservation_status
            );

            for (const country of item.countries) {
                incrementMap(
                    countries,
                    country
                );
            }

            for (const region of item.regions) {
                incrementMap(
                    regions,
                    region
                );
            }

            for (const source of item.sources) {
                incrementMap(
                    sources,
                    source
                );
            }

            for (const habitat of item.habitats) {
                incrementMap(
                    habitats,
                    habitat
                );
            }

            for (const environment of item.environments) {
                incrementMap(
                    environments,
                    environment
                );
            }

            occurrenceCount +=
                item.occurrence_count || 0;

            synonymCount +=
                item.synonyms.length;
        }

        return {
            total:
                values.length,
            accepted:
                values.filter(
                    item =>
                        item.accepted
                ).length,
            synonyms:
                values.filter(
                    item =>
                        item.synonym
                ).length,
            synonymNames:
                synonymCount,
            extinct:
                values.filter(
                    item =>
                        item.extinct
                ).length,
            threatened:
                values.filter(
                    item =>
                        item.threatened
                ).length,
            endemic:
                values.filter(
                    item =>
                        item.endemic
                ).length,
            native:
                values.filter(
                    item =>
                        item.native
                ).length,
            introduced:
                values.filter(
                    item =>
                        item.introduced
                ).length,
            invasive:
                values.filter(
                    item =>
                        item.invasive
                ).length,
            marine:
                values.filter(
                    item =>
                        item.marine
                ).length,
            freshwater:
                values.filter(
                    item =>
                        item.freshwater
                ).length,
            terrestrial:
                values.filter(
                    item =>
                        item.terrestrial
                ).length,
            verified:
                values.filter(
                    item =>
                        item.verified
                ).length,
            active:
                values.filter(
                    item =>
                        item.active
                ).length,
            occurrences:
                occurrenceCount,
            ranks:
                mapToSortedObject(
                    ranks
                ),
            statuses:
                mapToSortedObject(
                    statuses
                ),
            kingdoms:
                mapToSortedObject(
                    kingdoms
                ),
            phyla:
                mapToSortedObject(
                    phyla
                ),
            classes:
                mapToSortedObject(
                    classes
                ),
            orders:
                mapToSortedObject(
                    orders
                ),
            families:
                mapToSortedObject(
                    families
                ),
            genera:
                mapToSortedObject(
                    genera
                ),
            providers:
                mapToSortedObject(
                    providers
                ),
            countries:
                mapToSortedObject(
                    countries
                ),
            regions:
                mapToSortedObject(
                    regions
                ),
            sources:
                mapToSortedObject(
                    sources
                ),
            conservationStatuses:
                mapToSortedObject(
                    conservationStatuses
                ),
            habitats:
                mapToSortedObject(
                    habitats
                ),
            environments:
                mapToSortedObject(
                    environments
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
                                Array.isArray(payload.species)
                                    ? payload.species
                                    : (
                                        Array.isArray(payload.taxa)
                                            ? payload.taxa
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

    function findSpecies(records, value) {
        const target =
            normalizeText(value);

        const lower =
            target.toLowerCase();

        return records.find(
            item =>
                item.id === target ||
                item.species_id === target ||
                item.scientific_name.toLowerCase() === lower ||
                item.canonical_name.toLowerCase() === lower ||
                item.common_names.some(
                    name =>
                        name.toLowerCase() === lower
                )
        ) || null;
    }

    class SpeciesService extends EventTarget {
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
                    "Species service has been destroyed."
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
                    `species:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not break species operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-species-${name}`,
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
                        "taxa/species",
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
                    "A species ID or name is required."
                );
            }

            try {
                const payload =
                    await this.context.api.get(
                        `taxa/species/${encodeURIComponent(normalizedId)}`,
                        {},
                        options
                    );

                return normalizeRecord(
                    payload,
                    0
                );
            } catch (error) {
                const match =
                    findSpecies(
                        this.cache?.records || [],
                        normalizedId
                    );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async accepted(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        accepted: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.accepted
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async synonyms(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        synonym: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.synonym ||
                        item.synonyms.length
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async threatened(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        threatened: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.threatened
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async extinct(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        extinct: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.extinct
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async endemic(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        endemic: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.endemic
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async invasive(parameters = {}, options = {}) {
            const result =
                await this.list(
                    {
                        ...parameters,
                        invasive: true
                    },
                    options
                );

            const records =
                result.records.filter(
                    item =>
                        item.invasive
                );

            return {
                ...result,
                records,
                summary:
                    summarize(records)
            };
        }

        async lineage(id, options = {}) {
            const record =
                await this.get(
                    id,
                    options
                );

            return {
                id:
                    record.id,
                scientific_name:
                    record.scientific_name,
                lineage:
                    record.lineage
            };
        }

        async synonymList(id, options = {}) {
            const record =
                await this.get(
                    id,
                    options
                );

            return {
                id:
                    record.id,
                scientific_name:
                    record.scientific_name,
                accepted:
                    record.accepted,
                accepted_name:
                    record.accepted_name,
                accepted_id:
                    record.accepted_id,
                synonyms:
                    record.synonyms
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
                species:
                    result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint:
                    "taxa/species",
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
            SpeciesService &&
            !existing.destroyed
        ) {
            context.species =
                existing;

            return existing;
        }

        if (
            context.species instanceof
            SpeciesService &&
            !context.species.destroyed
        ) {
            return context.species;
        }

        const service =
            new SpeciesService(
                context
            );

        context.species =
            service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "taxa-species",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-species-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.species ||
            context?.services?.get?.(
                SERVICE_NAME
            );

        if (
            !(
                service instanceof
                SpeciesService
            )
        ) {
            throw new Error(
                "Species service is unavailable."
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
                    "--species="
                )
            ) {
                parameters.species =
                    argument.slice(10);
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
                    "--scientific-name="
                )
            ) {
                parameters.scientific_name =
                    argument.slice(18);
                continue;
            }

            if (
                argument.startsWith(
                    "--canonical-name="
                )
            ) {
                parameters.canonical_name =
                    argument.slice(17);
                continue;
            }

            if (
                argument.startsWith(
                    "--common-name="
                )
            ) {
                parameters.common_name =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--authorship="
                )
            ) {
                parameters.authorship =
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
                    "--status="
                )
            ) {
                parameters.status =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--accepted-name="
                )
            ) {
                parameters.accepted_name =
                    argument.slice(16);
                continue;
            }

            if (
                argument.startsWith(
                    "--accepted-id="
                )
            ) {
                parameters.accepted_id =
                    argument.slice(14);
                continue;
            }

            if (
                argument.startsWith(
                    "--kingdom="
                )
            ) {
                parameters.kingdom =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--phylum="
                )
            ) {
                parameters.phylum =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--class="
                )
            ) {
                parameters.class =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--order="
                )
            ) {
                parameters.order =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--family="
                )
            ) {
                parameters.family =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--genus="
                )
            ) {
                parameters.genus =
                    argument.slice(8);
                continue;
            }

            if (
                argument.startsWith(
                    "--subgenus="
                )
            ) {
                parameters.subgenus =
                    argument.slice(11);
                continue;
            }

            if (
                argument.startsWith(
                    "--specific-epithet="
                )
            ) {
                parameters.specific_epithet =
                    argument.slice(19);
                continue;
            }

            if (
                argument.startsWith(
                    "--subspecific-epithet="
                )
            ) {
                parameters.subspecific_epithet =
                    argument.slice(22);
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
                    "--region="
                )
            ) {
                parameters.region =
                    argument.slice(9);
                continue;
            }

            if (
                argument.startsWith(
                    "--continent="
                )
            ) {
                parameters.continent =
                    argument.slice(12);
                continue;
            }

            if (
                argument.startsWith(
                    "--habitat="
                )
            ) {
                parameters.habitat =
                    argument.slice(10);
                continue;
            }

            if (
                argument.startsWith(
                    "--environment="
                )
            ) {
                parameters.environment =
                    argument.slice(14);
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
                    "--source="
                )
            ) {
                parameters.source =
                    argument.slice(9);
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
                    "--conservation-status="
                )
            ) {
                parameters.conservation_status =
                    argument.slice(22);
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

            for (
                const [flag, key] of
                [
                    ["--accepted=", "accepted"],
                    ["--synonym=", "synonym"],
                    ["--extinct=", "extinct"],
                    ["--threatened=", "threatened"],
                    ["--endemic=", "endemic"],
                    ["--native=", "native"],
                    ["--introduced=", "introduced"],
                    ["--invasive=", "invasive"],
                    ["--marine=", "marine"],
                    ["--freshwater=", "freshwater"],
                    ["--terrestrial=", "terrestrial"],
                    ["--verified=", "verified"],
                    ["--active=", "active"]
                ]
            ) {
                if (
                    argument.startsWith(
                        flag
                    )
                ) {
                    parameters[key] =
                        argument.slice(
                            flag.length
                        );
                    break;
                }
            }

            if (
                argument.startsWith(
                    "--min-occurrences="
                )
            ) {
                parameters.min_occurrences =
                    argument.slice(18);
                continue;
            }

            if (
                argument.startsWith(
                    "--max-occurrences="
                )
            ) {
                parameters.max_occurrences =
                    argument.slice(18);
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

            if (
                !argument.startsWith("--")
            ) {
                positional.push(argument);
            }
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
            name: "species",
            aliases: [
                "taxa-species"
            ],
            category: "taxonomy",
            description:
                "Search canonical species records.",
            usage:
                "species [query] [limit] [--scientific-name=NAME] [--canonical-name=NAME] [--common-name=NAME] [--authorship=TEXT] [--rank=RANK] [--status=STATUS] [--accepted-name=NAME] [--accepted-id=ID] [--kingdom=KINGDOM] [--phylum=PHYLUM] [--class=CLASS] [--order=ORDER] [--family=FAMILY] [--genus=GENUS] [--subgenus=SUBGENUS] [--specific-epithet=NAME] [--subspecific-epithet=NAME] [--country=COUNTRY] [--region=REGION] [--continent=CONTINENT] [--habitat=HABITAT] [--environment=ENVIRONMENT] [--provider=PROVIDER] [--source=SOURCE] [--license=LICENSE] [--conservation-status=STATUS] [--accepted=true|false] [--synonym=true|false] [--extinct=true|false] [--threatened=true|false] [--endemic=true|false] [--native=true|false] [--introduced=true|false] [--invasive=true|false] [--marine=true|false] [--freshwater=true|false] [--terrestrial=true|false] [--verified=true|false] [--active=true|false] [--min-occurrences=N] [--max-occurrences=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).list(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-get",
            aliases: [
                "taxon-species"
            ],
            category: "taxonomy",
            description:
                "Retrieve one canonical species record by ID or name.",
            usage:
                "species-get <id|scientific-name|common-name>",
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
                        "A species ID or name is required."
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
            name: "species-accepted",
            aliases: [
                "accepted-species"
            ],
            category: "taxonomy",
            description:
                "List accepted canonical species records.",
            usage:
                "species-accepted [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).accepted(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-synonyms",
            aliases: [
                "synonym-species"
            ],
            category: "taxonomy",
            description:
                "List synonym species records or records carrying synonym names.",
            usage:
                "species-synonyms [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).synonyms(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-threatened",
            aliases: [
                "threatened-species"
            ],
            category: "taxonomy",
            description:
                "List threatened species records.",
            usage:
                "species-threatened [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).threatened(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-extinct",
            aliases: [
                "extinct-species"
            ],
            category: "taxonomy",
            description:
                "List extinct species records.",
            usage:
                "species-extinct [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).extinct(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-endemic",
            aliases: [
                "endemic-species"
            ],
            category: "taxonomy",
            description:
                "List endemic species records.",
            usage:
                "species-endemic [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).endemic(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-invasive",
            aliases: [
                "invasive-species"
            ],
            category: "taxonomy",
            description:
                "List invasive species records.",
            usage:
                "species-invasive [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).invasive(
                        parseCommandArguments(
                            args
                        )
                    )
                )
        },
        {
            name: "species-lineage",
            category: "taxonomy",
            description:
                "Show the normalized taxonomic lineage for one species.",
            usage:
                "species-lineage <id|name>",
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
                        "A species ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).lineage(id)
                );
            }
        },
        {
            name: "species-synonym-list",
            category: "taxonomy",
            description:
                "Show accepted-name and synonym information for one species.",
            usage:
                "species-synonym-list <id|name>",
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
                        "A species ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(
                        context
                    ).synonymList(id)
                );
            }
        },
        {
            name: "species-summary",
            aliases: [
                "taxa-species-summary"
            ],
            category: "taxonomy",
            description:
                "Summarize canonical species by status, lineage, provider, geography, habitat, environment, conservation state, and occurrence count.",
            usage:
                "species-summary [filters]",
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
            name: "species-status",
            category: "taxonomy",
            description:
                "Show species service status.",
            usage:
                "species-status",
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
        SpeciesService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeStringArray,
        normalizeTaxonomicStatus,
        normalizeConservationStatus,
        normalizeSynonyms,
        normalizeLineage,
        findSpecies,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalSpecies =
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
