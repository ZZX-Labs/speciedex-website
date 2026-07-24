/*
========================================================================
Speciedex.org
Terminal Phyla Module
========================================================================

Taxonomic phylum and subphylum search service for SpeciedexTerminal.

Provides validated API requests, normalized phylum records, parent-kingdom
resolution, child-class helpers, lineage and synonym handling, summaries,
caching, lifecycle events, resilient service registration, and terminal
command integration.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Phyla";
    const VERSION = "2.0.0";
    const SERVICE_NAME = "phyla";
    const DEFAULT_LIMIT = 50;
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

    function clampInteger(value, fallback, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
        const parsed = Number.parseInt(value, 10);

        return Number.isFinite(parsed)
            ? Math.min(maximum, Math.max(minimum, parsed))
            : fallback;
    }

    function normalizeNumber(value, fallback = 0) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function normalizeBoolean(value, fallback = null) {
        if (typeof value === "boolean") {
            return value;
        }

        const normalized = normalizeText(value).toLowerCase();

        if (value === 1 || normalized === "1" || normalized === "true") {
            return true;
        }

        if (value === 0 || normalized === "0" || normalized === "false") {
            return false;
        }

        return fallback;
    }

    function normalizeDate(value) {
        const normalized = normalizeText(value);

        if (!normalized) {
            return "";
        }

        const timestamp = Date.parse(normalized);

        if (!Number.isFinite(timestamp)) {
            throw new TypeError(`Invalid date value: ${value}`);
        }

        return new Date(timestamp).toISOString();
    }

    function normalizeStringArray(value) {
        const values = Array.isArray(value)
            ? value
            : normalizeText(value).split(/[;,|]+/);

        return [...new Set(
            values.map(normalizeText).filter(Boolean)
        )];
    }

    function normalizeTaxonomicStatus(value) {
        const normalized = normalizeKey(value || "unknown");

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

    function normalizeSort(value) {
        const normalized = normalizeKey(value || "scientific_name");
        const allowed = new Set([
            "scientific_name",
            "canonical_name",
            "name",
            "rank",
            "status",
            "kingdom",
            "superphylum",
            "phylum",
            "subphylum",
            "class_count",
            "order_count",
            "family_count",
            "genus_count",
            "species_count",
            "provider",
            "updated_at",
            "created_at",
            "id"
        ]);

        if (!allowed.has(normalized)) {
            throw new TypeError(`Unsupported phyla sort field: ${value}`);
        }

        return normalized;
    }

    function normalizeDirection(value) {
        const normalized = normalizeText(value || "asc").toLowerCase();

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
            q: normalizeText(source.q ?? source.query ?? ""),
            limit: clampInteger(source.limit, DEFAULT_LIMIT, 1, MAX_LIMIT),
            offset: clampInteger(source.offset, 0),
            sort: normalizeSort(source.sort),
            direction: normalizeDirection(
                source.direction ?? source.order
            )
        };

        const textFields = [
            "phylum",
            "phylum_id",
            "superphylum",
            "superphylum_id",
            "subphylum",
            "subphylum_id",
            "taxon",
            "taxon_id",
            "scientific_name",
            "canonical_name",
            "name",
            "authorship",
            "rank",
            "status",
            "accepted_name",
            "accepted_id",
            "domain",
            "domain_id",
            "kingdom",
            "kingdom_id",
            "class",
            "class_id",
            "provider",
            "source",
            "license",
            "country",
            "region",
            "continent",
            "category",
            "type"
        ];

        for (const field of textFields) {
            if (
                source[field] !== undefined &&
                source[field] !== null &&
                source[field] !== ""
            ) {
                normalized[field] = normalizeText(source[field]);
            }
        }

        const booleanFields = [
            "accepted",
            "synonym",
            "deprecated",
            "supported",
            "verified",
            "active",
            "root",
            "leaf"
        ];

        for (const field of booleanFields) {
            if (
                source[field] !== undefined &&
                source[field] !== null &&
                source[field] !== ""
            ) {
                const parsed = normalizeBoolean(source[field]);

                if (parsed === null) {
                    throw new TypeError(
                        `Invalid ${field} value: ${source[field]}`
                    );
                }

                normalized[field] = parsed;
            }
        }

        const ranges = [
            ["min_classes", "max_classes", source.min_classes ?? source.minClasses, source.max_classes ?? source.maxClasses, "class count"],
            ["min_orders", "max_orders", source.min_orders ?? source.minOrders, source.max_orders ?? source.maxOrders, "order count"],
            ["min_families", "max_families", source.min_families ?? source.minFamilies, source.max_families ?? source.maxFamilies, "family count"],
            ["min_genera", "max_genera", source.min_genera ?? source.minGenera, source.max_genera ?? source.maxGenera, "genus count"],
            ["min_species", "max_species", source.min_species ?? source.minSpecies, source.max_species ?? source.maxSpecies, "species count"]
        ];

        for (const [minimumKey, maximumKey, minimumValue, maximumValue, label] of ranges) {
            if (
                minimumValue !== undefined &&
                minimumValue !== null &&
                minimumValue !== ""
            ) {
                normalized[minimumKey] = clampInteger(minimumValue, 0);
            }

            if (
                maximumValue !== undefined &&
                maximumValue !== null &&
                maximumValue !== ""
            ) {
                normalized[maximumKey] = clampInteger(
                    maximumValue,
                    Number.MAX_SAFE_INTEGER
                );
            }

            if (
                normalized[minimumKey] !== undefined &&
                normalized[maximumKey] !== undefined &&
                normalized[minimumKey] > normalized[maximumKey]
            ) {
                throw new RangeError(
                    `Minimum ${label} must not exceed maximum ${label}.`
                );
            }
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
            Date.parse(normalized.from) > Date.parse(normalized.to)
        ) {
            throw new RangeError(
                "Phyla start date must not be later than the end date."
            );
        }

        return normalized;
    }

    function normalizeRelations(value, fallbackRank) {
        const values = Array.isArray(value)
            ? value
            : normalizeStringArray(value);

        return values.map((item, index) => {
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
                        fallbackRank
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
                        item.status ?? "accepted"
                    ),
                    source: normalizeText(item.source ?? ""),
                    index
                };
            }

            return {
                id: "",
                rank: fallbackRank,
                scientific_name: normalizeText(item),
                authorship: "",
                status: "accepted",
                source: "",
                index
            };
        }).filter(item => item.scientific_name);
    }

    function normalizeLineage(record) {
        if (Array.isArray(record.lineage)) {
            return normalizeRelations(record.lineage, "phylum");
        }

        const lineage = [];
        const levels = [
            ["domain", record.domain],
            ["kingdom", record.kingdom],
            ["superphylum", record.superphylum],
            [
                normalizeKey(record.rank ?? "phylum") || "phylum",
                record.scientific_name ??
                record.scientificName ??
                record.name
            ]
        ];

        for (const [rank, value] of levels) {
            const name = normalizeText(value);

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
            record = {
                scientific_name: normalizeText(record)
            };
        }

        const scientificName = normalizeText(
            record.scientific_name ??
            record.scientificName ??
            record.name ??
            record.canonical_name ??
            record.canonicalName ??
            record.phylum ??
            ""
        );

        const rank = normalizeKey(
            record.rank ??
            record.taxon_rank ??
            record.taxonRank ??
            (
                record.subphylum
                    ? "subphylum"
                    : (
                        record.superphylum
                            ? "superphylum"
                            : "phylum"
                    )
            )
        ) || "phylum";

        const status = normalizeTaxonomicStatus(
            record.status ??
            record.taxonomic_status ??
            record.taxonomicStatus ??
            record.acceptance_status ??
            record.acceptanceStatus
        );

        const classes = normalizeRelations(
            record.classes ??
            record.child_classes ??
            record.childClasses,
            "class"
        );

        const subphyla = normalizeRelations(
            record.subphyla ??
            record.child_subphyla ??
            record.childSubphyla,
            "subphylum"
        );

        const classCount = normalizeNumber(
            record.class_count ??
            record.classCount ??
            record.classes_count ??
            record.classesCount,
            classes.length
        );

        const parentPhylum = normalizeText(
            record.parent_phylum ??
            record.parentPhylum ??
            record.parent ??
            ""
        );

        return {
            ...record,
            index: record.index ?? index,
            id: normalizeText(
                record.id ??
                record.phylum_id ??
                record.phylumId ??
                record.superphylum_id ??
                record.superphylumId ??
                record.subphylum_id ??
                record.subphylumId ??
                record.taxon_id ??
                record.taxonId ??
                record.uuid ??
                `phylum-${index + 1}`
            ),
            phylum_id: normalizeText(
                record.phylum_id ??
                record.phylumId ??
                record.taxon_id ??
                record.taxonId ??
                record.id ??
                ""
            ),
            superphylum_id: normalizeText(
                record.superphylum_id ??
                record.superphylumId ??
                ""
            ),
            subphylum_id: normalizeText(
                record.subphylum_id ??
                record.subphylumId ??
                ""
            ),
            scientific_name: scientificName,
            canonical_name: normalizeText(
                record.canonical_name ??
                record.canonicalName ??
                record.canonical ??
                scientificName
            ),
            name: normalizeText(record.name ?? scientificName),
            authorship: normalizeText(
                record.authorship ??
                record.scientific_name_authorship ??
                record.scientificNameAuthorship ??
                ""
            ),
            rank,
            status,
            accepted:
                record.accepted === true ||
                ["accepted", "valid"].includes(status),
            synonym:
                record.synonym === true ||
                record.is_synonym === true ||
                record.isSynonym === true ||
                status === "synonym",
            deprecated:
                record.deprecated === true ||
                status === "deprecated",
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
            active:
                record.active !== false &&
                record.deleted !== true &&
                !["inactive", "deleted", "retired"].includes(status),
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
            domain_id: normalizeText(
                record.domain_id ??
                record.domainId ??
                ""
            ),
            kingdom: normalizeText(record.kingdom ?? ""),
            kingdom_id: normalizeText(
                record.kingdom_id ??
                record.kingdomId ??
                ""
            ),
            superphylum: normalizeText(
                record.superphylum ??
                (rank === "superphylum" ? scientificName : "")
            ),
            phylum: normalizeText(
                record.phylum ??
                (rank === "phylum" ? scientificName : "")
            ),
            subphylum: normalizeText(
                record.subphylum ??
                (rank === "subphylum" ? scientificName : "")
            ),
            parent_phylum: parentPhylum,
            parent_phylum_id: normalizeText(
                record.parent_phylum_id ??
                record.parentPhylumId ??
                record.parent_id ??
                record.parentId ??
                ""
            ),
            root:
                record.root === true ||
                record.is_root === true ||
                record.isRoot === true ||
                !parentPhylum,
            leaf:
                record.leaf === true ||
                record.is_leaf === true ||
                record.isLeaf === true ||
                (
                    classCount === 0 &&
                    subphyla.length === 0
                ),
            subphyla,
            classes,
            class_count: classCount,
            order_count: normalizeNumber(
                record.order_count ??
                record.orderCount
            ),
            family_count: normalizeNumber(
                record.family_count ??
                record.familyCount
            ),
            genus_count: normalizeNumber(
                record.genus_count ??
                record.genusCount
            ),
            species_count: normalizeNumber(
                record.species_count ??
                record.speciesCount
            ),
            synonyms: normalizeRelations(
                record.synonyms ??
                record.synonym_names ??
                record.synonymNames,
                "phylum"
            ),
            lineage: normalizeLineage({
                ...record,
                scientific_name: scientificName,
                rank
            }),
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

    function increment(map, value) {
        const normalized = normalizeText(value) || "unknown";

        map.set(
            normalized,
            (map.get(normalized) || 0) + 1
        );
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
                "ranks",
                "statuses",
                "domains",
                "kingdoms",
                "superphyla",
                "phyla",
                "subphyla",
                "classes",
                "providers",
                "sources",
                "countries",
                "regions",
                "continents",
                "categories",
                "types"
            ].map(name => [name, new Map()])
        );

        let classCount = 0;
        let orderCount = 0;
        let familyCount = 0;
        let genusCount = 0;
        let speciesCount = 0;
        let synonymCount = 0;

        for (const item of values) {
            increment(maps.ranks, item.rank);
            increment(maps.statuses, item.status);
            increment(maps.domains, item.domain);
            increment(maps.kingdoms, item.kingdom);
            increment(maps.superphyla, item.superphylum);
            increment(maps.phyla, item.phylum);
            increment(maps.subphyla, item.subphylum);
            increment(maps.providers, item.provider);
            increment(maps.sources, item.source);
            increment(maps.categories, item.category);
            increment(maps.types, item.type);

            item.classes.forEach(
                value => increment(
                    maps.classes,
                    value.scientific_name
                )
            );

            item.countries.forEach(
                value => increment(maps.countries, value)
            );

            item.regions.forEach(
                value => increment(maps.regions, value)
            );

            item.continents.forEach(
                value => increment(maps.continents, value)
            );

            classCount += item.class_count;
            orderCount += item.order_count;
            familyCount += item.family_count;
            genusCount += item.genus_count;
            speciesCount += item.species_count;
            synonymCount += item.synonyms.length;
        }

        return {
            total: values.length,
            accepted:
                values.filter(item => item.accepted).length,
            synonyms:
                values.filter(item => item.synonym).length,
            synonymNames: synonymCount,
            deprecated:
                values.filter(item => item.deprecated).length,
            supported:
                values.filter(item => item.supported).length,
            verified:
                values.filter(item => item.verified).length,
            active:
                values.filter(item => item.active).length,
            roots:
                values.filter(item => item.root).length,
            leaves:
                values.filter(item => item.leaf).length,
            classes: classCount,
            orders: orderCount,
            families: familyCount,
            genera: genusCount,
            species: speciesCount,
            ranks: sortedObject(maps.ranks),
            statuses: sortedObject(maps.statuses),
            domains: sortedObject(maps.domains),
            kingdoms: sortedObject(maps.kingdoms),
            superphyla: sortedObject(maps.superphyla),
            phyla: sortedObject(maps.phyla),
            subphyla: sortedObject(maps.subphyla),
            childClasses: sortedObject(maps.classes),
            providers: sortedObject(maps.providers),
            sources: sortedObject(maps.sources),
            countries: sortedObject(maps.countries),
            regions: sortedObject(maps.regions),
            continents: sortedObject(maps.continents),
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
                                        Array.isArray(payload.phyla)
                                            ? payload.phyla
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
            summary:
                payload?.summary &&
                typeof payload.summary === "object"
                    ? {
                        ...summarize(records),
                        ...payload.summary
                    }
                    : summarize(records),
            next:
                payload?.next ??
                payload?.nextPage ??
                null,
            previous:
                payload?.previous ??
                payload?.previousPage ??
                null,
            raw: payload
        };
    }

    function findPhylum(records, value) {
        const target = normalizeText(value);
        const lower = target.toLowerCase();

        return records.find(item =>
            item.id === target ||
            item.phylum_id === target ||
            item.superphylum_id === target ||
            item.subphylum_id === target ||
            item.scientific_name.toLowerCase() === lower ||
            item.canonical_name.toLowerCase() === lower ||
            item.name.toLowerCase() === lower
        ) || null;
    }

    class PhylaService extends EventTarget {
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
                    "Phyla service has been destroyed."
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
                    `phyla:${name}`,
                    detail
                );
            } catch (_error) {
                /*
                Observer failures must not interrupt phylum operations.
                */
            }

            dispatch(
                this.context.root,
                `speciedex:terminal-phyla-${name}`,
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
                    "taxa/phyla",
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
                    "A phylum-level taxon ID or name is required."
                );
            }

            try {
                const payload = await this.context.api.get(
                    `taxa/phyla/${encodeURIComponent(normalizedId)}`,
                    {},
                    options
                );

                return normalizeRecord(payload, 0);
            } catch (error) {
                const match = findPhylum(
                    this.cache?.records || [],
                    normalizedId
                );

                if (match) {
                    return match;
                }

                throw error;
            }
        }

        async byKingdom(kingdom, parameters = {}, options = {}) {
            const normalizedKingdom = normalizeText(kingdom);

            if (!normalizedKingdom) {
                throw new TypeError(
                    "A kingdom ID or name is required."
                );
            }

            const result = await this.list({
                ...parameters,
                kingdom: normalizedKingdom
            }, options);

            const lower = normalizedKingdom.toLowerCase();

            const records = result.records.filter(item =>
                item.kingdom_id === normalizedKingdom ||
                item.kingdom.toLowerCase() === lower
            );

            return {
                ...result,
                kingdom: normalizedKingdom,
                records,
                summary: summarize(records)
            };
        }

        async children(id, options = {}) {
            const record = await this.get(id, options);

            return {
                id: record.id,
                scientific_name: record.scientific_name,
                rank: record.rank,
                subphyla: record.subphyla,
                classes: record.classes,
                class_count: record.class_count,
                order_count: record.order_count,
                family_count: record.family_count,
                genus_count: record.genus_count,
                species_count: record.species_count
            };
        }

        async lineage(id, options = {}) {
            const record = await this.get(id, options);

            return {
                id: record.id,
                scientific_name: record.scientific_name,
                rank: record.rank,
                domain: record.domain,
                domain_id: record.domain_id,
                kingdom: record.kingdom,
                kingdom_id: record.kingdom_id,
                superphylum: record.superphylum,
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
            return this.filtered(
                "accepted",
                parameters,
                options
            );
        }

        synonyms(parameters = {}, options = {}) {
            return this.filtered(
                "synonym",
                parameters,
                options
            );
        }

        deprecated(parameters = {}, options = {}) {
            return this.filtered(
                "deprecated",
                parameters,
                options
            );
        }

        supported(parameters = {}, options = {}) {
            return this.filtered(
                "supported",
                parameters,
                options
            );
        }

        async summary(parameters = {}, options = {}) {
            const result = await this.list({
                ...parameters,
                limit: parameters.limit ?? MAX_LIMIT
            }, options);

            return {
                parameters: result.parameters,
                summary: summarize(result.records),
                phyla: result.records
            };
        }

        status() {
            return {
                version: VERSION,
                endpoint: "taxa/phyla",
                service: SERVICE_NAME,
                available: Boolean(
                    this.context.api &&
                    typeof this.context.api.get === "function"
                ),
                cached: Boolean(this.cache),
                cacheAge:
                    this.cacheTimestamp
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
            existing instanceof PhylaService &&
            !existing.destroyed
        ) {
            context.phyla = existing;
            return existing;
        }

        if (
            context.phyla instanceof PhylaService &&
            !context.phyla.destroyed
        ) {
            return context.phyla;
        }

        const service = new PhylaService(context);

        context.phyla = service;

        context.registerService?.(
            SERVICE_NAME,
            service
        );

        context.registerService?.(
            "taxa-phyla",
            service
        );

        dispatch(
            document,
            "speciedex:terminal-phyla-ready",
            {
                context,
                service
            }
        );

        return service;
    }

    function requireService(context) {
        const service =
            context?.phyla ||
            context?.services?.get?.(SERVICE_NAME);

        if (!(service instanceof PhylaService)) {
            throw new Error(
                "Phyla service is unavailable."
            );
        }

        return service;
    }

    function parseCommandArguments(args = []) {
        const parameters = {};
        const positional = [];

        const flags = {
            "--phylum=": "phylum",
            "--phylum-id=": "phylum_id",
            "--superphylum=": "superphylum",
            "--superphylum-id=": "superphylum_id",
            "--subphylum=": "subphylum",
            "--subphylum-id=": "subphylum_id",
            "--taxon=": "taxon",
            "--taxon-id=": "taxon_id",
            "--scientific-name=": "scientific_name",
            "--canonical-name=": "canonical_name",
            "--name=": "name",
            "--authorship=": "authorship",
            "--rank=": "rank",
            "--status=": "status",
            "--accepted-name=": "accepted_name",
            "--accepted-id=": "accepted_id",
            "--domain=": "domain",
            "--domain-id=": "domain_id",
            "--kingdom=": "kingdom",
            "--kingdom-id=": "kingdom_id",
            "--class=": "class",
            "--class-id=": "class_id",
            "--provider=": "provider",
            "--source=": "source",
            "--license=": "license",
            "--country=": "country",
            "--region=": "region",
            "--continent=": "continent",
            "--category=": "category",
            "--type=": "type",
            "--from=": "from",
            "--to=": "to",
            "--sort=": "sort",
            "--direction=": "direction",
            "--limit=": "limit",
            "--offset=": "offset",
            "--min-classes=": "min_classes",
            "--max-classes=": "max_classes",
            "--min-orders=": "min_orders",
            "--max-orders=": "max_orders",
            "--min-families=": "min_families",
            "--max-families=": "max_families",
            "--min-genera=": "min_genera",
            "--max-genera=": "max_genera",
            "--min-species=": "min_species",
            "--max-species=": "max_species",
            "--accepted=": "accepted",
            "--synonym=": "synonym",
            "--deprecated=": "deprecated",
            "--supported=": "supported",
            "--verified=": "verified",
            "--active=": "active",
            "--root=": "root",
            "--leaf=": "leaf"
        };

        for (const argument of args) {
            const match = Object.entries(flags).find(
                ([flag]) => argument.startsWith(flag)
            );

            if (match) {
                parameters[match[1]] =
                    argument.slice(match[0].length);
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
            name: "phyla",
            aliases: [
                "taxa-phyla"
            ],
            category: "taxonomy",
            description:
                "Search taxonomic phyla.",
            usage:
                "phyla [query] [limit] [--phylum=NAME] [--superphylum=NAME] [--subphylum=NAME] [--kingdom=NAME] [--class=NAME] [--rank=RANK] [--status=STATUS] [--provider=PROVIDER] [--accepted=true|false] [--synonym=true|false] [--deprecated=true|false] [--supported=true|false] [--verified=true|false] [--active=true|false] [--root=true|false] [--leaf=true|false] [--min-classes=N] [--max-classes=N] [--min-orders=N] [--max-orders=N] [--min-families=N] [--max-families=N] [--min-genera=N] [--max-genera=N] [--min-species=N] [--max-species=N] [--from=DATE] [--to=DATE] [--sort=FIELD] [--direction=asc|desc] [--offset=N]",
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
            name: "phylum",
            aliases: [
                "phylum-get"
            ],
            category: "taxonomy",
            description:
                "Retrieve one phylum-level taxon by ID or name.",
            usage:
                "phylum <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A phylum-level taxon ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).get(id)
                );
            }
        },
        {
            name: "phyla-by-kingdom",
            aliases: [
                "kingdom-phyla"
            ],
            category: "taxonomy",
            description:
                "List phyla belonging to one kingdom.",
            usage:
                "phyla-by-kingdom <kingdom-id|kingdom-name> [filters]",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                if (!args.length) {
                    throw new Error(
                        "A kingdom ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).byKingdom(
                        args[0],
                        parseCommandArguments(args.slice(1))
                    )
                );
            }
        },
        {
            name: "phylum-children",
            category: "taxonomy",
            description:
                "Show child subphyla and classes for one phylum.",
            usage:
                "phylum-children <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A phylum-level taxon ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).children(id)
                );
            }
        },
        {
            name: "phylum-lineage",
            category: "taxonomy",
            description:
                "Show normalized lineage for one phylum-level taxon.",
            usage:
                "phylum-lineage <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A phylum-level taxon ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).lineage(id)
                );
            }
        },
        {
            name: "phylum-synonym-list",
            category: "taxonomy",
            description:
                "Show accepted-name and synonym information for one phylum.",
            usage:
                "phylum-synonym-list <id|name>",
            handler: async ({
                args = [],
                context,
                writeJSON
            }) => {
                const id = args.join(" ").trim();

                if (!id) {
                    throw new Error(
                        "A phylum-level taxon ID or name is required."
                    );
                }

                return writeJSONValue(
                    writeJSON,
                    await requireService(context).synonymList(id)
                );
            }
        },
        filteredCommand(
            "phyla-accepted",
            ["accepted-phyla"],
            "List accepted phylum records.",
            "accepted"
        ),
        filteredCommand(
            "phyla-synonyms",
            ["synonym-phyla"],
            "List synonym phyla or records carrying synonym names.",
            "synonyms"
        ),
        filteredCommand(
            "phyla-deprecated",
            ["deprecated-phyla"],
            "List deprecated phylum records.",
            "deprecated"
        ),
        filteredCommand(
            "phyla-supported",
            ["supported-phyla"],
            "List supported phylum records.",
            "supported"
        ),
        {
            name: "phyla-summary",
            aliases: [
                "phylum-summary"
            ],
            category: "taxonomy",
            description:
                "Summarize phyla by rank, status, domain, kingdom, superphylum, phylum, subphylum, child class, provider, source, geography, and descendant counts.",
            usage:
                "phyla-summary [filters]",
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
            name: "phyla-status",
            category: "taxonomy",
            description:
                "Show phyla service status.",
            usage:
                "phyla-status",
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
        PhylaService,
        normalizeParameters,
        normalizeRecord,
        normalizeResponse,
        normalizeStringArray,
        normalizeTaxonomicStatus,
        normalizeRelations,
        normalizeLineage,
        findPhylum,
        summarize,
        parseCommandArguments,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalPhyla = api;

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
