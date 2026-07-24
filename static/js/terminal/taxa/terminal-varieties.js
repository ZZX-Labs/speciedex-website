/*
========================================================================
Speciedex.org
Terminal Varieties Module
========================================================================

Canonical taxonomic variety search and lineage service for SpeciedexTerminal.

Provides:

    • Validated variety and subvariety API requests
    • Parent species, parent subspecies, lineage, provider, source,
      conservation, geography, status, habitat, environment, and date filters
    • Normalized variety records
    • Parent-taxon, synonym, accepted-name, lineage, and distribution helpers
    • Accepted, synonym, deprecated, supported, extinct, threatened,
      endemic, invasive, active, and verified views
    • Parent taxon, status, lineage, provider, source, geography,
      habitat, environment, and conservation summaries
    • Lifecycle events, caching, and resilient service registration
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Varieties";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "varieties";

    const DEFAULT_LIMIT = 50;
    const MIN_LIMIT = 1;
    const MAX_LIMIT = 1000;

    function dispatch(target, name, detail, options = {}) {
        if (!target || typeof target.dispatchEvent !== "function") {
            return false;
        }

        try {
            return target.dispatchEvent(new CustomEvent(name, {
                bubbles: options.bubbles === true,
                cancelable: options.cancelable === true,
                detail
            }));
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

        return Math.min(maximum, Math.max(minimum, parsed));
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
            throw new TypeError(`Invalid date value: ${value}`);
        }

        return new Date(timestamp).toISOString();
    }

    function normalizeSort(value) {
        const normalized = normalizeKey(value || "scientific_name");
        const allowed = new Set([
            "scientific_name",
            "canonical_name",
            "common_name",
            "parent_species",
            "parent_subspecies",
            "varietal_epithet",
            "status",
            "kingdom",
            "phylum",
            "class",
            "order",
            "family",
            "genus",
            "conservation_status",
            "provider",
            "occurrence_count",
            "updated_at",
            "created_at",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(
                `Unsupported varieties sort field: ${value}`
            );
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized = normalizeText(value || "asc").toLowerCase();

        if (normalized !== "asc" && normalized !== "desc") {
            throw new TypeError(
                `Unsupported sort direction: ${value}`
            );
        }

        return normalized;
    }

    function normalizeParameters(parameters = {}) {
        const source = parameters && typeof parameters === "object"
            ? parameters
            : {};

        const normalized = {
            q: normalizeText(source.q ?? source.query ?? ""),
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
            sort: normalizeSort(source.sort),
            direction: normalizeDirection(
                source.direction ?? source.order
            )
        };

        const textKeys = [
            "variety",
            "variety_id",
            "subvariety",
            "subvariety_id",
            "taxon",
            "taxon_id",
            "species",
            "species_id",
            "subspecies",
            "subspecies_id",
            "parent_species",
            "parent_species_id",
            "parent_subspecies",
            "parent_subspecies_id",
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
            "varietal_epithet",
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
        ];

        for (const key of textKeys) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                normalized[key] = normalizeText(source[key]);
            }
        }

        const booleanKeys = [
            "accepted",
            "synonym",
            "deprecated",
            "supported",
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
        ];

        for (const key of booleanKeys) {
            if (
                source[key] !== undefined &&
                source[key] !== null &&
                source[key] !== ""
            ) {
                const value = normalizeBoolean(source[key], null);

                if (value === null) {
                    throw new TypeError(
                        `Invalid ${key} value: ${source[key]}`
                    );
                }

                normalized[key] = value;
            }
        }

        const minOccurrences =
            source.min_occurrences ??
            source.minOccurrences;

        const maxOccurrences =
            source.max_occurrences ??
            source.maxOccurrences;

        if (
            minOccurrences !== undefined &&
            minOccurrences !== null &&
            minOccurrences !== ""
        ) {
            normalized.min_occurrences = clampInteger(
                minOccurrences,
                0,
                0,
                Number.MAX_SAFE_INTEGER
            );
        }

        if (
            maxOccurrences !== undefined &&
            maxOccurrences !== null &&
            maxOccurrences !== ""
        ) {
            normalized.max_occurrences = clampInteger(
                maxOccurrences,
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

        const from = source.from ?? source.since ?? source.start;
        const to = source.to ?? source.until ?? source.end;

        if (from !== undefined && from !== null && from !== "") {
            normalized.from = normalizeDate(from);
        }

        if (to !== undefined && to !== null && to !== "") {
            normalized.to = normalizeDate(to);
        }

        if (
            normalized.from &&
            normalized.to &&
            Date.parse(normalized.from) >
            Date.parse(normalized.to)
        ) {
            throw new RangeError(
                "Varieties start date must not be later than the end date."
            );
        }

        return normalized;
    }

    function normalizeStringArray(value) {
        if (Array.isArray(value)) {
            return [...new Set(
                value.map(normalizeText).filter(Boolean)
            )];
        }

        const text = normalizeText(value);

        if (!text) {
            return [];
        }

        return [...new Set(
            text
                .split(/[;,|]+/)
                .map(normalizeText)
                .filter(Boolean)
        )];
    }

    function normalizeTaxonomicStatus(value) {
        const normalized = normalizeKey(value || "unknown");
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
        const normalized = normalizeText(value).toUpperCase();
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
                    if (item && typeof item === "object") {
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
                                item.status ?? "synonym"
                            ),
                            source: normalizeText(item.source ?? ""),
                            index
                        };
                    }

                    return {
                        id: "",
                        scientific_name: normalizeText(item),
                        authorship: "",
                        status: "synonym",
                        source: "",
                        index
                    };
                })
                .filter(item => item.scientific_name);
        }

        return normalizeStringArray(value).map((name, index) => ({
            id: "",
            scientific_name: name,
            authorship: "",
            status: "synonym",
            source: "",
            index
        }));
    }

    function inferParentSpecies(scientificName) {
        const parts = normalizeText(scientificName)
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubvar\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        return parts.length >= 2
            ? parts.slice(0, 2).join(" ")
            : "";
    }

    function inferParentSubspecies(scientificName) {
        const parts = normalizeText(scientificName)
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubvar\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        return parts.length >= 4
            ? parts.slice(0, 3).join(" ")
            : "";
    }

    function inferSpecificEpithet(scientificName) {
        const parts = normalizeText(scientificName)
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubvar\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        return parts.length >= 2 ? parts[1] : "";
    }

    function inferSubspecificEpithet(scientificName) {
        const parts = normalizeText(scientificName)
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubvar\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        return parts.length >= 4 ? parts[2] : "";
    }

    function inferVarietalEpithet(scientificName) {
        const parts = normalizeText(scientificName)
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubvar\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        if (parts.length >= 4) {
            return parts[parts.length - 1];
        }

        return parts.length >= 3
            ? parts[2]
            : "";
    }

    function normalizeLineage(record) {
        const explicit = Array.isArray(record.lineage)
            ? record.lineage
            : (
                Array.isArray(record.classification)
                    ? record.classification
                    : null
            );

        if (explicit) {
            return explicit
                .map((item, index) => {
                    if (item && typeof item === "object") {
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
                        scientific_name: normalizeText(item),
                        index
                    };
                })
                .filter(item => item.scientific_name);
        }

        const lineage = [];
        const values = [
            ["domain", record.domain],
            ["kingdom", record.kingdom],
            ["phylum", record.phylum],
            ["class", record.class ?? record.class_name ?? record.className],
            ["order", record.order ?? record.order_name ?? record.orderName],
            ["family", record.family],
            ["genus", record.genus],
            ["species", record.parent_species ?? record.parentSpecies],
            ["subspecies", record.parent_subspecies ?? record.parentSubspecies],
            [
                normalizeKey(
                    record.rank ??
                    record.taxon_rank ??
                    record.taxonRank ??
                    "variety"
                ) || "variety",
                record.scientific_name ??
                record.scientificName ??
                record.name
            ]
        ];

        for (const [rank, value] of values) {
            const scientificName = normalizeText(value);

            if (scientificName) {
                lineage.push({
                    id: "",
                    rank,
                    scientific_name: scientificName,
                    index: lineage.length
                });
            }
        }

        return lineage;
    }

    function normalizeRecord(record, index = 0) {
        if (!record || typeof record !== "object") {
            const name = normalizeText(record);

            return {
                index,
                id: name || `variety-${index + 1}`,
                scientific_name: name,
                canonical_name: name,
                parent_species: inferParentSpecies(name),
                parent_species_id: "",
                parent_subspecies: inferParentSubspecies(name),
                parent_subspecies_id: "",
                specific_epithet: inferSpecificEpithet(name),
                subspecific_epithet: inferSubspecificEpithet(name),
                varietal_epithet: inferVarietalEpithet(name),
                common_name: "",
                common_names: [],
                authorship: "",
                rank: "variety",
                status: "unknown",
                accepted: false,
                synonym: false,
                deprecated: false,
                supported: true,
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
                sources: [],
                occurrence_count: 0
            };
        }

        const scientificName = normalizeText(
            record.scientific_name ??
            record.scientificName ??
            record.name ??
            record.canonical_name ??
            record.canonicalName ??
            ""
        );

        const canonicalName = normalizeText(
            record.canonical_name ??
            record.canonicalName ??
            record.canonical ??
            scientificName
        );

        const parentSpecies = normalizeText(
            record.parent_species ??
            record.parentSpecies ??
            record.species_name ??
            record.speciesName ??
            record.species ??
            inferParentSpecies(scientificName)
        );

        const parentSubspecies = normalizeText(
            record.parent_subspecies ??
            record.parentSubspecies ??
            record.subspecies_name ??
            record.subspeciesName ??
            record.subspecies ??
            inferParentSubspecies(scientificName)
        );

        const rank = normalizeKey(
            record.rank ??
            record.taxon_rank ??
            record.taxonRank ??
            (
                record.subvariety
                    ? "subvariety"
                    : "variety"
            )
        ) || "variety";

        const status = normalizeTaxonomicStatus(
            record.status ??
            record.taxonomic_status ??
            record.taxonomicStatus ??
            record.acceptance_status ??
            record.acceptanceStatus
        );

        const conservationStatus = normalizeConservationStatus(
            record.conservation_status ??
            record.conservationStatus ??
            record.iucn_status ??
            record.iucnStatus ??
            ""
        );

        const accepted =
            record.accepted === true ||
            ["accepted", "valid"].includes(status);

        const synonym =
            record.synonym === true ||
            record.is_synonym === true ||
            record.isSynonym === true ||
            status === "synonym";

        const deprecated =
            record.deprecated === true ||
            status === "deprecated";

        const extinct =
            record.extinct === true ||
            ["EX", "EW"].includes(conservationStatus) ||
            status === "extinct";

        const threatened =
            record.threatened === true ||
            ["VU", "EN", "CR", "EW"].includes(conservationStatus);

        const habitats = normalizeStringArray(
            record.habitats ??
            record.habitat
        );

        const environments = normalizeStringArray(
            record.environments ??
            record.environment
        );

        const habitatText = [
            ...habitats,
            ...environments
        ].join(" ").toLowerCase();

        const active =
            record.active !== false &&
            record.deleted !== true &&
            !["inactive", "deleted", "retired"].includes(status);

        return {
            ...record,
            index: record.index ?? index,
            id: normalizeText(
                record.id ??
                record.variety_id ??
                record.varietyId ??
                record.subvariety_id ??
                record.subvarietyId ??
                record.taxon_id ??
                record.taxonId ??
                record.uuid ??
                `variety-${index + 1}`
            ),
            variety_id: normalizeText(
                record.variety_id ??
                record.varietyId ??
                record.taxon_id ??
                record.taxonId ??
                record.id ??
                ""
            ),
            subvariety_id: normalizeText(
                record.subvariety_id ??
                record.subvarietyId ??
                ""
            ),
            scientific_name: scientificName,
            canonical_name: canonicalName,
            parent_species: parentSpecies,
            parent_species_id: normalizeText(
                record.parent_species_id ??
                record.parentSpeciesId ??
                record.species_id ??
                record.speciesId ??
                ""
            ),
            parent_subspecies: parentSubspecies,
            parent_subspecies_id: normalizeText(
                record.parent_subspecies_id ??
                record.parentSubspeciesId ??
                record.subspecies_id ??
                record.subspeciesId ??
                ""
            ),
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
            rank,
            status,
            accepted,
            synonym,
            deprecated,
            supported:
                record.supported !== false &&
                !["unsupported", "disabled"].includes(status),
            verified:
                record.verified === true ||
                ["verified", "confirmed"].includes(
                    normalizeKey(
                        record.verification_status ??
                        record.verificationStatus
                    )
                ),
            active,
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
            domain: normalizeText(record.domain ?? ""),
            kingdom: normalizeText(record.kingdom ?? ""),
            phylum: normalizeText(record.phylum ?? ""),
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
            family: normalizeText(record.family ?? ""),
            genus: normalizeText(record.genus ?? ""),
            subgenus: normalizeText(record.subgenus ?? ""),
            specific_epithet: normalizeText(
                record.specific_epithet ??
                record.specificEpithet ??
                inferSpecificEpithet(scientificName)
            ),
            subspecific_epithet: normalizeText(
                record.subspecific_epithet ??
                record.subspecificEpithet ??
                record.infraspecific_epithet ??
                record.infraspecificEpithet ??
                inferSubspecificEpithet(scientificName)
            ),
            varietal_epithet: normalizeText(
                record.varietal_epithet ??
                record.varietalEpithet ??
                record.variety_epithet ??
                record.varietyEpithet ??
                inferVarietalEpithet(scientificName)
            ),
            conservation_status: conservationStatus,
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
            synonyms: normalizeSynonyms(
                record.synonyms ??
                record.synonym_names ??
                record.synonymNames
            ),
            lineage: normalizeLineage({
                ...record,
                parent_species: parentSpecies,
                parent_subspecies: parentSubspecies,
                scientific_name: scientificName,
                rank
            }),
            countries: normalizeStringArray(
                record.countries ??
                record.country_codes ??
                record.countryCodes ??
                record.country
            ),
            regions: normalizeStringArray(
                record.regions ??
                record.region
            ),
            continents: normalizeStringArray(
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
            providers: normalizeStringArray(
                record.providers ??
                record.provider
            ),
            source: normalizeText(
                record.source ??
                record.source_name ??
                record.sourceName ??
                ""
            ),
            sources: normalizeStringArray(
                record.sources ??
                record.source
            ),
            license: normalizeText(
                record.license ??
                record.licence ??
                ""
            ),
            occurrence_count: Number.isFinite(Number(
                record.occurrence_count ??
                record.occurrenceCount ??
                record.occurrences
            ))
                ? Number(
                    record.occurrence_count ??
                    record.occurrenceCount ??
                    record.occurrences
                )
                : 0,
            category: normalizeText(record.category ?? ""),
            type: normalizeText(record.type ?? ""),
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
        const normalized = normalizeText(key) || "unknown";
        map.set(normalized, (map.get(normalized) || 0) + 1);
    }

    function mapToSortedObject(map) {
        return Object.fromEntries(
            [...map.entries()].sort(
                (left, right) =>
                    right[1] - left[1] ||
                    left[0].localeCompare(right[0])
            )
        );
    }

    function summarize(records) {
        const values = Array.isArray(records) ? records : [];

        const maps = {
            ranks: new Map(),
            statuses: new Map(),
            parentSpecies: new Map(),
            parentSubspecies: new Map(),
            kingdoms: new Map(),
            phyla: new Map(),
            classes: new Map(),
            orders: new Map(),
            families: new Map(),
            genera: new Map(),
            providers: new Map(),
            sources: new Map(),
            countries: new Map(),
            regions: new Map(),
            continents: new Map(),
            conservationStatuses: new Map(),
            habitats: new Map(),
            environments: new Map(),
            categories: new Map(),
            types: new Map()
        };

        let occurrenceCount = 0;
        let synonymCount = 0;

        for (const item of values) {
            incrementMap(maps.ranks, item.rank);
            incrementMap(maps.statuses, item.status);
            incrementMap(maps.parentSpecies, item.parent_species);
            incrementMap(maps.parentSubspecies, item.parent_subspecies);
            incrementMap(maps.kingdoms, item.kingdom);
            incrementMap(maps.phyla, item.phylum);
            incrementMap(maps.classes, item.class);
            incrementMap(maps.orders, item.order);
            incrementMap(maps.families, item.family);
            incrementMap(maps.genera, item.genus);
            incrementMap(maps.providers, item.provider);
            incrementMap(maps.sources, item.source);
            incrementMap(
                maps.conservationStatuses,
                item.conservation_status
            );
            incrementMap(maps.categories, item.category);
            incrementMap(maps.types, item.type);

            for (const country of item.countries) {
                incrementMap(maps.countries, country);
            }

            for (const region of item.regions) {
                incrementMap(maps.regions, region);
            }

            for (const continent of item.continents) {
                incrementMap(maps.continents, continent);
            }

            for (const habitat of item.habitats) {
                incrementMap(maps.habitats, habitat);
            }

            for (const environment of item.environments) {
                incrementMap(maps.environments, environment);
            }

            occurrenceCount += item.occurrence_count || 0;
            synonymCount += item.synonyms.length;
        }

        return {
            total: values.length,
            accepted: values.filter(item => item.accepted).length,
            synonyms: values.filter(item => item.synonym).length,
            synonymNames: synonymCount,
            deprecated: values.filter(item => item.deprecated).length,
            supported: values.filter(item => item.supported).length,
            extinct: values.filter(item => item.extinct).length,
            threatened: values.filter(item => item.threatened).length,
            endemic: values.filter(item => item.endemic).length,
            native: values.filter(item => item.native).length,
            introduced: values.filter(item => item.introduced).length,
            invasive: values.filter(item => item.invasive).length,
            marine: values.filter(item => item.marine).length,
            freshwater: values.filter(item => item.freshwater).length,
            terrestrial: values.filter(item => item.terrestrial).length,
            verified: values.filter(item => item.verified).length,
            active: values.filter(item => item.active).length,
            occurrences: occurrenceCount,
            ranks: mapToSortedObject(maps.ranks),
            statuses: mapToSortedObject(maps.statuses),
            parentSpecies: mapToSortedObject(maps.parentSpecies),
            parentSubspecies: mapToSortedObject(
                maps.parentSubspecies
            ),
            kingdoms: mapToSortedObject(maps.kingdoms),
            phyla: mapToSortedObject(maps.phyla),
            classes: mapToSortedObject(maps.classes),
            orders: mapToSortedObject(maps.orders),
            families: mapToSortedObject(maps.families),
            genera: mapToSortedObject(maps.genera),
            providers: mapToSortedObject(maps.providers),
            sources: mapToSortedObject(maps.sources),
            countries: mapToSortedObject(maps.countries),
            regions: mapToSortedObject(maps.regions),
            continents: mapToSortedObject(maps.continents),
            conservationStatuses: mapToSortedObject(
                maps.conservationStatuses
            ),
            habitats: mapToSortedObject(maps.habitats),
            environments: mapToSortedObject(maps.environments),
            categories: mapToSortedObject(maps.categories),
            types: mapToSortedObject(maps.types)
        };
    }

    function normalizeResponse(payload) {
        if (Array.isArray(payload)) {
            const records = payload.map(normalizeRecord);

            return {
                records,
                total: records.length,
                limit: records.length,
                offset: 0,
                summary: summarize(records),
                raw: payload
            };
        }

        if (payload && typeof payload === "object") {
            const values = Array.isArray(payload.records)
                ? payload.records
                : (
                    Array.isArray(payload.items)
                        ? payload.items
                        : (
                            Array.isArray(payload.varieties)
                                ? payload.varieties
                                : (
                                    Array.isArray(payload.taxa)
                                        ? payload.taxa
                                        : []
                                )
                        )
                );

            const records = values.map(normalizeRecord);

            return {
                records,
                total: Number.isFinite(Number(payload.total))
                    ? Number(payload.total)
                    : records.length,
                limit: Number.isFinite(Number(payload.limit))
                    ? Number(payload.limit)
                    : records.length,
                offset: Number.isFinite(Number(payload.offset))
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
            summary: summarize([]),
            raw: payload
        };
    }

    function findVariety(records, value) {
        const target = normalizeText(value);
        const lower = target.toLowerCase();

        return records.find(item =>
            item.id === target ||
            item.variety_id === target ||
            item.subvariety_id === target ||
            item.scientific_name.toLowerCase() === lower ||
            item.canonical_name.toLowerCase() === lower ||
            item.common_names.some(
                name => name.toLowerCase() === lower
            )
        ) || null;
    }

    class VarietiesService extends EventTarget {
        constructor(context) {
            super();

            if (!context || typeof context !== "object") {
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
                    "Varieties service has been destroyed."
                );
            }

            if (
                !this.context.api ||
                typeof this.context.api.get !== "function"
            ) {
                throw new Error(
                    "Speciedex API client is unavailable."
                );
            }
        }

        emit(name, detail) {
            dispatch(this, name, detail);

            try {
                this.context.events?.emit?.(
                    `varieties:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not break variety operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-varieties-${name}`,
                detail,
                { bubbles: true }
            );
        }

        async list(parameters = {}, options = {}) {
            this.ensureAvailable();

            const normalized = normalizeParameters(parameters);
            const startedAt = performance.now();

            this.emit("request", {
                operation: "list",
                parameters: normalized
            });

            try {
                const payload = await this.context.api.get(
                    "taxa/varieties",
                    normalized,
                    options
                );

                const result = normalizeResponse(payload);

                result.parameters = normalized;
                result.duration = performance.now() - startedAt;

                this.cache = result;
                this.cacheTimestamp = Date.now();

                this.emit("complete", result);

                return result;
            } catch (error) {
                this.emit("error", {
                    operation: "list",
                    error,
                    parameters: normalized,
                    duration: performance.now() - startedAt
                });

                throw error;
            }
        }

        async get(id, options = {}) {
            this.ensureAvailable();

            const normalizedId = normalizeText(id);

            if (!normalizedId) {
                throw new TypeError(
                    "A variety or subvariety ID or name is required."
                );
            }

            try {
                const payload = await this.context.api.get(
                    `taxa/varieties/${encodeURIComponent(normalizedId)}`,
                    {},
                    options
                );

                return normalizeRecord(payload, 0);
            } catch (error) {
                const match = findVariety(
                    this.cache?.records || [],
                    normalizedId
                );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async bySpecies(species, parameters = {}, options = {}) {
            const normalizedSpecies = normalizeText(species);

            if (!normalizedSpecies) {
                throw new TypeError(
                    "A parent species ID or name is required."
                );
            }

            const result = await this.list({
                ...parameters,
                parent_species: normalizedSpecies
            }, options);

            const lower = normalizedSpecies.toLowerCase();

            const records = result.records.filter(item =>
                item.parent_species.toLowerCase() === lower ||
                item.parent_species_id === normalizedSpecies
            );

            return {
                ...result,
                parent_species: normalizedSpecies,
                records,
                summary: summarize(records)
            };
        }

        async bySubspecies(subspecies, parameters = {}, options = {}) {
            const normalizedSubspecies = normalizeText(subspecies);

            if (!normalizedSubspecies) {
                throw new TypeError(
                    "A parent subspecies ID or name is required."
                );
            }

            const result = await this.list({
                ...parameters,
                parent_subspecies: normalizedSubspecies
            }, options);

            const lower = normalizedSubspecies.toLowerCase();

            const records = result.records.filter(item =>
                item.parent_subspecies.toLowerCase() === lower ||
                item.parent_subspecies_id === normalizedSubspecies
            );

            return {
                ...result,
                parent_subspecies: normalizedSubspecies,
                records,
                summary: summarize(records)
            };
        }

        async filtered(flag, parameters = {}, options = {}) {
            const result = await this.list({
                ...parameters,
                [flag]: true
            }, options);

            const records = result.records.filter(item => {
                if (flag === "synonym") {
                    return item.synonym || item.synonyms.length;
                }

                return Boolean(item[flag]);
            });

            return {
                ...result,
                records,
                summary: summarize(records)
            };
        }

        accepted(parameters = {}, options = {}) {
            return this.filtered("accepted", parameters, options);
        }

        synonyms(parameters = {}, options = {}) {
            return this.filtered("synonym", parameters, options);
        }

        deprecated(parameters = {}, options = {}) {
            return this.filtered("deprecated", parameters, options);
        }

        supported(parameters = {}, options = {}) {
            return this.filtered("supported", parameters, options);
        }

        threatened(parameters = {}, options = {}) {
            return this.filtered("threatened", parameters, options);
        }

        extinct(parameters = {}, options = {}) {
            return this.filtered("extinct", parameters, options);
        }

        endemic(parameters = {}, options = {}) {
            return this.filtered("endemic", parameters, options);
        }

        invasive(parameters = {}, options = {}) {
            return this.filtered("invasive", parameters, options);
        }

        async lineage(id, options = {}) {
            const record = await this.get(id, options);

            return {
                id: record.id,
                scientific_name: record.scientific_name,
                rank: record.rank,
                parent_species: record.parent_species,
                parent_species_id: record.parent_species_id,
                parent_subspecies: record.parent_subspecies,
                parent_subspecies_id: record.parent_subspecies_id,
                lineage: record.lineage
            };
        }

        async synonymList(id, options = {}) {
            const record = await this.get(id, options);

            return {
                id: record.id,
                scientific_name: record.scientific_name,
                accepted: record.accepted,
                accepted_name: record.accepted_name,
                accepted_id: record.accepted_id,
                synonyms: record.synonyms
            };
        }

        async summary(parameters = {}, options = {}) {
            const result = await this.list({
                ...parameters,
                limit: parameters.limit ?? MAX_LIMIT
            }, options);

            return {
                parameters: result.parameters,
                summary: summarize(result.records),
                varieties: result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint: "taxa/varieties",
                service: SERVICE_NAME,
                available: Boolean(
                    this.context.api &&
                    typeof this.context.api.get === "function"
                ),
                cached: Boolean(this.cache),
                cacheAge: this.cacheTimestamp
                    ? Date.now() - this.cacheTimestamp
                    : null,
                destroyed: this.destroyed
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.cache = null;
            this.cacheTimestamp = 0;
            this.destroyed = true;

            dispatch(this, "destroy", {
                timestamp: new Date().toISOString()
            });

            return true;
        }
    }

    function initialize(context) {
        const existing = context.services?.get?.(SERVICE_NAME);

        if (
            existing instanceof VarietiesService &&
            !existing.destroyed
        ) {
            context.varieties = existing;
            return existing;
        }

        if (
            context.varieties instanceof VarietiesService &&
            !context.varieties.destroyed
        ) {
            return context.varieties;
        }

        const service = new VarietiesService(context);

        context.varieties = service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "taxa-varieties",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-varieties-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.varieties ||
            context?.services?.get?.(SERVICE_NAME);

        if (!(service instanceof VarietiesService)) {
            throw new Error(
                "Varieties service is unavailable."
            );
        }

        return service;
    }

    function parseCommandArguments(args = []) {
        const parameters = {};
        const positional = [];

        const textFlags = {
            "--variety=": "variety",
            "--variety-id=": "variety_id",
            "--subvariety=": "subvariety",
            "--subvariety-id=": "subvariety_id",
            "--taxon=": "taxon",
            "--taxon-id=": "taxon_id",
            "--species=": "species",
            "--species-id=": "species_id",
            "--subspecies=": "subspecies",
            "--subspecies-id=": "subspecies_id",
            "--parent-species=": "parent_species",
            "--parent-species-id=": "parent_species_id",
            "--parent-subspecies=": "parent_subspecies",
            "--parent-subspecies-id=": "parent_subspecies_id",
            "--scientific-name=": "scientific_name",
            "--canonical-name=": "canonical_name",
            "--common-name=": "common_name",
            "--authorship=": "authorship",
            "--rank=": "rank",
            "--status=": "status",
            "--accepted-name=": "accepted_name",
            "--accepted-id=": "accepted_id",
            "--kingdom=": "kingdom",
            "--phylum=": "phylum",
            "--class=": "class",
            "--order=": "order",
            "--family=": "family",
            "--genus=": "genus",
            "--subgenus=": "subgenus",
            "--specific-epithet=": "specific_epithet",
            "--subspecific-epithet=": "subspecific_epithet",
            "--varietal-epithet=": "varietal_epithet",
            "--country=": "country",
            "--region=": "region",
            "--continent=": "continent",
            "--habitat=": "habitat",
            "--environment=": "environment",
            "--provider=": "provider",
            "--source=": "source",
            "--license=": "license",
            "--conservation-status=": "conservation_status",
            "--category=": "category",
            "--type=": "type",
            "--from=": "from",
            "--to=": "to",
            "--sort=": "sort",
            "--direction=": "direction",
            "--limit=": "limit",
            "--offset=": "offset",
            "--min-occurrences=": "min_occurrences",
            "--max-occurrences=": "max_occurrences"
        };

        const booleanFlags = {
            "--accepted=": "accepted",
            "--synonym=": "synonym",
            "--deprecated=": "deprecated",
            "--supported=": "supported",
            "--extinct=": "extinct",
            "--threatened=": "threatened",
            "--endemic=": "endemic",
            "--native=": "native",
            "--introduced=": "introduced",
            "--invasive=": "invasive",
            "--marine=": "marine",
            "--freshwater=": "freshwater",
            "--terrestrial=": "terrestrial",
            "--verified=": "verified",
            "--active=": "active"
        };

        for (const argument of args) {
            let matched = false;

            for (const [flag, key] of Object.entries(textFlags)) {
                if (argument.startsWith(flag)) {
                    parameters[key] = argument.slice(flag.length);
                    matched = true;
                    break;
                }
            }

            if (matched) {
                continue;
            }

            for (const [flag, key] of Object.entries(booleanFlags)) {
                if (argument.startsWith(flag)) {
                    parameters[key] = argument.slice(flag.length);
                    matched = true;
                    break;
                }
            }

            if (!matched && !argument.startsWith("--")) {
                positional.push(argument);
            }
        }

        if (positional.length) {
            parameters.q = positional[0];
        }

        if (positional[1] !== undefined) {
            parameters.limit = positional[1];
        }

        return normalizeParameters(parameters);
    }

    function writeJSONValue(writeJSON, value) {
        return typeof writeJSON === "function"
            ? writeJSON(value)
            : value;
    }

    function filteredCommand(name, aliases, description, method) {
        return {
            name,
            aliases,
            category: "taxonomy",
            description,
            usage: `${name} [filters]`,
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context)[method](
                        parseCommandArguments(args)
                    )
                )
        };
    }

    const commands = [
        {
            name: "varieties",
            aliases: [
                "taxa-varieties"
            ],
            category: "taxonomy",
            description:
                "Search taxonomic varieties.",
            usage:
                "varieties [query] [limit] [--parent-species=NAME] [--parent-subspecies=NAME] [--scientific-name=NAME] [--status=STATUS] [--provider=PROVIDER] [--country=COUNTRY] [--conservation-status=STATUS] [--accepted=true|false] [--synonym=true|false] [--deprecated=true|false] [--supported=true|false] [--extinct=true|false] [--threatened=true|false] [--endemic=true|false] [--invasive=true|false] [--verified=true|false] [--active=true|false] [--min-occurrences=N] [--max-occurrences=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context).list(
                        parseCommandArguments(args)
                    )
                )
        },
        {
            name: "variety",
            aliases: [
                "variety-get"
            ],
            category: "taxonomy",
            description:
                "Retrieve one variety or subvariety by ID or name.",
            usage:
                "variety <id|scientific-name|common-name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A variety or subvariety ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).get(id)
                );
            }
        },
        {
            name: "varieties-by-species",
            aliases: [
                "species-varieties"
            ],
            category: "taxonomy",
            description:
                "List varieties belonging to one parent species.",
            usage:
                "varieties-by-species <species-id|species-name> [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                if (!args.length) {
                    throw new Error(
                        "A parent species ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).bySpecies(
                        args[0],
                        parseCommandArguments(args.slice(1))
                    )
                );
            }
        },
        {
            name: "varieties-by-subspecies",
            aliases: [
                "subspecies-varieties"
            ],
            category: "taxonomy",
            description:
                "List varieties belonging to one parent subspecies.",
            usage:
                "varieties-by-subspecies <subspecies-id|subspecies-name> [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                if (!args.length) {
                    throw new Error(
                        "A parent subspecies ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).bySubspecies(
                        args[0],
                        parseCommandArguments(args.slice(1))
                    )
                );
            }
        },
        filteredCommand(
            "varieties-accepted",
            ["accepted-varieties"],
            "List accepted variety records.",
            "accepted"
        ),
        filteredCommand(
            "varieties-synonyms",
            ["synonym-varieties"],
            "List synonym varieties or records carrying synonym names.",
            "synonyms"
        ),
        filteredCommand(
            "varieties-deprecated",
            ["deprecated-varieties"],
            "List deprecated variety records.",
            "deprecated"
        ),
        filteredCommand(
            "varieties-supported",
            ["supported-varieties"],
            "List supported variety records.",
            "supported"
        ),
        filteredCommand(
            "varieties-threatened",
            ["threatened-varieties"],
            "List threatened variety records.",
            "threatened"
        ),
        filteredCommand(
            "varieties-extinct",
            ["extinct-varieties"],
            "List extinct variety records.",
            "extinct"
        ),
        filteredCommand(
            "varieties-endemic",
            ["endemic-varieties"],
            "List endemic variety records.",
            "endemic"
        ),
        filteredCommand(
            "varieties-invasive",
            ["invasive-varieties"],
            "List invasive variety records.",
            "invasive"
        ),
        {
            name: "variety-lineage",
            category: "taxonomy",
            description:
                "Show normalized lineage and parent taxa for one variety.",
            usage:
                "variety-lineage <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A variety or subvariety ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).lineage(id)
                );
            }
        },
        {
            name: "variety-synonym-list",
            category: "taxonomy",
            description:
                "Show accepted-name and synonym information for one variety.",
            usage:
                "variety-synonym-list <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A variety or subvariety ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).synonymList(id)
                );
            }
        },
        {
            name: "varieties-summary",
            aliases: [
                "variety-summary"
            ],
            category: "taxonomy",
            description:
                "Summarize varieties by parent species, parent subspecies, rank, status, lineage, provider, source, geography, habitat, environment, conservation state, and occurrence count.",
            usage:
                "varieties-summary [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context).summary(
                        parseCommandArguments(args)
                    )
                )
        },
        {
            name: "varieties-status",
            category: "taxonomy",
            description:
                "Show varieties service status.",
            usage:
                "varieties-status",
            handler: ({
                context,
                writeJSON
            }) =>
                writeJSONValue(
                    writeJSON,
                    requireService(context).status()
                )
        }
    ];

    const api = Object.freeze({
        name: MODULE_NAME,
        version: VERSION,
        serviceName: SERVICE_NAME,
        VarietiesService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeStringArray,
        normalizeTaxonomicStatus,
        normalizeConservationStatus,
        normalizeSynonyms,
        normalizeLineage,
        inferParentSpecies,
        inferParentSubspecies,
        inferSpecificEpithet,
        inferSubspecificEpithet,
        inferVarietalEpithet,
        findVariety,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalVarieties = api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};

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
