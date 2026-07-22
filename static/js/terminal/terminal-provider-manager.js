/*
========================================================================
Speciedex.org
Terminal Provider Manager
========================================================================

Provider configuration and lifecycle manager for SpeciedexTerminal.

Provides:

    • provider registration and removal
    • enabled and disabled states
    • eligibility controls
    • priority ordering
    • endpoint and documentation metadata
    • schedule and refresh configuration
    • authentication metadata storage
    • validation
    • persistence
    • import and export
    • provider cloning
    • bulk operations
    • integration with provider health
    • library synchronization
    • runtime events
    • terminal commands
    • clean teardown

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "ProviderManager";

    const VERSION =
        "2.0.0";

    const STORAGE_PREFIX =
        "speciedex-terminal:providers:";

    const DEFAULT_OPTIONS =
        Object.freeze({
            persist:
                true,

            autoSyncLibrary:
                true,

            validateURLs:
                true,

            defaultEnabled:
                true,

            defaultEligible:
                true,

            defaultPriority:
                100,

            defaultRefreshInterval:
                24 * 60 * 60 * 1000,

            historyLimit:
                1000,

            allowDuplicateEndpoints:
                false,

            emitNotifications:
                true
        });

    const AUTH_TYPES =
        Object.freeze([
            "none",
            "api-key",
            "bearer",
            "basic",
            "oauth2",
            "custom"
        ]);

    const PROVIDER_TYPES =
        Object.freeze([
            "taxonomy",
            "occurrence",
            "genetics",
            "conservation",
            "geospatial",
            "media",
            "archive",
            "hybrid",
            "unknown"
        ]);

    /*
    ==========================================================================
    Utilities
    ==========================================================================
    */

    function normalizeProviderID(value) {
        const id =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase()
                .replace(/[^a-z0-9:_-]+/g, "-")
                .replace(/^-+|-+$/g, "");

        if (!id) {
            throw new Error(
                "Provider ID is required."
            );
        }

        return id;
    }

    function normalizeText(value) {
        return String(
            value ?? ""
        ).trim();
    }

    function normalizeType(value) {
        const type =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase();

        return PROVIDER_TYPES.includes(
            type
        )
            ? type
            : "unknown";
    }

    function normalizeAuthType(value) {
        const type =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase();

        return AUTH_TYPES.includes(
            type
        )
            ? type
            : "none";
    }

    function parseBoolean(
        value,
        fallback = false
    ) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return fallback;
        }

        return ![
            "false",
            "0",
            "no",
            "off"
        ].includes(
            String(value)
                .trim()
                .toLowerCase()
        );
    }

    function parseNumber(
        value,
        fallback = 0
    ) {
        const parsed =
            Number(value);

        return Number.isFinite(
            parsed
        )
            ? parsed
            : fallback;
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

    function safeStorage() {
        try {
            const key =
                "__speciedex_provider_manager_probe__";

            window.localStorage.setItem(
                key,
                key
            );

            window.localStorage.removeItem(
                key
            );

            return window.localStorage;
        } catch (error) {
            return null;
        }
    }

    function normalizeURL(value) {
        const text =
            normalizeText(
                value
            );

        if (!text) {
            return "";
        }

        try {
            return new URL(
                text,
                window.location.origin
            ).href;
        } catch (error) {
            throw new Error(
                `Invalid provider URL: ${value}`
            );
        }
    }

    function normalizeHeaders(value) {
        if (
            !value ||
            typeof value !==
            "object" ||
            Array.isArray(value)
        ) {
            return {};
        }

        return Object.fromEntries(
            Object.entries(value)
                .map(
                    (
                        [
                            key,
                            item
                        ]
                    ) => [
                        String(key).trim(),
                        String(item)
                    ]
                )
                .filter(
                    (
                        [
                            key
                        ]
                    ) =>
                        Boolean(key)
                )
        );
    }

    function normalizeTags(value) {
        if (Array.isArray(value)) {
            return [
                ...new Set(
                    value
                        .map(
                            item =>
                                String(item)
                                    .trim()
                                    .toLowerCase()
                        )
                        .filter(Boolean)
                )
            ];
        }

        if (!value) {
            return [];
        }

        return normalizeTags(
            String(value)
                .split(",")
        );
    }

    function cloneProvider(provider) {
        return {
            ...provider,

            tags:
                [
                    ...(provider.tags || [])
                ],

            endpoints: {
                ...(provider.endpoints || {})
            },

            authentication: {
                ...(provider.authentication || {}),

                headers: {
                    ...(provider.authentication?.headers || {})
                }
            },

            schedule: {
                ...(provider.schedule || {})
            },

            capabilities:
                [
                    ...(provider.capabilities || [])
                ],

            metadata: {
                ...(provider.metadata || {})
            }
        };
    }

    function serializeProvider(provider) {
        const cloned =
            cloneProvider(
                provider
            );

        if (
            cloned.authentication &&
            cloned.authentication.secret
        ) {
            cloned.authentication.secret =
                "[REDACTED]";
        }

        return cloned;
    }

    function makeHistoryEntry(
        action,
        provider,
        detail = {}
    ) {
        return {
            timestamp:
                new Date().toISOString(),

            action,

            provider:
                provider?.id ||
                normalizeText(
                    provider
                ),

            detail
        };
    }

    /*
    ==========================================================================
    Provider Manager
    ==========================================================================
    */

    class ProviderManager
        extends EventTarget {
        constructor(
            context,
            options = {}
        ) {
            super();

            this.context =
                context;

            this.options = {
                ...DEFAULT_OPTIONS,
                ...options
            };

            this.providers =
                new Map();

            this.history =
                [];

            this.storage =
                safeStorage();

            this.storageKey =
                `${STORAGE_PREFIX}${
                    context.root?.
                        dataset.
                        terminalInstance ||
                    "default"
                }`;

            this.destroyed =
                false;

            this.libraryUnsubscribe =
                null;

            if (
                this.options.persist
            ) {
                this.restore();
            }

            this.ingestLibrary();
            this.bindLibrary();
        }

        /*
        ======================================================================
        Validation
        ======================================================================
        */

        validate(
            definition,
            options = {}
        ) {
            const errors =
                [];

            const warnings =
                [];

            let id;

            try {
                id =
                    normalizeProviderID(
                        definition.id ||
                        definition.name
                    );
            } catch (error) {
                errors.push(
                    error.message
                );
            }

            const name =
                normalizeText(
                    definition.name
                );

            if (!name) {
                warnings.push(
                    "Provider name is empty."
                );
            }

            const endpoint =
                definition.endpoint ||
                definition.url ||
                definition.endpoints?.primary ||
                "";

            if (
                endpoint &&
                this.options.validateURLs
            ) {
                try {
                    normalizeURL(
                        endpoint
                    );
                } catch (error) {
                    errors.push(
                        error.message
                    );
                }
            }

            const documentation =
                definition.documentation ||
                definition.docs ||
                "";

            if (
                documentation &&
                this.options.validateURLs
            ) {
                try {
                    normalizeURL(
                        documentation
                    );
                } catch (error) {
                    warnings.push(
                        error.message
                    );
                }
            }

            if (
                definition.priority !==
                    undefined &&
                !Number.isFinite(
                    Number(
                        definition.priority
                    )
                )
            ) {
                errors.push(
                    "Provider priority must be numeric."
                );
            }

            if (
                definition.refreshInterval !==
                    undefined &&
                Number(
                    definition.refreshInterval
                ) <
                    0
            ) {
                errors.push(
                    "Provider refresh interval cannot be negative."
                );
            }

            if (
                !this.options.allowDuplicateEndpoints &&
                endpoint
            ) {
                const normalizedEndpoint =
                    normalizeURL(
                        endpoint
                    );

                for (const provider of this.providers.values()) {
                    if (
                        provider.id !==
                            id &&
                        provider.endpoints.primary ===
                            normalizedEndpoint
                    ) {
                        errors.push(
                            `Endpoint is already used by provider "${provider.id}".`
                        );

                        break;
                    }
                }
            }

            if (
                options.requireEndpoint ===
                    true &&
                !endpoint
            ) {
                errors.push(
                    "Provider endpoint is required."
                );
            }

            return {
                valid:
                    errors.length ===
                    0,

                id:
                    id ||
                    null,

                errors,
                warnings
            };
        }

        normalizeDefinition(
            definition,
            existing = null
        ) {
            if (
                !definition ||
                typeof definition !==
                "object"
            ) {
                throw new TypeError(
                    "Provider definition must be an object."
                );
            }

            const validation =
                this.validate(
                    definition
                );

            if (!validation.valid) {
                throw new Error(
                    validation.errors.join(
                        " "
                    )
                );
            }

            const now =
                new Date().toISOString();

            const id =
                validation.id;

            const endpoint =
                definition.endpoint ||
                definition.url ||
                definition.endpoints?.primary ||
                existing?.endpoints?.primary ||
                "";

            const endpoints = {
                ...(existing?.endpoints || {}),
                ...(definition.endpoints || {})
            };

            if (endpoint) {
                endpoints.primary =
                    normalizeURL(
                        endpoint
                    );
            }

            for (
                const [
                    key,
                    value
                ] of Object.entries(
                    endpoints
                )
            ) {
                if (value) {
                    endpoints[
                        key
                    ] =
                        normalizeURL(
                            value
                        );
                }
            }

            const authentication = {
                type:
                    normalizeAuthType(
                        definition.authentication?.type ||
                        definition.authType ||
                        existing?.authentication?.type ||
                        "none"
                    ),

                keyName:
                    normalizeText(
                        definition.authentication?.keyName ||
                        definition.keyName ||
                        existing?.authentication?.keyName ||
                        ""
                    ),

                secret:
                    definition.authentication?.secret ??
                    definition.secret ??
                    existing?.authentication?.secret ??
                    "",

                headers:
                    normalizeHeaders({
                        ...(existing?.authentication?.headers || {}),
                        ...(definition.authentication?.headers || {}),
                        ...(definition.headers || {})
                    }),

                queryParameter:
                    normalizeText(
                        definition.authentication?.queryParameter ||
                        existing?.authentication?.queryParameter ||
                        ""
                    )
            };

            const schedule = {
                enabled:
                    parseBoolean(
                        definition.schedule?.enabled ??
                        definition.scheduled ??
                        existing?.schedule?.enabled,
                        true
                    ),

                refreshInterval:
                    Math.max(
                        0,
                        parseNumber(
                            definition.schedule?.refreshInterval ??
                            definition.refreshInterval ??
                            existing?.schedule?.refreshInterval,
                            this.options.defaultRefreshInterval
                        )
                    ),

                timezone:
                    normalizeText(
                        definition.schedule?.timezone ||
                        existing?.schedule?.timezone ||
                        "UTC"
                    ),

                nextRun:
                    definition.schedule?.nextRun ??
                    existing?.schedule?.nextRun ??
                    null,

                lastRun:
                    definition.schedule?.lastRun ??
                    existing?.schedule?.lastRun ??
                    null
            };

            return {
                id,

                name:
                    normalizeText(
                        definition.name ||
                        existing?.name ||
                        id
                    ),

                type:
                    normalizeType(
                        definition.type ||
                        existing?.type ||
                        "unknown"
                    ),

                description:
                    normalizeText(
                        definition.description ||
                        existing?.description ||
                        ""
                    ),

                enabled:
                    parseBoolean(
                        definition.enabled,
                        existing?.enabled ??
                        this.options.defaultEnabled
                    ),

                eligible:
                    parseBoolean(
                        definition.eligible,
                        existing?.eligible ??
                        this.options.defaultEligible
                    ),

                priority:
                    clampInteger(
                        definition.priority,
                        existing?.priority ??
                        this.options.defaultPriority,
                        -1000000,
                        1000000
                    ),

                endpoints,

                documentation:
                    definition.documentation ||
                    definition.docs ||
                    existing?.documentation
                        ? normalizeURL(
                            definition.documentation ||
                            definition.docs ||
                            existing?.documentation
                        )
                        : "",

                homepage:
                    definition.homepage ||
                    existing?.homepage
                        ? normalizeURL(
                            definition.homepage ||
                            existing?.homepage
                        )
                        : "",

                license:
                    normalizeText(
                        definition.license ||
                        existing?.license ||
                        ""
                    ),

                country:
                    normalizeText(
                        definition.country ||
                        existing?.country ||
                        ""
                    ),

                tags:
                    normalizeTags(
                        definition.tags ??
                        existing?.tags
                    ),

                capabilities:
                    normalizeTags(
                        definition.capabilities ??
                        existing?.capabilities
                    ),

                authentication,

                schedule,

                createdAt:
                    existing?.createdAt ||
                    now,

                updatedAt:
                    now,

                revision:
                    (
                        existing?.revision ||
                        0
                    ) +
                    1,

                metadata: {
                    ...(existing?.metadata || {}),
                    ...(definition.metadata || {})
                }
            };
        }

        /*
        ======================================================================
        Core Operations
        ======================================================================
        */

        register(
            definition,
            options = {}
        ) {
            const id =
                normalizeProviderID(
                    definition.id ||
                    definition.name
                );

            const existing =
                this.providers.get(
                    id
                ) ||
                null;

            if (
                existing &&
                options.replace !==
                    true &&
                options.merge !==
                    true
            ) {
                throw new Error(
                    `Provider already exists: ${id}`
                );
            }

            const normalized =
                this.normalizeDefinition(
                    options.replace ===
                        true
                        ? definition
                        : {
                            ...(existing || {}),
                            ...definition
                        },
                    options.replace ===
                        true
                        ? null
                        : existing
                );

            this.providers.set(
                id,
                normalized
            );

            this.recordHistory(
                existing
                    ? "update"
                    : "register",
                normalized,
                {
                    replace:
                        options.replace ===
                        true,
                    merge:
                        options.merge ===
                        true
                }
            );

            this.persist();
            this.syncLibrary();
            this.syncHealth(
                normalized
            );
            this.emit(
                existing
                    ? "updated"
                    : "registered",
                {
                    provider:
                        serializeProvider(
                            normalized
                        )
                }
            );

            return cloneProvider(
                normalized
            );
        }

        update(
            id,
            patch
        ) {
            const normalizedID =
                normalizeProviderID(
                    id
                );

            const existing =
                this.providers.get(
                    normalizedID
                );

            if (!existing) {
                throw new Error(
                    `Unknown provider: ${normalizedID}`
                );
            }

            return this.register(
                {
                    ...patch,
                    id:
                        normalizedID
                },
                {
                    merge:
                        true
                }
            );
        }

        remove(
            id
        ) {
            const normalizedID =
                normalizeProviderID(
                    id
                );

            const provider =
                this.providers.get(
                    normalizedID
                );

            if (!provider) {
                return false;
            }

            this.providers.delete(
                normalizedID
            );

            this.recordHistory(
                "remove",
                provider
            );

            this.persist();
            this.syncLibrary();
            this.emit(
                "removed",
                {
                    provider:
                        serializeProvider(
                            provider
                        )
                }
            );

            return true;
        }

        get(
            id,
            options = {}
        ) {
            const provider =
                this.providers.get(
                    normalizeProviderID(
                        id
                    )
                ) ||
                null;

            if (!provider) {
                return null;
            }

            return options.redact ===
                false
                ? cloneProvider(
                    provider
                )
                : serializeProvider(
                    provider
                );
        }

        has(
            id
        ) {
            return this.providers.has(
                normalizeProviderID(
                    id
                )
            );
        }

        clone(
            sourceID,
            destinationID,
            overrides = {}
        ) {
            const source =
                this.providers.get(
                    normalizeProviderID(
                        sourceID
                    )
                );

            if (!source) {
                throw new Error(
                    `Unknown provider: ${sourceID}`
                );
            }

            const destination =
                normalizeProviderID(
                    destinationID
                );

            if (
                this.providers.has(
                    destination
                )
            ) {
                throw new Error(
                    `Provider already exists: ${destination}`
                );
            }

            return this.register({
                ...cloneProvider(
                    source
                ),

                ...overrides,

                id:
                    destination,

                name:
                    overrides.name ||
                    `${source.name} Copy`,

                createdAt:
                    undefined,

                updatedAt:
                    undefined,

                revision:
                    undefined
            });
        }

        /*
        ======================================================================
        State Operations
        ======================================================================
        */

        setEnabled(
            id,
            enabled
        ) {
            return this.update(
                id,
                {
                    enabled:
                        Boolean(
                            enabled
                        )
                }
            );
        }

        enable(
            id
        ) {
            return this.setEnabled(
                id,
                true
            );
        }

        disable(
            id
        ) {
            return this.setEnabled(
                id,
                false
            );
        }

        setEligible(
            id,
            eligible
        ) {
            return this.update(
                id,
                {
                    eligible:
                        Boolean(
                            eligible
                        )
                }
            );
        }

        setPriority(
            id,
            priority
        ) {
            return this.update(
                id,
                {
                    priority:
                        clampInteger(
                            priority,
                            this.options.defaultPriority,
                            -1000000,
                            1000000
                        )
                }
            );
        }

        setEndpoint(
            id,
            endpoint,
            name =
                "primary"
        ) {
            const provider =
                this.providers.get(
                    normalizeProviderID(
                        id
                    )
                );

            if (!provider) {
                throw new Error(
                    `Unknown provider: ${id}`
                );
            }

            return this.update(
                id,
                {
                    endpoints: {
                        ...provider.endpoints,
                        [
                            normalizeText(
                                name
                            ) ||
                            "primary"
                        ]:
                            normalizeURL(
                                endpoint
                            )
                    }
                }
            );
        }

        setSchedule(
            id,
            schedule
        ) {
            const provider =
                this.providers.get(
                    normalizeProviderID(
                        id
                    )
                );

            if (!provider) {
                throw new Error(
                    `Unknown provider: ${id}`
                );
            }

            return this.update(
                id,
                {
                    schedule: {
                        ...provider.schedule,
                        ...schedule
                    }
                }
            );
        }

        setAuthentication(
            id,
            authentication
        ) {
            const provider =
                this.providers.get(
                    normalizeProviderID(
                        id
                    )
                );

            if (!provider) {
                throw new Error(
                    `Unknown provider: ${id}`
                );
            }

            return this.update(
                id,
                {
                    authentication: {
                        ...provider.authentication,
                        ...authentication
                    }
                }
            );
        }

        bulk(
            ids,
            operation,
            value = null
        ) {
            const results =
                [];

            for (const id of ids) {
                try {
                    let result;

                    switch (operation) {
                        case "enable":
                            result =
                                this.enable(
                                    id
                                );
                            break;

                        case "disable":
                            result =
                                this.disable(
                                    id
                                );
                            break;

                        case "eligible":
                            result =
                                this.setEligible(
                                    id,
                                    true
                                );
                            break;

                        case "ineligible":
                            result =
                                this.setEligible(
                                    id,
                                    false
                                );
                            break;

                        case "priority":
                            result =
                                this.setPriority(
                                    id,
                                    value
                                );
                            break;

                        case "remove":
                            result =
                                this.remove(
                                    id
                                );
                            break;

                        default:
                            throw new Error(
                                `Unsupported provider bulk operation: ${operation}`
                            );
                    }

                    results.push({
                        id,
                        success:
                            true,
                        result
                    });
                } catch (error) {
                    results.push({
                        id,
                        success:
                            false,
                        error:
                            error.message
                    });
                }
            }

            return results;
        }

        /*
        ======================================================================
        Listing and Queries
        ======================================================================
        */

        list(
            options = {}
        ) {
            const type =
                options.type
                    ? normalizeType(
                        options.type
                    )
                    : null;

            const enabled =
                options.enabled;

            const eligible =
                options.eligible;

            const tag =
                normalizeText(
                    options.tag
                ).toLowerCase();

            const contains =
                normalizeText(
                    options.contains ||
                    options.text
                ).toLowerCase();

            let providers =
                [
                    ...this.providers.values()
                ].filter(
                    provider =>
                        (
                            !type ||
                            provider.type ===
                            type
                        ) &&
                        (
                            enabled ===
                                undefined ||
                            provider.enabled ===
                            enabled
                        ) &&
                        (
                            eligible ===
                                undefined ||
                            provider.eligible ===
                            eligible
                        ) &&
                        (
                            !tag ||
                            provider.tags.includes(
                                tag
                            )
                        ) &&
                        (
                            !contains ||
                            [
                                provider.id,
                                provider.name,
                                provider.description,
                                provider.type,
                                provider.country,
                                provider.license,
                                provider.documentation,
                                provider.homepage,
                                provider.tags.join(
                                    " "
                                ),
                                provider.capabilities.join(
                                    " "
                                )
                            ]
                                .join(" ")
                                .toLowerCase()
                                .includes(
                                    contains
                                )
                        )
                );

            const sort =
                String(
                    options.sort ||
                    "priority"
                );

            providers.sort(
                (
                    left,
                    right
                ) => {
                    switch (sort) {
                        case "name":
                            return left.name.localeCompare(
                                right.name
                            );

                        case "updated":
                            return Date.parse(
                                right.updatedAt
                            ) -
                            Date.parse(
                                left.updatedAt
                            );

                        case "type":
                            return left.type.localeCompare(
                                right.type
                            );

                        case "priority":
                        default:
                            return (
                                left.priority -
                                right.priority
                            ) ||
                            left.name.localeCompare(
                                right.name
                            );
                    }
                }
            );

            const limit =
                clampInteger(
                    options.limit,
                    providers.length ||
                    1,
                    1,
                    10000
                );

            return providers
                .slice(
                    0,
                    limit
                )
                .map(
                    provider =>
                        options.redact ===
                            false
                            ? cloneProvider(
                                provider
                            )
                            : serializeProvider(
                                provider
                            )
                );
        }

        enabled() {
            return this.list({
                enabled:
                    true
            });
        }

        eligible() {
            return this.list({
                eligible:
                    true
            });
        }

        prioritized() {
            return this.list({
                enabled:
                    true,
                eligible:
                    true,
                sort:
                    "priority"
            });
        }

        summary() {
            const providers =
                [
                    ...this.providers.values()
                ];

            const byType =
                Object.fromEntries(
                    PROVIDER_TYPES.map(
                        type => [
                            type,
                            0
                        ]
                    )
                );

            for (const provider of providers) {
                byType[
                    provider.type
                ] =
                    (
                        byType[
                            provider.type
                        ] ||
                        0
                    ) +
                    1;
            }

            return {
                version:
                    VERSION,

                providers:
                    providers.length,

                enabled:
                    providers.filter(
                        provider =>
                            provider.enabled
                    ).length,

                eligible:
                    providers.filter(
                        provider =>
                            provider.eligible
                    ).length,

                active:
                    providers.filter(
                        provider =>
                            provider.enabled &&
                            provider.eligible
                    ).length,

                scheduled:
                    providers.filter(
                        provider =>
                            provider.schedule.enabled
                    ).length,

                authenticated:
                    providers.filter(
                        provider =>
                            provider.authentication.type !==
                            "none"
                    ).length,

                byType,

                history:
                    this.history.length
            };
        }

        /*
        ======================================================================
        Library Synchronization
        ======================================================================
        */

        ingestLibrary() {
            const library =
                this.context.library;

            if (!library) {
                return [];
            }

            const collections = [
                "providers",
                "enabled-providers",
                "eligible-providers"
            ];

            const imported =
                [];

            for (const collection of collections) {
                const records =
                    library.get?.(
                        collection
                    ) ||
                    [];

                if (!Array.isArray(records)) {
                    continue;
                }

                for (const record of records) {
                    if (
                        !record ||
                        typeof record !==
                        "object"
                    ) {
                        continue;
                    }

                    const id =
                        record.id ||
                        record.provider_id ||
                        record.providerId ||
                        record.provider ||
                        record.name;

                    if (!id) {
                        continue;
                    }

                    const definition = {
                        ...record,

                        id,

                        enabled:
                            collection ===
                                "enabled-providers"
                                ? true
                                : record.enabled,

                        eligible:
                            collection ===
                                "eligible-providers"
                                ? true
                                : record.eligible
                    };

                    try {
                        const provider =
                            this.register(
                                definition,
                                {
                                    merge:
                                        true
                                }
                            );

                        imported.push(
                            provider.id
                        );
                    } catch (error) {
                        this.emit(
                            "ingest-error",
                            {
                                collection,
                                record,
                                error:
                                    error.message
                            }
                        );
                    }
                }
            }

            return [
                ...new Set(
                    imported
                )
            ];
        }

        syncLibrary() {
            if (
                !this.options.autoSyncLibrary ||
                !this.context.library
            ) {
                return;
            }

            const providers =
                this.list({
                    redact:
                        false
                });

            this.context.library.set?.(
                "providers",
                providers,
                {
                    source:
                        "provider-manager",
                    description:
                        "Speciedex provider configuration registry."
                }
            );

            this.context.library.set?.(
                "enabled-providers",
                providers.filter(
                    provider =>
                        provider.enabled
                ),
                {
                    source:
                        "provider-manager"
                }
            );

            this.context.library.set?.(
                "eligible-providers",
                providers.filter(
                    provider =>
                        provider.eligible
                ),
                {
                    source:
                        "provider-manager"
                }
            );
        }

        bindLibrary() {
            if (
                !this.options.autoSyncLibrary ||
                !this.context.library?.subscribe
            ) {
                return;
            }

            this.libraryUnsubscribe =
                this.context.library.subscribe(
                    "*",
                    event => {
                        if (
                            event.collection ===
                            "providers" &&
                            event.operation !==
                            "set"
                        ) {
                            this.ingestLibrary();
                        }
                    }
                );
        }

        /*
        ======================================================================
        Health Integration
        ======================================================================
        */

        syncHealth(
            provider
        ) {
            const health =
                this.context.providerHealth ||
                this.context.services?.get?.(
                    "provider-health"
                );

            health?.registerProvider?.(
                provider.id,
                {
                    ...serializeProvider(
                        provider
                    ),

                    endpoint:
                        provider.endpoints.primary ||
                        ""
                }
            );
        }

        async check(
            id,
            options = {}
        ) {
            const health =
                this.context.providerHealth ||
                this.context.services?.get?.(
                    "provider-health"
                );

            if (!health?.checkProvider) {
                throw new Error(
                    "Provider health service is unavailable."
                );
            }

            return health.checkProvider(
                id,
                options
            );
        }

        /*
        ======================================================================
        Persistence
        ======================================================================
        */

        persist() {
            if (
                !this.options.persist ||
                !this.storage
            ) {
                return false;
            }

            try {
                this.storage.setItem(
                    this.storageKey,
                    JSON.stringify({
                        version:
                            VERSION,

                        providers:
                            [
                                ...this.providers.values()
                            ],

                        history:
                            this.history.slice(
                                -this.options.historyLimit
                            )
                    })
                );

                return true;
            } catch (error) {
                this.emit(
                    "persistence-error",
                    {
                        error:
                            error.message
                    }
                );

                return false;
            }
        }

        restore() {
            if (!this.storage) {
                return [];
            }

            try {
                const payload =
                    JSON.parse(
                        this.storage.getItem(
                            this.storageKey
                        ) ||
                        "null"
                    );

                if (
                    !payload ||
                    !Array.isArray(
                        payload.providers
                    )
                ) {
                    return [];
                }

                for (const provider of payload.providers) {
                    try {
                        const normalized =
                            this.normalizeDefinition(
                                provider,
                                null
                            );

                        this.providers.set(
                            normalized.id,
                            normalized
                        );
                    } catch (error) {
                        this.emit(
                            "restore-error",
                            {
                                provider,
                                error:
                                    error.message
                            }
                        );
                    }
                }

                this.history =
                    Array.isArray(
                        payload.history
                    )
                        ? payload.history.slice(
                            -this.options.historyLimit
                        )
                        : [];

                return this.list();
            } catch (error) {
                this.emit(
                    "restore-error",
                    {
                        error:
                            error.message
                    }
                );

                return [];
            }
        }

        resetPersistence() {
            try {
                this.storage?.removeItem(
                    this.storageKey
                );
            } catch (error) {
                /*
                --------------------------------------------------------------
                Ignore unavailable storage.
                --------------------------------------------------------------
                */
            }
        }

        /*
        ======================================================================
        Import and Export
        ======================================================================
        */

        import(
            payload,
            options = {}
        ) {
            let providers;

            if (Array.isArray(payload)) {
                providers =
                    payload;
            } else if (
                payload &&
                Array.isArray(
                    payload.providers
                )
            ) {
                providers =
                    payload.providers;
            } else {
                throw new Error(
                    "Provider import payload must contain a providers array."
                );
            }

            const results =
                [];

            for (const definition of providers) {
                try {
                    results.push({
                        success:
                            true,

                        provider:
                            this.register(
                                definition,
                                {
                                    replace:
                                        options.replace ===
                                        true,

                                    merge:
                                        options.replace !==
                                        true
                                }
                            )
                    });
                } catch (error) {
                    results.push({
                        success:
                            false,

                        provider:
                            definition?.id ||
                            definition?.name ||
                            null,

                        error:
                            error.message
                    });
                }
            }

            return results;
        }

        export(
            options = {}
        ) {
            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                summary:
                    this.summary(),

                providers:
                    this.list({
                        redact:
                            options.includeSecrets ===
                            true
                                ? false
                                : true
                    }),

                history:
                    [
                        ...this.history
                    ]
            };
        }

        exportCSV() {
            const rows =
                this.list();

            const header = [
                "id",
                "name",
                "type",
                "enabled",
                "eligible",
                "priority",
                "primary_endpoint",
                "documentation",
                "homepage",
                "license",
                "country",
                "refresh_interval_ms",
                "schedule_enabled",
                "auth_type",
                "tags",
                "capabilities",
                "updated_at"
            ];

            const escape =
                value => {
                    const text =
                        String(
                            value ?? ""
                        );

                    return /[",\n\r]/.test(
                        text
                    )
                        ? `"${text.replace(/"/g, '""')}"`
                        : text;
                };

            const lines = [
                header.join(",")
            ];

            for (const provider of rows) {
                lines.push(
                    [
                        provider.id,
                        provider.name,
                        provider.type,
                        provider.enabled,
                        provider.eligible,
                        provider.priority,
                        provider.endpoints.primary ||
                            "",
                        provider.documentation,
                        provider.homepage,
                        provider.license,
                        provider.country,
                        provider.schedule.refreshInterval,
                        provider.schedule.enabled,
                        provider.authentication.type,
                        provider.tags.join(
                            "|"
                        ),
                        provider.capabilities.join(
                            "|"
                        ),
                        provider.updatedAt
                    ]
                        .map(
                            escape
                        )
                        .join(",")
                );
            }

            return lines.join(
                "\n"
            );
        }

        /*
        ======================================================================
        History and Events
        ======================================================================
        */

        recordHistory(
            action,
            provider,
            detail = {}
        ) {
            const entry =
                makeHistoryEntry(
                    action,
                    provider,
                    detail
                );

            this.history.push(
                entry
            );

            this.history =
                this.history.slice(
                    -this.options.historyLimit
                );

            return entry;
        }

        emit(
            type,
            detail = {}
        ) {
            this.dispatchEvent(
                new CustomEvent(
                    type,
                    {
                        detail
                    }
                )
            );

            this.context.events?.emit?.(
                `provider-manager:${type}`,
                detail
            );

            this.context.root?.
                dispatchEvent?.(
                    new CustomEvent(
                        `speciedex:terminal-provider-manager-${type}`,
                        {
                            bubbles:
                                true,

                            detail
                        }
                    )
                );

            document.dispatchEvent(
                new CustomEvent(
                    `speciedex:terminal-provider-manager-${type}`,
                    {
                        detail
                    }
                )
            );
        }

        async run(
            parameters = {}
        ) {
            const action =
                parameters.action ||
                parameters.args?.[0] ||
                "list";

            switch (action) {
                case "summary":
                case "status":
                    return this.summary();

                case "enabled":
                    return this.enabled();

                case "eligible":
                    return this.eligible();

                case "prioritized":
                    return this.prioritized();

                case "list":
                default:
                    return this.list();
            }
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.libraryUnsubscribe?.();

            this.libraryUnsubscribe =
                null;

            this.providers.clear();
            this.history =
                [];

            this.destroyed =
                true;

            this.dispatchEvent(
                new CustomEvent(
                    "destroy"
                )
            );
        }
    }

    /*
    ==========================================================================
    Initialization
    ==========================================================================
    */

    function initialize(
        context
    ) {
        if (
            context.providerManager instanceof
            ProviderManager
        ) {
            return context.providerManager;
        }

        const root =
            context.root;

        const service =
            new ProviderManager(
                context,
                {
                    persist:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderManagerPersist,
                            true
                        ),

                    autoSyncLibrary:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderManagerSyncLibrary,
                            true
                        ),

                    validateURLs:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderManagerValidateUrls,
                            true
                        ),

                    allowDuplicateEndpoints:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderManagerAllowDuplicateEndpoints,
                            false
                        ),

                    emitNotifications:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalProviderManagerNotifications,
                            true
                        ),

                    historyLimit:
                        clampInteger(
                            root?.
                                dataset.
                                terminalProviderManagerHistory,
                            DEFAULT_OPTIONS.historyLimit,
                            10,
                            10000
                        )
                }
            );

        context.providerManager =
            service;

        context.providermanager =
            service;

        context.registerService?.(
            "provider-manager",
            service
        );

        return service;
    }

    /*
    ==========================================================================
    Download Helper
    ==========================================================================
    */

    function download(
        content,
        filename,
        mime
    ) {
        const blob =
            new Blob(
                [
                    content
                ],
                {
                    type:
                        mime
                }
            );

        const url =
            URL.createObjectURL(
                blob
            );

        const anchor =
            document.createElement(
                "a"
            );

        anchor.href =
            url;

        anchor.download =
            filename;

        anchor.click();

        window.setTimeout(
            () =>
                URL.revokeObjectURL(
                    url
                ),
            1000
        );

        return filename;
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
                    "provider-manager",

                category:
                    "data",

                description:
                    "Inspect provider-manager state.",

                usage:
                    "provider-manager [list|summary|enabled|eligible|prioritized]",

                handler: async ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const service =
                        context.services?.get?.(
                            "provider-manager"
                        ) ||
                        context.providerManager;

                    if (!service) {
                        throw new Error(
                            "Provider manager is unavailable."
                        );
                    }

                    return writeJSON(
                        await service.run({
                            args
                        })
                    );
                }
            },

            {
                name:
                    "provider-add",

                category:
                    "data",

                description:
                    "Register a provider.",

                usage:
                    "provider-add <id> <name> [endpoint] [--type TYPE] [--priority N] [--disabled] [--ineligible]",

                handler: ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const id =
                        args.shift();

                    const name =
                        args.shift();

                    const endpoint =
                        args.shift() ||
                        parsed.options.endpoint ||
                        "";

                    if (
                        !id ||
                        !name
                    ) {
                        throw new Error(
                            "Usage: provider-add <id> <name> [endpoint]"
                        );
                    }

                    return writeJSON(
                        context.providerManager.register({
                            id,
                            name,
                            endpoint,
                            type:
                                parsed.options.type ||
                                "unknown",
                            priority:
                                parsed.options.priority,
                            enabled:
                                parsed.flags.disabled
                                    ? false
                                    : true,
                            eligible:
                                parsed.flags.ineligible
                                    ? false
                                    : true,
                            documentation:
                                parsed.options.documentation ||
                                parsed.options.docs ||
                                "",
                            homepage:
                                parsed.options.homepage ||
                                "",
                            license:
                                parsed.options.license ||
                                "",
                            country:
                                parsed.options.country ||
                                "",
                            tags:
                                parsed.options.tags ||
                                "",
                            capabilities:
                                parsed.options.capabilities ||
                                ""
                        })
                    );
                }
            },

            {
                name:
                    "provider-get",

                category:
                    "data",

                description:
                    "Display one provider configuration.",

                usage:
                    "provider-get <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const provider =
                        context.providerManager.get(
                            args[0]
                        );

                    if (!provider) {
                        throw new Error(
                            `Unknown provider: ${args[0]}`
                        );
                    }

                    return writeJSON(
                        provider
                    );
                }
            },

            {
                name:
                    "provider-remove",

                category:
                    "data",

                description:
                    "Remove a provider.",

                usage:
                    "provider-remove <id>",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const id =
                        args[0];

                    if (!id) {
                        throw new Error(
                            "A provider ID is required."
                        );
                    }

                    if (
                        !context.providerManager.remove(
                            id
                        )
                    ) {
                        throw new Error(
                            `Unknown provider: ${id}`
                        );
                    }

                    return write(
                        `Provider removed: ${id}`,
                        "success"
                    );
                }
            },

            {
                name:
                    "provider-enable",

                category:
                    "data",

                description:
                    "Enable a provider.",

                usage:
                    "provider-enable <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.providerManager.enable(
                            args[0]
                        )
                    )
            },

            {
                name:
                    "provider-disable",

                category:
                    "data",

                description:
                    "Disable a provider.",

                usage:
                    "provider-disable <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.providerManager.disable(
                            args[0]
                        )
                    )
            },

            {
                name:
                    "provider-eligible",

                category:
                    "data",

                description:
                    "Set provider eligibility.",

                usage:
                    "provider-eligible <id> <true|false>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    if (
                        args.length <
                        2
                    ) {
                        throw new Error(
                            "Usage: provider-eligible <id> <true|false>"
                        );
                    }

                    return writeJSON(
                        context.providerManager.setEligible(
                            args[0],
                            parseBoolean(
                                args[1],
                                true
                            )
                        )
                    );
                }
            },

            {
                name:
                    "provider-priority",

                category:
                    "data",

                description:
                    "Set provider priority.",

                usage:
                    "provider-priority <id> <priority>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    if (
                        args.length <
                        2
                    ) {
                        throw new Error(
                            "Usage: provider-priority <id> <priority>"
                        );
                    }

                    return writeJSON(
                        context.providerManager.setPriority(
                            args[0],
                            args[1]
                        )
                    );
                }
            },

            {
                name:
                    "provider-endpoint",

                category:
                    "data",

                description:
                    "Set a provider endpoint.",

                usage:
                    "provider-endpoint <id> <url> [name]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    if (
                        args.length <
                        2
                    ) {
                        throw new Error(
                            "Usage: provider-endpoint <id> <url> [name]"
                        );
                    }

                    return writeJSON(
                        context.providerManager.setEndpoint(
                            args[0],
                            args[1],
                            args[2] ||
                            "primary"
                        )
                    );
                }
            },

            {
                name:
                    "provider-schedule",

                category:
                    "data",

                description:
                    "Configure provider refresh scheduling.",

                usage:
                    "provider-schedule <id> <interval-ms> [enabled]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    if (
                        args.length <
                        2
                    ) {
                        throw new Error(
                            "Usage: provider-schedule <id> <interval-ms> [enabled]"
                        );
                    }

                    return writeJSON(
                        context.providerManager.setSchedule(
                            args[0],
                            {
                                refreshInterval:
                                    parseNumber(
                                        args[1],
                                        DEFAULT_OPTIONS.defaultRefreshInterval
                                    ),

                                enabled:
                                    args[2] ===
                                        undefined
                                        ? true
                                        : parseBoolean(
                                            args[2],
                                            true
                                        )
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-clone",

                category:
                    "data",

                description:
                    "Clone a provider configuration.",

                usage:
                    "provider-clone <source-id> <destination-id> [name]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    if (
                        args.length <
                        2
                    ) {
                        throw new Error(
                            "Usage: provider-clone <source-id> <destination-id> [name]"
                        );
                    }

                    return writeJSON(
                        context.providerManager.clone(
                            args[0],
                            args[1],
                            {
                                name:
                                    args.slice(
                                        2
                                    ).join(
                                        " "
                                    ) ||
                                    undefined
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-validate",

                category:
                    "data",

                description:
                    "Validate a provider definition or existing provider.",

                usage:
                    "provider-validate <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const provider =
                        context.providerManager.get(
                            args[0],
                            {
                                redact:
                                    false
                            }
                        );

                    if (!provider) {
                        throw new Error(
                            `Unknown provider: ${args[0]}`
                        );
                    }

                    return writeJSON(
                        context.providerManager.validate(
                            provider,
                            {
                                requireEndpoint:
                                    true
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-check",

                category:
                    "data",

                description:
                    "Run a provider health check.",

                usage:
                    "provider-check <id> [--timeout MS] [--method HEAD|GET]",

                handler: async ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    if (!args[0]) {
                        throw new Error(
                            "A provider ID is required."
                        );
                    }

                    return writeJSON(
                        await context.providerManager.check(
                            args[0],
                            {
                                timeout:
                                    parsed.options.timeout,
                                method:
                                    parsed.options.method
                            }
                        )
                    );
                }
            },

            {
                name:
                    "provider-import",

                category:
                    "data",

                description:
                    "Import provider definitions from a library collection.",

                usage:
                    "provider-import [collection]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const collection =
                        args[0] ||
                        "providers-import";

                    const records =
                        context.library?.get?.(
                            collection
                        ) ||
                        [];

                    return writeJSON(
                        context.providerManager.import(
                            records
                        )
                    );
                }
            },

            {
                name:
                    "provider-export",

                category:
                    "data",

                description:
                    "Export provider configurations as JSON or CSV.",

                usage:
                    "provider-export [json|csv] [filename]",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const format =
                        String(
                            args[0] ||
                            "json"
                        ).toLowerCase();

                    if (
                        format ===
                        "csv"
                    ) {
                        const filename =
                            args[1] ||
                            "speciedex-providers.csv";

                        download(
                            context.providerManager.exportCSV(),
                            filename,
                            "text/csv"
                        );

                        return write(
                            `Providers exported to ${filename}.`,
                            "success"
                        );
                    }

                    const filename =
                        args[1] ||
                        "speciedex-providers.json";

                    download(
                        JSON.stringify(
                            context.providerManager.export(),
                            null,
                            2
                        ),
                        filename,
                        "application/json"
                    );

                    return write(
                        `Providers exported to ${filename}.`,
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

            STORAGE_PREFIX,
            DEFAULT_OPTIONS,
            AUTH_TYPES,
            PROVIDER_TYPES,
            ProviderManager,

            normalizeProviderID,
            normalizeText,
            normalizeType,
            normalizeAuthType,
            parseBoolean,
            parseNumber,
            clampInteger,
            normalizeURL,
            normalizeHeaders,
            normalizeTags,
            cloneProvider,
            serializeProvider,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalProviderManager =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] =
        api;

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
