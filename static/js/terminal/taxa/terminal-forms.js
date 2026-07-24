/*
========================================================================
Speciedex.org
Terminal Forms Module
========================================================================

Taxonomic form and subform search service for SpeciedexTerminal.

Provides validated API requests, normalized infraspecific form records,
parent-species/subspecies/variety resolution, lineage and synonym helpers,
status and conservation views, summaries, caching, lifecycle events,
resilient service registration, and terminal command integration.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Forms";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "forms";
    const DEFAULT_LIMIT = 50;
    const MAX_LIMIT = 1000;

    function emit(target, name, detail, options = {}) {
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

    function text(value) {
        return String(value ?? "").trim();
    }

    function key(value) {
        return text(value)
            .toLowerCase()
            .replace(/[\s-]+/g, "_")
            .replace(/[^a-z0-9_]/g, "");
    }

    function integer(value, fallback, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
        const parsed = Number.parseInt(value, 10);

        return Number.isFinite(parsed)
            ? Math.min(maximum, Math.max(minimum, parsed))
            : fallback;
    }

    function number(value, fallback = 0) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function boolean(value, fallback = null) {
        if (typeof value === "boolean") {
            return value;
        }

        const normalized = text(value).toLowerCase();

        if (value === 1 || normalized === "1" || normalized === "true") {
            return true;
        }

        if (value === 0 || normalized === "0" || normalized === "false") {
            return false;
        }

        return fallback;
    }

    function isoDate(value) {
        const normalized = text(value);

        if (!normalized) {
            return "";
        }

        const timestamp = Date.parse(normalized);

        if (!Number.isFinite(timestamp)) {
            throw new TypeError(`Invalid date value: ${value}`);
        }

        return new Date(timestamp).toISOString();
    }

    function stringArray(value) {
        const values = Array.isArray(value)
            ? value
            : text(value).split(/[;,|]+/);

        return [...new Set(values.map(text).filter(Boolean))];
    }

    function taxonomicStatus(value) {
        const normalized = key(value || "unknown");

        return {
            valid: "accepted",
            current: "accepted",
            synonymized: "synonym",
            unaccepted: "synonym",
            uncertain: "unresolved",
            ambiguous: "unresolved",
            deleted: "inactive"
        }[normalized] || normalized;
    }

    function conservationStatus(value) {
        const normalized = text(value).toUpperCase();

        return {
            "LEAST CONCERN": "LC",
            "NEAR THREATENED": "NT",
            "VULNERABLE": "VU",
            "ENDANGERED": "EN",
            "CRITICALLY ENDANGERED": "CR",
            "EXTINCT IN THE WILD": "EW",
            "EXTINCT": "EX",
            "DATA DEFICIENT": "DD",
            "NOT EVALUATED": "NE"
        }[normalized] || normalized;
    }

    function sortField(value) {
        const normalized = key(value || "scientific_name");
        const allowed = new Set([
            "scientific_name",
            "canonical_name",
            "common_name",
            "parent_species",
            "parent_subspecies",
            "parent_variety",
            "form_epithet",
            "status",
            "conservation_status",
            "provider",
            "occurrence_count",
            "updated_at",
            "created_at",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(`Unsupported forms sort field: ${value}`);
        }

        return normalized;
    }

    function direction(value) {
        const normalized = text(value || "asc").toLowerCase();

        if (normalized !== "asc" && normalized !== "desc") {
            throw new TypeError(`Unsupported sort direction: ${value}`);
        }

        return normalized;
    }

    function normalizeParameters(parameters = {}) {
        const source = parameters && typeof parameters === "object"
            ? parameters
            : {};

        const normalized = {
            q: text(source.q ?? source.query ?? ""),
            limit: integer(source.limit, DEFAULT_LIMIT, 1, MAX_LIMIT),
            offset: integer(source.offset, 0),
            sort: sortField(source.sort),
            direction: direction(source.direction ?? source.order)
        };

        const textFields = [
            "form", "form_id", "subform", "subform_id", "taxon", "taxon_id",
            "species", "species_id", "subspecies", "subspecies_id",
            "variety", "variety_id", "parent_species", "parent_species_id",
            "parent_subspecies", "parent_subspecies_id", "parent_variety",
            "parent_variety_id", "scientific_name", "canonical_name",
            "common_name", "authorship", "rank", "status", "accepted_name",
            "accepted_id", "kingdom", "phylum", "class", "order", "family",
            "genus", "subgenus", "specific_epithet", "subspecific_epithet",
            "varietal_epithet", "form_epithet", "country", "region",
            "continent", "habitat", "environment", "provider", "source",
            "license", "conservation_status", "category", "type"
        ];

        for (const field of textFields) {
            if (source[field] !== undefined && source[field] !== null && source[field] !== "") {
                normalized[field] = text(source[field]);
            }
        }

        const booleanFields = [
            "accepted", "synonym", "deprecated", "supported", "extinct",
            "threatened", "endemic", "native", "introduced", "invasive",
            "marine", "freshwater", "terrestrial", "verified", "active"
        ];

        for (const field of booleanFields) {
            if (source[field] !== undefined && source[field] !== null && source[field] !== "") {
                const parsed = boolean(source[field]);

                if (parsed === null) {
                    throw new TypeError(`Invalid ${field} value: ${source[field]}`);
                }

                normalized[field] = parsed;
            }
        }

        const minimum = source.min_occurrences ?? source.minOccurrences;
        const maximum = source.max_occurrences ?? source.maxOccurrences;

        if (minimum !== undefined && minimum !== null && minimum !== "") {
            normalized.min_occurrences = integer(minimum, 0);
        }

        if (maximum !== undefined && maximum !== null && maximum !== "") {
            normalized.max_occurrences = integer(maximum, Number.MAX_SAFE_INTEGER);
        }

        if (
            normalized.min_occurrences !== undefined &&
            normalized.max_occurrences !== undefined &&
            normalized.min_occurrences > normalized.max_occurrences
        ) {
            throw new RangeError(
                "Minimum occurrence count must not exceed maximum occurrence count."
            );
        }

        const from = source.from ?? source.since ?? source.start;
        const to = source.to ?? source.until ?? source.end;

        if (from !== undefined && from !== null && from !== "") {
            normalized.from = isoDate(from);
        }

        if (to !== undefined && to !== null && to !== "") {
            normalized.to = isoDate(to);
        }

        if (
            normalized.from &&
            normalized.to &&
            Date.parse(normalized.from) > Date.parse(normalized.to)
        ) {
            throw new RangeError(
                "Forms start date must not be later than the end date."
            );
        }

        return normalized;
    }

    function normalizeRelations(value, fallbackRank) {
        const values = Array.isArray(value)
            ? value
            : stringArray(value);

        return values.map((item, index) => {
            if (item && typeof item === "object") {
                return {
                    id: text(item.id ?? item.taxon_id ?? item.taxonId ?? ""),
                    rank: key(item.rank ?? item.taxon_rank ?? item.taxonRank ?? fallbackRank),
                    scientific_name: text(item.scientific_name ?? item.scientificName ?? item.name ?? ""),
                    authorship: text(
                        item.authorship ??
                        item.scientific_name_authorship ??
                        item.scientificNameAuthorship ??
                        ""
                    ),
                    status: taxonomicStatus(item.status ?? "accepted"),
                    source: text(item.source ?? ""),
                    index
                };
            }

            return {
                id: "",
                rank: fallbackRank,
                scientific_name: text(item),
                authorship: "",
                status: "accepted",
                source: "",
                index
            };
        }).filter(item => item.scientific_name);
    }

    function inferParts(scientificName) {
        const clean = text(scientificName)
            .replace(/\bsubf\.?\b/gi, " ")
            .replace(/\bf\.?\b/gi, " ")
            .replace(/\bforma\b/gi, " ")
            .replace(/\bvar\.?\b/gi, " ")
            .replace(/\bsubsp\.?\b/gi, " ")
            .replace(/\bssp\.?\b/gi, " ")
            .split(/\s+/)
            .filter(Boolean);

        return {
            parent_species: clean.length >= 2 ? clean.slice(0, 2).join(" ") : "",
            parent_subspecies: clean.length >= 4 ? clean.slice(0, 3).join(" ") : "",
            parent_variety: clean.length >= 5 ? clean.slice(0, 4).join(" ") : "",
            specific_epithet: clean[1] || "",
            subspecific_epithet: clean.length >= 4 ? clean[2] : "",
            varietal_epithet: clean.length >= 5 ? clean[3] : "",
            form_epithet: clean.length >= 3 ? clean[clean.length - 1] : ""
        };
    }

    function normalizeLineage(record) {
        if (Array.isArray(record.lineage)) {
            return normalizeRelations(record.lineage, "form");
        }

        const lineage = [];
        const levels = [
            ["domain", record.domain],
            ["kingdom", record.kingdom],
            ["phylum", record.phylum],
            ["class", record.class ?? record.class_name ?? record.className],
            ["order", record.order ?? record.order_name ?? record.orderName],
            ["family", record.family],
            ["genus", record.genus],
            ["species", record.parent_species ?? record.parentSpecies],
            ["subspecies", record.parent_subspecies ?? record.parentSubspecies],
            ["variety", record.parent_variety ?? record.parentVariety],
            [key(record.rank ?? "form") || "form", record.scientific_name ?? record.scientificName ?? record.name]
        ];

        for (const [rank, value] of levels) {
            const name = text(value);

            if (name) {
                lineage.push({
                    id: "",
                    rank,
                    scientific_name: name,
                    authorship: "",
                    status: "accepted",
                    source: "",
                    index: lineage.length
                });
            }
        }

        return lineage;
    }

    function normalizeRecord(record, index = 0) {
        if (!record || typeof record !== "object") {
            record = { scientific_name: text(record) };
        }

        const scientificName = text(
            record.scientific_name ??
            record.scientificName ??
            record.name ??
            record.canonical_name ??
            record.canonicalName ??
            ""
        );

        const inferred = inferParts(scientificName);
        const rank = key(
            record.rank ??
            record.taxon_rank ??
            record.taxonRank ??
            (record.subform ? "subform" : "form")
        ) || "form";

        const status = taxonomicStatus(
            record.status ??
            record.taxonomic_status ??
            record.taxonomicStatus ??
            record.acceptance_status ??
            record.acceptanceStatus
        );

        const conservation = conservationStatus(
            record.conservation_status ??
            record.conservationStatus ??
            record.iucn_status ??
            record.iucnStatus ??
            ""
        );

        const habitats = stringArray(record.habitats ?? record.habitat);
        const environments = stringArray(record.environments ?? record.environment);
        const habitatText = [...habitats, ...environments].join(" ").toLowerCase();

        const accepted = record.accepted === true ||
            ["accepted", "valid"].includes(status);

        const synonym = record.synonym === true ||
            record.is_synonym === true ||
            record.isSynonym === true ||
            status === "synonym";

        const deprecated = record.deprecated === true ||
            status === "deprecated";

        const extinct = record.extinct === true ||
            ["EX", "EW"].includes(conservation) ||
            status === "extinct";

        const threatened = record.threatened === true ||
            ["VU", "EN", "CR", "EW"].includes(conservation);

        const parentSpecies = text(
            record.parent_species ??
            record.parentSpecies ??
            record.species_name ??
            record.speciesName ??
            record.species ??
            inferred.parent_species
        );

        const parentSubspecies = text(
            record.parent_subspecies ??
            record.parentSubspecies ??
            record.subspecies_name ??
            record.subspeciesName ??
            record.subspecies ??
            inferred.parent_subspecies
        );

        const parentVariety = text(
            record.parent_variety ??
            record.parentVariety ??
            record.variety_name ??
            record.varietyName ??
            record.variety ??
            inferred.parent_variety
        );

        return {
            ...record,
            index: record.index ?? index,
            id: text(
                record.id ??
                record.form_id ??
                record.formId ??
                record.subform_id ??
                record.subformId ??
                record.taxon_id ??
                record.taxonId ??
                record.uuid ??
                `form-${index + 1}`
            ),
            form_id: text(
                record.form_id ??
                record.formId ??
                record.taxon_id ??
                record.taxonId ??
                record.id ??
                ""
            ),
            subform_id: text(
                record.subform_id ??
                record.subformId ??
                ""
            ),
            scientific_name: scientificName,
            canonical_name: text(
                record.canonical_name ??
                record.canonicalName ??
                record.canonical ??
                scientificName
            ),
            common_name: text(record.common_name ?? record.commonName ?? ""),
            common_names: stringArray(
                record.common_names ??
                record.commonNames ??
                record.vernacular_names ??
                record.vernacularNames ??
                record.common_name ??
                record.commonName
            ),
            authorship: text(
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
            supported: record.supported !== false &&
                !["unsupported", "disabled"].includes(status),
            verified: record.verified === true ||
                ["verified", "confirmed"].includes(
                    key(
                        record.verification_status ??
                        record.verificationStatus
                    )
                ),
            active: record.active !== false &&
                record.deleted !== true &&
                !["inactive", "deleted", "retired"].includes(status),
            accepted_name: text(
                record.accepted_name ??
                record.acceptedName ??
                ""
            ),
            accepted_id: text(
                record.accepted_id ??
                record.acceptedId ??
                record.accepted_taxon_id ??
                record.acceptedTaxonId ??
                ""
            ),
            parent_species: parentSpecies,
            parent_species_id: text(
                record.parent_species_id ??
                record.parentSpeciesId ??
                record.species_id ??
                record.speciesId ??
                ""
            ),
            parent_subspecies: parentSubspecies,
            parent_subspecies_id: text(
                record.parent_subspecies_id ??
                record.parentSubspeciesId ??
                record.subspecies_id ??
                record.subspeciesId ??
                ""
            ),
            parent_variety: parentVariety,
            parent_variety_id: text(
                record.parent_variety_id ??
                record.parentVarietyId ??
                record.variety_id ??
                record.varietyId ??
                ""
            ),
            domain: text(record.domain ?? ""),
            kingdom: text(record.kingdom ?? ""),
            phylum: text(record.phylum ?? ""),
            class: text(
                record.class ??
                record.class_name ??
                record.className ??
                ""
            ),
            order: text(
                record.order ??
                record.order_name ??
                record.orderName ??
                ""
            ),
            family: text(record.family ?? ""),
            genus: text(record.genus ?? ""),
            subgenus: text(record.subgenus ?? ""),
            specific_epithet: text(
                record.specific_epithet ??
                record.specificEpithet ??
                inferred.specific_epithet
            ),
            subspecific_epithet: text(
                record.subspecific_epithet ??
                record.subspecificEpithet ??
                inferred.subspecific_epithet
            ),
            varietal_epithet: text(
                record.varietal_epithet ??
                record.varietalEpithet ??
                inferred.varietal_epithet
            ),
            form_epithet: text(
                record.form_epithet ??
                record.formEpithet ??
                record.forma_epithet ??
                record.formaEpithet ??
                inferred.form_epithet
            ),
            conservation_status: conservation,
            extinct,
            threatened,
            endemic: record.endemic === true ||
                record.is_endemic === true ||
                record.isEndemic === true,
            native: record.native === true ||
                record.is_native === true ||
                record.isNative === true,
            introduced: record.introduced === true ||
                record.is_introduced === true ||
                record.isIntroduced === true,
            invasive: record.invasive === true ||
                record.is_invasive === true ||
                record.isInvasive === true,
            marine: record.marine === true ||
                habitatText.includes("marine") ||
                habitatText.includes("ocean"),
            freshwater: record.freshwater === true ||
                habitatText.includes("freshwater") ||
                habitatText.includes("river") ||
                habitatText.includes("lake"),
            terrestrial: record.terrestrial === true ||
                habitatText.includes("terrestrial") ||
                habitatText.includes("land"),
            synonyms: normalizeRelations(
                record.synonyms ??
                record.synonym_names ??
                record.synonymNames,
                "form"
            ),
            lineage: normalizeLineage({
                ...record,
                parent_species: parentSpecies,
                parent_subspecies: parentSubspecies,
                parent_variety: parentVariety,
                scientific_name: scientificName,
                rank
            }),
            countries: stringArray(
                record.countries ??
                record.country_codes ??
                record.countryCodes ??
                record.country
            ),
            regions: stringArray(record.regions ?? record.region),
            continents: stringArray(record.continents ?? record.continent),
            habitats,
            environments,
            provider: text(
                record.provider ??
                record.provider_name ??
                record.providerName ??
                ""
            ),
            providers: stringArray(record.providers ?? record.provider),
            source: text(
                record.source ??
                record.source_name ??
                record.sourceName ??
                ""
            ),
            sources: stringArray(record.sources ?? record.source),
            license: text(record.license ?? record.licence ?? ""),
            occurrence_count: number(
                record.occurrence_count ??
                record.occurrenceCount ??
                record.occurrences
            ),
            category: text(record.category ?? ""),
            type: text(record.type ?? ""),
            created_at: record.created_at ?? record.createdAt ?? "",
            updated_at:
                record.updated_at ??
                record.updatedAt ??
                record.last_updated ??
                record.lastUpdated ??
                ""
        };
    }

    function increment(map, value) {
        const normalized = text(value) || "unknown";
        map.set(normalized, (map.get(normalized) || 0) + 1);
    }

    function sortedObject(map) {
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
        const maps = Object.fromEntries(
            [
                "ranks", "statuses", "parentSpecies", "parentSubspecies",
                "parentVarieties", "kingdoms", "phyla", "classes", "orders",
                "families", "genera", "providers", "sources", "countries",
                "regions", "continents", "conservationStatuses", "habitats",
                "environments", "categories", "types"
            ].map(name => [name, new Map()])
        );

        let occurrenceCount = 0;
        let synonymCount = 0;

        for (const item of values) {
            increment(maps.ranks, item.rank);
            increment(maps.statuses, item.status);
            increment(maps.parentSpecies, item.parent_species);
            increment(maps.parentSubspecies, item.parent_subspecies);
            increment(maps.parentVarieties, item.parent_variety);
            increment(maps.kingdoms, item.kingdom);
            increment(maps.phyla, item.phylum);
            increment(maps.classes, item.class);
            increment(maps.orders, item.order);
            increment(maps.families, item.family);
            increment(maps.genera, item.genus);
            increment(maps.providers, item.provider);
            increment(maps.sources, item.source);
            increment(maps.conservationStatuses, item.conservation_status);
            increment(maps.categories, item.category);
            increment(maps.types, item.type);

            item.countries.forEach(value => increment(maps.countries, value));
            item.regions.forEach(value => increment(maps.regions, value));
            item.continents.forEach(value => increment(maps.continents, value));
            item.habitats.forEach(value => increment(maps.habitats, value));
            item.environments.forEach(value => increment(maps.environments, value));

            occurrenceCount += item.occurrence_count;
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
            ranks: sortedObject(maps.ranks),
            statuses: sortedObject(maps.statuses),
            parentSpecies: sortedObject(maps.parentSpecies),
            parentSubspecies: sortedObject(maps.parentSubspecies),
            parentVarieties: sortedObject(maps.parentVarieties),
            kingdoms: sortedObject(maps.kingdoms),
            phyla: sortedObject(maps.phyla),
            classes: sortedObject(maps.classes),
            orders: sortedObject(maps.orders),
            families: sortedObject(maps.families),
            genera: sortedObject(maps.genera),
            providers: sortedObject(maps.providers),
            sources: sortedObject(maps.sources),
            countries: sortedObject(maps.countries),
            regions: sortedObject(maps.regions),
            continents: sortedObject(maps.continents),
            conservationStatuses: sortedObject(maps.conservationStatuses),
            habitats: sortedObject(maps.habitats),
            environments: sortedObject(maps.environments),
            categories: sortedObject(maps.categories),
            types: sortedObject(maps.types)
        };
    }

    function normalizeResponse(payload) {
        const source = Array.isArray(payload)
            ? payload
            : (
                payload && typeof payload === "object"
                    ? (
                        Array.isArray(payload.records)
                            ? payload.records
                            : (
                                Array.isArray(payload.items)
                                    ? payload.items
                                    : (
                                        Array.isArray(payload.forms)
                                            ? payload.forms
                                            : (
                                                Array.isArray(payload.taxa)
                                                    ? payload.taxa
                                                    : []
                                            )
                                    )
                            )
                    )
                    : []
            );

        const records = source.map(normalizeRecord);

        return {
            records,
            total: Number.isFinite(Number(payload?.total))
                ? Number(payload.total)
                : records.length,
            limit: Number.isFinite(Number(payload?.limit))
                ? Number(payload.limit)
                : records.length,
            offset: Number.isFinite(Number(payload?.offset))
                ? Number(payload.offset)
                : 0,
            summary: payload?.summary && typeof payload.summary === "object"
                ? { ...summarize(records), ...payload.summary }
                : summarize(records),
            next: payload?.next ?? payload?.nextPage ?? null,
            previous: payload?.previous ?? payload?.previousPage ?? null,
            raw: payload
        };
    }

    function findForm(records, value) {
        const target = text(value);
        const lower = target.toLowerCase();

        return records.find(item =>
            item.id === target ||
            item.form_id === target ||
            item.subform_id === target ||
            item.scientific_name.toLowerCase() === lower ||
            item.canonical_name.toLowerCase() === lower ||
            item.common_names.some(name => name.toLowerCase() === lower)
        ) || null;
    }

    class FormsService extends EventTarget {
        constructor(context) {
            super();

            if (!context || typeof context !== "object") {
                throw new TypeError("A terminal context is required.");
            }

            this.context = context;
            this.destroyed = false;
            this.cache = null;
            this.cacheTimestamp = 0;
        }

        ensureAvailable() {
            if (this.destroyed) {
                throw new Error("Forms service has been destroyed.");
            }

            if (!this.context.api || typeof this.context.api.get !== "function") {
                throw new Error("Speciedex API client is unavailable.");
            }
        }

        dispatch(name, detail) {
            emit(this, name, detail);

            try {
                this.context.events?.emit?.(`forms:${name}`, detail);
            } catch (_error) {
                /* Observer failures must not interrupt form operations. */
            }

            emit(
                this.context.root,
                `speciedex:terminal-forms-${name}`,
                detail,
                { bubbles: true }
            );
        }

        async list(parameters = {}, options = {}) {
            this.ensureAvailable();

            const normalized = normalizeParameters(parameters);
            const startedAt = performance.now();

            this.dispatch("request", {
                operation: "list",
                parameters: normalized
            });

            try {
                const payload = await this.context.api.get(
                    "taxa/forms",
                    normalized,
                    options
                );

                const result = normalizeResponse(payload);
                result.parameters = normalized;
                result.duration = performance.now() - startedAt;

                this.cache = result;
                this.cacheTimestamp = Date.now();

                this.dispatch("complete", result);
                return result;
            } catch (error) {
                this.dispatch("error", {
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

            const normalizedId = text(id);

            if (!normalizedId) {
                throw new TypeError("A form or subform ID or name is required.");
            }

            try {
                const payload = await this.context.api.get(
                    `taxa/forms/${encodeURIComponent(normalizedId)}`,
                    {},
                    options
                );

                return normalizeRecord(payload, 0);
            } catch (error) {
                const match = findForm(this.cache?.records || [], normalizedId);

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async byParent(field, value, parameters = {}, options = {}) {
            const normalizedValue = text(value);

            if (!normalizedValue) {
                throw new TypeError(`A ${field.replaceAll("_", " ")} ID or name is required.`);
            }

            const result = await this.list({
                ...parameters,
                [field]: normalizedValue
            }, options);

            const idField = `${field}_id`;
            const lower = normalizedValue.toLowerCase();
            const records = result.records.filter(item =>
                item[idField] === normalizedValue ||
                text(item[field]).toLowerCase() === lower
            );

            return {
                ...result,
                [field]: normalizedValue,
                records,
                summary: summarize(records)
            };
        }

        bySpecies(value, parameters = {}, options = {}) {
            return this.byParent("parent_species", value, parameters, options);
        }

        bySubspecies(value, parameters = {}, options = {}) {
            return this.byParent("parent_subspecies", value, parameters, options);
        }

        byVariety(value, parameters = {}, options = {}) {
            return this.byParent("parent_variety", value, parameters, options);
        }

        async filtered(flag, parameters = {}, options = {}) {
            const result = await this.list({
                ...parameters,
                [flag]: true
            }, options);

            const records = result.records.filter(item =>
                flag === "synonym"
                    ? item.synonym || item.synonyms.length > 0
                    : Boolean(item[flag])
            );

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
                parent_variety: record.parent_variety,
                parent_variety_id: record.parent_variety_id,
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
                forms: result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint: "taxa/forms",
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

            emit(this, "destroy", {
                timestamp: new Date().toISOString()
            });

            return true;
        }
    }

    function initialize(context) {
        const existing = context.services?.get?.(SERVICE_NAME);

        if (existing instanceof FormsService && !existing.destroyed) {
            context.forms = existing;
            return existing;
        }

        if (
            context.forms instanceof FormsService &&
            !context.forms.destroyed
        ) {
            return context.forms;
        }

        const service = new FormsService(context);

        context.forms = service;
        context.registerService?.(SERVICE_NAME, service);
        context.registerService?.("taxa-forms", service);

        emit(document, "speciedex:terminal-forms-ready", {
            context,
            service
        });

        return service;
    }

    function requireService(context) {
        const service =
            context?.forms ||
            context?.services?.get?.(SERVICE_NAME);

        if (!(service instanceof FormsService)) {
            throw new Error("Forms service is unavailable.");
        }

        return service;
    }

    function parseCommandArguments(args = []) {
        const parameters = {};
        const positional = [];

        const flags = {
            "--form=": "form",
            "--form-id=": "form_id",
            "--subform=": "subform",
            "--subform-id=": "subform_id",
            "--taxon=": "taxon",
            "--taxon-id=": "taxon_id",
            "--species=": "species",
            "--species-id=": "species_id",
            "--subspecies=": "subspecies",
            "--subspecies-id=": "subspecies_id",
            "--variety=": "variety",
            "--variety-id=": "variety_id",
            "--parent-species=": "parent_species",
            "--parent-species-id=": "parent_species_id",
            "--parent-subspecies=": "parent_subspecies",
            "--parent-subspecies-id=": "parent_subspecies_id",
            "--parent-variety=": "parent_variety",
            "--parent-variety-id=": "parent_variety_id",
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
            "--form-epithet=": "form_epithet",
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
            "--max-occurrences=": "max_occurrences",
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
            const match = Object.entries(flags).find(
                ([flag]) => argument.startsWith(flag)
            );

            if (match) {
                parameters[match[1]] = argument.slice(match[0].length);
            } else if (!argument.startsWith("--")) {
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
            handler: async ({ args = [], context, writeJSON }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context)[method](
                        parseCommandArguments(args)
                    )
                )
        };
    }

    function parentCommand(name, aliases, description, method, label) {
        return {
            name,
            aliases,
            category: "taxonomy",
            description,
            usage: `${name} <${label}-id|${label}-name> [filters]`,
            handler: async ({ args = [], context, writeJSON }) => {
                if (!args.length) {
                    throw new Error(`A parent ${label} ID or name is required.`);
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context)[method](
                        args[0],
                        parseCommandArguments(args.slice(1))
                    )
                );
            }
        };
    }

    const commands = [
        {
            name: "forms",
            aliases: ["taxa-forms"],
            category: "taxonomy",
            description: "Search taxonomic forms.",
            usage:
                "forms [query] [limit] [--parent-species=NAME] [--parent-subspecies=NAME] [--parent-variety=NAME] [--scientific-name=NAME] [--status=STATUS] [--provider=PROVIDER] [--country=COUNTRY] [--conservation-status=STATUS] [--accepted=true|false] [--synonym=true|false] [--deprecated=true|false] [--supported=true|false] [--extinct=true|false] [--threatened=true|false] [--endemic=true|false] [--invasive=true|false] [--verified=true|false] [--active=true|false] [--min-occurrences=N] [--max-occurrences=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
            handler: async ({ args = [], context, writeJSON }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context).list(
                        parseCommandArguments(args)
                    )
                )
        },
        {
            name: "form",
            aliases: ["form-get"],
            category: "taxonomy",
            description: "Retrieve one form or subform by ID or name.",
            usage: "form <id|scientific-name|common-name>",
            handler: async ({ args = [], context, writeJSON }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error("A form or subform ID or name is required.");
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).get(id)
                );
            }
        },
        parentCommand(
            "forms-by-species",
            ["species-forms"],
            "List forms belonging to one parent species.",
            "bySpecies",
            "species"
        ),
        parentCommand(
            "forms-by-subspecies",
            ["subspecies-forms"],
            "List forms belonging to one parent subspecies.",
            "bySubspecies",
            "subspecies"
        ),
        parentCommand(
            "forms-by-variety",
            ["variety-forms"],
            "List forms belonging to one parent variety.",
            "byVariety",
            "variety"
        ),
        filteredCommand(
            "forms-accepted",
            ["accepted-forms"],
            "List accepted form records.",
            "accepted"
        ),
        filteredCommand(
            "forms-synonyms",
            ["synonym-forms"],
            "List synonym forms or records carrying synonym names.",
            "synonyms"
        ),
        filteredCommand(
            "forms-deprecated",
            ["deprecated-forms"],
            "List deprecated form records.",
            "deprecated"
        ),
        filteredCommand(
            "forms-supported",
            ["supported-forms"],
            "List supported form records.",
            "supported"
        ),
        filteredCommand(
            "forms-threatened",
            ["threatened-forms"],
            "List threatened form records.",
            "threatened"
        ),
        filteredCommand(
            "forms-extinct",
            ["extinct-forms"],
            "List extinct form records.",
            "extinct"
        ),
        filteredCommand(
            "forms-endemic",
            ["endemic-forms"],
            "List endemic form records.",
            "endemic"
        ),
        filteredCommand(
            "forms-invasive",
            ["invasive-forms"],
            "List invasive form records.",
            "invasive"
        ),
        {
            name: "form-lineage",
            category: "taxonomy",
            description: "Show normalized lineage and parent taxa for one form.",
            usage: "form-lineage <id|name>",
            handler: async ({ args = [], context, writeJSON }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error("A form or subform ID or name is required.");
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).lineage(id)
                );
            }
        },
        {
            name: "form-synonym-list",
            category: "taxonomy",
            description: "Show accepted-name and synonym information for one form.",
            usage: "form-synonym-list <id|name>",
            handler: async ({ args = [], context, writeJSON }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error("A form or subform ID or name is required.");
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).synonymList(id)
                );
            }
        },
        {
            name: "forms-summary",
            aliases: ["form-summary"],
            category: "taxonomy",
            description:
                "Summarize forms by parent species, parent subspecies, parent variety, rank, status, lineage, provider, source, geography, habitat, environment, conservation state, and occurrence count.",
            usage: "forms-summary [filters]",
            handler: async ({ args = [], context, writeJSON }) =>
                writeJSONValue(
                    writeJSON,
                    await requireService(context).summary(
                        parseCommandArguments(args)
                    )
                )
        },
        {
            name: "forms-status",
            category: "taxonomy",
            description: "Show forms service status.",
            usage: "forms-status",
            handler: ({ context, writeJSON }) =>
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
        FormsService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeStringArray: stringArray,
        normalizeTaxonomicStatus: taxonomicStatus,
        normalizeConservationStatus: conservationStatus,
        normalizeRelations,
        normalizeLineage,
        inferParts,
        findForm,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalForms = api;
    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};
    window.SpeciedexTerminalModules[MODULE_NAME] = api;

    emit(document, "speciedex:terminal-module-available", {
        name: MODULE_NAME,
        module: api
    });
})(window, document);
