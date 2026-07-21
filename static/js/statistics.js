"use strict";

/*
==============================================================================
Speciedex.org
Statistics Module
==============================================================================

Loaded by:

    /static/js/script.js

Responsibilities:

    • Load statistics.json through the shared Data module
    • Load statistics-sources.json when available
    • Populate original and expanded splash statistics
    • Support HTML partials inserted after module initialization
    • Bind explicit element IDs and generic [data-stat] elements
    • Format numeric values consistently
    • Display timestamps in America/New_York
    • Gracefully handle unavailable values
    • Dispatch statistics lifecycle events

==============================================================================
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (
        Speciedex
            .statisticsModuleLoaded
    ) {
        return;
    }

    Speciedex
        .statisticsModuleLoaded =
        true;

    /*
    ==========================================================================
    Configuration
    ==========================================================================
    */

    const DATA_FILE =
        "statistics.json";

    const SOURCES_FILE =
        "statistics-sources.json";

    const DISPLAY_TIME_ZONE =
        "America/New_York";

    const SELECTORS =
        Object.freeze({
            species:
                "#species-count",

            subspecies:
                "#subspecies-count",

            genera:
                "#genus-count",

            families:
                "#family-count",

            orders:
                "#order-count",

            classes:
                "#class-count",

            phyla:
                "#phylum-count",

            kingdoms:
                "#kingdom-count",

            records_archived:
                "#records-count",

            source_assertions:
                "#assertions-count",

            synonyms:
                "#synonyms-count",

            unresolved_conflicts:
                "#conflicts-count",

            volumes:
                "#volumes-count",

            providers:
                "#providers-count",

            enabled_providers:
                "#enabled-providers-count",

            eligible_providers:
                "#eligible-providers-count",

            last_updated:
                "#updated-date"
        });

    const ALIASES =
        Object.freeze({
            updated:
                "last_updated",

            species_count:
                "species",

            subspecies_count:
                "subspecies",

            genus:
                "genera",

            family:
                "families",

            order:
                "orders",

            class:
                "classes",

            phylum:
                "phyla",

            kingdom:
                "kingdoms",

            records:
                "records_archived",

            canonical_records:
                "records_archived",

            assertions:
                "source_assertions",

            conflicts:
                "unresolved_conflicts",

            archive_volumes:
                "volumes",

            provider_count:
                "providers",

            registered_providers:
                "providers",

            providers_total:
                "providers"
        });

    const DATE_KEYS =
        new Set([
            "last_updated",
            "updated",
            "generated_at",
            "created_at",
            "modified_at"
        ]);

    let loadingPromise =
        null;

    let cachedStatistics =
        null;

    /*
    ==========================================================================
    Resolve Elements
    ==========================================================================
    */

    function getStatisticElements() {
        const bindings =
            new Map();

        for (
            const [
                key,
                selector
            ]
            of Object.entries(
                SELECTORS
            )
        ) {
            const element =
                document
                    .querySelector(
                        selector
                    );

            if (element) {
                bindings.set(
                    element,
                    key
                );
            }
        }

        document
            .querySelectorAll(
                "[data-stat]"
            )
            .forEach(
                (
                    element
                ) => {
                    const key =
                        resolveStatisticKey(
                            element
                                .dataset
                                .stat
                        );

                    if (!key) {
                        return;
                    }

                    bindings.set(
                        element,
                        key
                    );
                }
            );

        return bindings;
    }

    function resolveStatisticKey(
        value
    ) {
        const key =
            String(
                value || ""
            )
                .trim()
                .toLowerCase()
                .replace(
                    /[\s-]+/g,
                    "_"
                );

        if (!key) {
            return "";
        }

        return (
            ALIASES[key] ||
            key
        );
    }

    /*
    ==========================================================================
    Validate Data
    ==========================================================================
    */

    function requireObject(
        data,
        label
    ) {
        if (
            Speciedex.Data &&
            typeof Speciedex.Data
                .requireObject ===
                "function"
        ) {
            Speciedex.Data
                .requireObject(
                    data,
                    label
                );

            return true;
        }

        if (
            !data ||
            typeof data !==
                "object" ||
            Array.isArray(
                data
            )
        ) {
            throw new TypeError(
                `${label} must be an object.`
            );
        }

        return true;
    }

    function validateStatisticsData(
        data
    ) {
        return requireObject(
            data,
            "Statistics data"
        );
    }

    function validateSourcesData(
        data
    ) {
        return requireObject(
            data,
            "Statistics sources data"
        );
    }

    /*
    ==========================================================================
    Initialize Statistics
    ==========================================================================
    */

    async function initializeStatistics(
        options = {}
    ) {
        const bindings =
            getStatisticElements();

        if (!bindings.size) {
            return null;
        }

        if (
            cachedStatistics &&
            !options.force
        ) {
            populateStatistics(
                bindings,
                cachedStatistics
            );

            dispatchStatisticsEvent(
                "speciedex:statistics-loaded",
                {
                    elements:
                        Array.from(
                            bindings.keys()
                        ),

                    data:
                        cachedStatistics,

                    cached:
                        true
                }
            );

            return cachedStatistics;
        }

        if (loadingPromise) {
            const data =
                await loadingPromise;

            populateStatistics(
                getStatisticElements(),
                data
            );

            return data;
        }

        dispatchStatisticsEvent(
            "speciedex:statistics-loading",
            {
                elements:
                    Array.from(
                        bindings.keys()
                    )
            }
        );

        loadingPromise =
            loadStatisticsData();

        try {
            const data =
                await loadingPromise;

            cachedStatistics =
                data;

            populateStatistics(
                getStatisticElements(),
                data
            );

            dispatchStatisticsEvent(
                "speciedex:statistics-loaded",
                {
                    elements:
                        Array.from(
                            getStatisticElements()
                                .keys()
                        ),

                    data,

                    cached:
                        false
                }
            );

            return data;
        } catch (error) {
            console.error(
                `Unable to load ${DATA_FILE}:`,
                error
            );

            setStatisticsUnavailable(
                getStatisticElements()
            );

            dispatchStatisticsEvent(
                "speciedex:statistics-error",
                {
                    elements:
                        Array.from(
                            getStatisticElements()
                                .keys()
                        ),

                    error
                }
            );

            return null;
        } finally {
            loadingPromise =
                null;
        }
    }

    /*
    ==========================================================================
    Load Data
    ==========================================================================
    */

    async function loadStatisticsData() {
        if (
            !Speciedex.Data ||
            typeof Speciedex.Data
                .fetchJSON !==
                "function"
        ) {
            throw new Error(
                "Speciedex Data module is unavailable."
            );
        }

        const statistics =
            await Speciedex.Data
                .fetchJSON(
                    DATA_FILE,
                    {
                        cache:
                            true,

                        requestCache:
                            "no-cache",

                        validate:
                            validateStatisticsData
                    }
                );

        let sources =
            null;

        try {
            sources =
                await Speciedex.Data
                    .fetchJSON(
                        SOURCES_FILE,
                        {
                            cache:
                                true,

                            requestCache:
                                "no-cache",

                            validate:
                                validateSourcesData
                        }
                    );
        } catch (error) {
            console.warn(
                `Unable to load optional ${SOURCES_FILE}:`,
                error
            );
        }

        return mergeStatistics(
            statistics,
            sources
        );
    }

    /*
    ==========================================================================
    Merge Statistics
    ==========================================================================
    */

    function mergeStatistics(
        statistics,
        sources
    ) {
        const merged = {
            ...statistics
        };

        if (
            merged
                .last_updated ===
                undefined &&
            merged
                .updated !==
                undefined
        ) {
            merged
                .last_updated =
                merged.updated;
        }

        const providerMetadata =
            extractProviderMetadata(
                sources
            );

        for (
            const [
                key,
                value
            ]
            of Object.entries(
                providerMetadata
            )
        ) {
            if (
                merged[key] ===
                    undefined &&
                value !== null
            ) {
                merged[key] =
                    value;
            }
        }

        return merged;
    }

    /*
    ==========================================================================
    Provider Metadata
    ==========================================================================
    */

    function extractProviderMetadata(
        data
    ) {
        const result = {
            providers:
                null,

            enabled_providers:
                null,

            eligible_providers:
                null
        };

        if (
            !data ||
            typeof data !==
                "object" ||
            Array.isArray(
                data
            )
        ) {
            return result;
        }

        result.providers =
            firstNumericValue(
                data,
                [
                    "provider_count",
                    "providers_total",
                    "registered_providers",
                    "providers"
                ]
            );

        result.enabled_providers =
            firstNumericValue(
                data,
                [
                    "enabled_providers",
                    "providers_enabled"
                ]
            );

        result.eligible_providers =
            firstNumericValue(
                data,
                [
                    "eligible_providers",
                    "providers_eligible"
                ]
            );

        if (
            result.providers ===
                null
        ) {
            result.providers =
                countCollection(
                    data.providers
                );
        }

        if (
            result.providers ===
                null
        ) {
            result.providers =
                countCollection(
                    data.sources
                );
        }

        if (
            result.providers ===
                null
        ) {
            result.providers =
                countCollection(
                    data.provider_statistics
                );
        }

        if (
            result.providers ===
                null
        ) {
            result.providers =
                countCollection(
                    data.provider_counts
                );
        }

        return result;
    }

    function firstNumericValue(
        data,
        keys
    ) {
        for (
            const key
            of keys
        ) {
            if (
                !Object.prototype
                    .hasOwnProperty
                    .call(
                        data,
                        key
                    )
            ) {
                continue;
            }

            const value =
                data[key];

            if (
                Array.isArray(
                    value
                ) ||
                (
                    value &&
                    typeof value ===
                        "object"
                )
            ) {
                continue;
            }

            const number =
                Number(
                    value
                );

            if (
                Number.isFinite(
                    number
                )
            ) {
                return number;
            }
        }

        return null;
    }

    function countCollection(
        value
    ) {
        if (
            Array.isArray(
                value
            )
        ) {
            return value.length;
        }

        if (
            value &&
            typeof value ===
                "object"
        ) {
            return Object.keys(
                value
            ).length;
        }

        return null;
    }

    /*
    ==========================================================================
    Populate Statistics
    ==========================================================================
    */

    function populateStatistics(
        bindings,
        data
    ) {
        if (
            !(bindings instanceof Map)
        ) {
            return;
        }

        bindings.forEach(
            (
                key,
                element
            ) => {
                const value =
                    data[key];

                if (
                    DATE_KEYS.has(
                        key
                    )
                ) {
                    setStatisticDate(
                        element,
                        value
                    );

                    return;
                }

                setStatistic(
                    element,
                    value
                );
            }
        );
    }

    /*
    ==========================================================================
    Set Numeric Statistic
    ==========================================================================
    */

    function setStatistic(
        element,
        value
    ) {
        if (!element) {
            return;
        }

        const formatted =
            formatStatisticValue(
                value
            );

        element.textContent =
            formatted;

        element.dataset
            .statStatus =
            formatted ===
                "Unavailable"
                ? "unavailable"
                : "loaded";
    }

    /*
    ==========================================================================
    Format Statistic Value
    ==========================================================================
    */

    function formatStatisticValue(
        value
    ) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return "Unavailable";
        }

        if (
            typeof value ===
                "boolean"
        ) {
            return value
                ? "Yes"
                : "No";
        }

        if (
            typeof value ===
                "number"
        ) {
            return fallbackFormatNumber(
                value
            );
        }

        if (
            typeof value ===
                "string"
        ) {
            const trimmed =
                value.trim();

            if (!trimmed) {
                return "Unavailable";
            }

            const numeric =
                Number(
                    trimmed
                );

            if (
                Number.isFinite(
                    numeric
                )
            ) {
                return fallbackFormatNumber(
                    numeric
                );
            }

            return trimmed;
        }

        if (
            Array.isArray(
                value
            )
        ) {
            return fallbackFormatNumber(
                value.length
            );
        }

        if (
            typeof value ===
                "object"
        ) {
            return fallbackFormatNumber(
                Object.keys(
                    value
                ).length
            );
        }

        return String(
            value
        );
    }

    /*
    ==========================================================================
    Set Date Statistic
    ==========================================================================
    */

    function setStatisticDate(
        element,
        value
    ) {
        if (!element) {
            return;
        }

        const formatted =
            formatEasternDate(
                value
            );

        element.textContent =
            formatted;

        element.dataset
            .statStatus =
            formatted ===
                "Unavailable"
                ? "unavailable"
                : "loaded";
    }

    /*
    ==========================================================================
    Eastern Time Formatting
    ==========================================================================
    */

    function formatEasternDate(
        value
    ) {
        if (!value) {
            return "Unavailable";
        }

        const date =
            value instanceof Date
                ? value
                : new Date(
                    value
                );

        if (
            Number.isNaN(
                date.getTime()
            )
        ) {
            return String(
                value
            );
        }

        try {
            return new Intl
                .DateTimeFormat(
                    "en-US",
                    {
                        timeZone:
                            DISPLAY_TIME_ZONE,

                        year:
                            "numeric",

                        month:
                            "short",

                        day:
                            "2-digit",

                        hour:
                            "numeric",

                        minute:
                            "2-digit",

                        second:
                            "2-digit",

                        timeZoneName:
                            "short"
                    }
                )
                .format(
                    date
                );
        } catch (error) {
            console.warn(
                "Unable to format timestamp "
                + `using ${DISPLAY_TIME_ZONE}:`,
                error
            );

            return fallbackFormatDate(
                value
            );
        }
    }

    /*
    ==========================================================================
    Unavailable State
    ==========================================================================
    */

    function setStatisticUnavailable(
        element
    ) {
        if (!element) {
            return;
        }

        element.textContent =
            "Unavailable";

        element.dataset
            .statStatus =
            "unavailable";
    }

    function setStatisticsUnavailable(
        bindings
    ) {
        if (
            bindings instanceof Map
        ) {
            bindings.forEach(
                (
                    key,
                    element
                ) => {
                    if (!element) {
                        return;
                    }

                    element.textContent =
                        "Unavailable";

                    element.dataset
                        .statStatus =
                        "error";
                }
            );

            return;
        }

        if (
            Array.isArray(
                bindings
            )
        ) {
            bindings.forEach(
                (
                    element
                ) => {
                    if (!element) {
                        return;
                    }

                    element.textContent =
                        "Unavailable";

                    element.dataset
                        .statStatus =
                        "error";
                }
            );
        }
    }

    /*
    ==========================================================================
    Refresh Statistics
    ==========================================================================
    */

    async function refreshStatistics() {
        if (
            Speciedex.Data &&
            typeof Speciedex.Data
                .clearCache ===
                "function"
        ) {
            Speciedex.Data
                .clearCache(
                    DATA_FILE
                );

            Speciedex.Data
                .clearCache(
                    SOURCES_FILE
                );
        }

        cachedStatistics =
            null;

        return initializeStatistics({
            force:
                true
        });
    }

    /*
    ==========================================================================
    Partial-Insertion Support
    ==========================================================================
    */

    function bindPartialEvents() {
        const eventNames = [
            "speciedex:includes-loaded",
            "speciedex:include-loaded",
            "speciedex:partials-loaded",
            "speciedex:partial-loaded",
            "speciedex:header-loaded",
            "speciedex:splash-loaded"
        ];

        eventNames.forEach(
            (
                eventName
            ) => {
                document.addEventListener(
                    eventName,
                    () => {
                        initializeStatistics()
                            .catch(
                                (
                                    error
                                ) => {
                                    console.error(
                                        "Unable to initialize "
                                        + "statistics after "
                                        + `${eventName}:`,
                                        error
                                    );
                                }
                            );
                    }
                );
            }
        );
    }

    /*
    ==========================================================================
    DOM Observation
    ==========================================================================
    */

    function observeStatisticElements() {
        if (
            typeof MutationObserver ===
                "undefined"
        ) {
            return;
        }

        const observer =
            new MutationObserver(
                (
                    mutations
                ) => {
                    let foundStatistics =
                        false;

                    for (
                        const mutation
                        of mutations
                    ) {
                        for (
                            const node
                            of mutation
                                .addedNodes
                        ) {
                            if (
                                !(
                                    node instanceof
                                    Element
                                )
                            ) {
                                continue;
                            }

                            if (
                                node.matches(
                                    "[data-stat]"
                                ) ||
                                node.querySelector(
                                    "[data-stat]"
                                )
                            ) {
                                foundStatistics =
                                    true;

                                break;
                            }
                        }

                        if (
                            foundStatistics
                        ) {
                            break;
                        }
                    }

                    if (
                        !foundStatistics
                    ) {
                        return;
                    }

                    initializeStatistics()
                        .catch(
                            (
                                error
                            ) => {
                                console.error(
                                    "Unable to initialize "
                                    + "statistics after "
                                    + "DOM insertion:",
                                    error
                                );
                            }
                        );
                }
            );

        observer.observe(
            document.documentElement,
            {
                childList:
                    true,

                subtree:
                    true
            }
        );

        Speciedex
            .statisticsObserver =
            observer;
    }

    /*
    ==========================================================================
    Fallback Number Formatting
    ==========================================================================
    */

    function fallbackFormatNumber(
        value
    ) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return "Unavailable";
        }

        const number =
            Number(
                value
            );

        if (
            !Number.isFinite(
                number
            )
        ) {
            return String(
                value
            );
        }

        if (
            Speciedex.Data &&
            typeof Speciedex.Data
                .formatNumber ===
                "function"
        ) {
            try {
                return Speciedex.Data
                    .formatNumber(
                        number
                    );
            } catch (error) {
                console.warn(
                    "Shared number formatter failed:",
                    error
                );
            }
        }

        return number
            .toLocaleString(
                "en-US"
            );
    }

    /*
    ==========================================================================
    Fallback Date Formatting
    ==========================================================================
    */

    function fallbackFormatDate(
        value
    ) {
        if (!value) {
            return "Unavailable";
        }

        const date =
            new Date(
                value
            );

        if (
            Number.isNaN(
                date.getTime()
            )
        ) {
            return String(
                value
            );
        }

        return date
            .toLocaleString(
                "en-US",
                {
                    year:
                        "numeric",

                    month:
                        "short",

                    day:
                        "2-digit",

                    hour:
                        "numeric",

                    minute:
                        "2-digit",

                    second:
                        "2-digit",

                    timeZone:
                        DISPLAY_TIME_ZONE,

                    timeZoneName:
                        "short"
                }
            );
    }

    /*
    ==========================================================================
    Lifecycle Events
    ==========================================================================
    */

    function dispatchStatisticsEvent(
        name,
        detail = {}
    ) {
        document.dispatchEvent(
            new CustomEvent(
                name,
                {
                    detail
                }
            )
        );
    }

    /*
    ==========================================================================
    Initial Binding
    ==========================================================================
    */

    function bindInitialStatistics() {
        const initialize =
            () => {
                initializeStatistics()
                    .catch(
                        (
                            error
                        ) => {
                            console.error(
                                "Unable to initialize "
                                + "Speciedex statistics:",
                                error
                            );
                        }
                    );
            };

        if (
            document.readyState ===
                "loading"
        ) {
            document.addEventListener(
                "DOMContentLoaded",
                initialize,
                {
                    once:
                        true
                }
            );
        } else {
            initialize();
        }
    }

    /*
    ==========================================================================
    Public API
    ==========================================================================
    */

    Speciedex.initializeStatistics =
        initializeStatistics;

    Speciedex.refreshStatistics =
        refreshStatistics;

    Speciedex.setStatistic =
        setStatistic;

    Speciedex.setStatisticDate =
        setStatisticDate;

    Speciedex.formatStatisticDate =
        formatEasternDate;

    Speciedex.getStatisticElements =
        getStatisticElements;

    Speciedex.getStatistics =
        () => {
            if (
                !cachedStatistics
            ) {
                return null;
            }

            return {
                ...cachedStatistics
            };
        };

    /*
    ==========================================================================
    Module Startup
    ==========================================================================
    */

    bindPartialEvents();

    observeStatisticElements();

    bindInitialStatistics();
})();