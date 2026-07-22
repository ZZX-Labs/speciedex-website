/*
========================================================================
Speciedex.org
Terminal Storage Service
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Storage";
    const DEFAULT_NAMESPACE = "speciedex-terminal";
    const DEFAULT_VERSION = 1;
    const DEFAULT_BACKEND = "local";
    const DEFAULT_MAX_MEMORY_ENTRIES = 2048;
    const ENVELOPE_MARKER = "__speciedex_storage_envelope__";
    const RESERVED_KEYS = new Set(["__proto__", "prototype", "constructor"]);

    function now() {
        return Date.now();
    }

    function iso(timestamp = now()) {
        return new Date(timestamp).toISOString();
    }

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function clone(value) {
        if (typeof structuredClone === "function") {
            try {
                return structuredClone(value);
            } catch (error) {
                /* Fall through to JSON-compatible cloning. */
            }
        }

        if (value === undefined || value === null || typeof value !== "object") {
            return value;
        }

        try {
            return JSON.parse(JSON.stringify(value));
        } catch (error) {
            return value;
        }
    }

    function normalizeNamespace(value) {
        const normalized = String(value || DEFAULT_NAMESPACE)
            .trim()
            .replace(/\s+/g, "-")
            .replace(/:+/g, "-")
            .replace(/[^a-zA-Z0-9._-]/g, "-")
            .replace(/-+/g, "-")
            .replace(/^[-.]+|[-.]+$/g, "");

        return normalized || DEFAULT_NAMESPACE;
    }

    function normalizeKey(value) {
        if (Array.isArray(value)) {
            value = value.join(".");
        }

        const key = String(value ?? "").trim();

        if (!key) {
            throw new TypeError("Storage key must be a non-empty string.");
        }

        const parts = key.split(".");
        if (parts.some((part) => RESERVED_KEYS.has(part))) {
            throw new TypeError("Storage key contains a reserved property name.");
        }

        if (key.includes("\u0000")) {
            throw new TypeError("Storage key contains an invalid null character.");
        }

        return key;
    }

    function normalizeBackend(value) {
        const backend = String(value || DEFAULT_BACKEND).toLowerCase();

        if (["local", "localstorage", "persistent"].includes(backend)) {
            return "local";
        }

        if (["session", "sessionstorage", "temporary"].includes(backend)) {
            return "session";
        }

        if (["memory", "volatile"].includes(backend)) {
            return "memory";
        }

        return DEFAULT_BACKEND;
    }

    function parseBoolean(value, fallback = false) {
        if (typeof value === "boolean") {
            return value;
        }

        if (value === undefined || value === null || value === "") {
            return fallback;
        }

        return ["1", "true", "yes", "on", "enabled"].includes(
            String(value).trim().toLowerCase()
        );
    }

    function parseDuration(value, fallback = 0) {
        if (value === undefined || value === null || value === "") {
            return fallback;
        }

        if (typeof value === "number" && Number.isFinite(value)) {
            return Math.max(0, value);
        }

        const text = String(value).trim().toLowerCase();
        const match = text.match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|h|d|w)?$/);

        if (!match) {
            return fallback;
        }

        const amount = Number(match[1]);
        const unit = match[2] || "ms";
        const multipliers = {
            ms: 1,
            s: 1000,
            m: 60 * 1000,
            h: 60 * 60 * 1000,
            d: 24 * 60 * 60 * 1000,
            w: 7 * 24 * 60 * 60 * 1000
        };

        return Math.max(0, Math.round(amount * multipliers[unit]));
    }

    function byteLength(text) {
        const value = String(text ?? "");

        if (typeof TextEncoder === "function") {
            return new TextEncoder().encode(value).length;
        }

        return unescape(encodeURIComponent(value)).length;
    }

    function safeDispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, { detail }));
        } catch (error) {
            /* Event delivery must never break storage operations. */
        }
    }

    function serialize(value) {
        const seen = new WeakSet();

        return JSON.stringify(value, function replacer(key, current) {
            if (typeof current === "bigint") {
                return {
                    __speciedexType: "BigInt",
                    value: current.toString()
                };
            }

            if (current instanceof Date) {
                return {
                    __speciedexType: "Date",
                    value: current.toISOString()
                };
            }

            if (current instanceof Map) {
                return {
                    __speciedexType: "Map",
                    value: Array.from(current.entries())
                };
            }

            if (current instanceof Set) {
                return {
                    __speciedexType: "Set",
                    value: Array.from(current.values())
                };
            }

            if (current instanceof RegExp) {
                return {
                    __speciedexType: "RegExp",
                    source: current.source,
                    flags: current.flags
                };
            }

            if (current instanceof Error) {
                return {
                    __speciedexType: "Error",
                    name: current.name,
                    message: current.message,
                    stack: current.stack || ""
                };
            }

            if (typeof current === "number" && !Number.isFinite(current)) {
                return {
                    __speciedexType: "Number",
                    value: String(current)
                };
            }

            if (typeof current === "undefined") {
                return {
                    __speciedexType: "Undefined"
                };
            }

            if (typeof current === "object" && current !== null) {
                if (seen.has(current)) {
                    throw new TypeError("Circular values cannot be stored.");
                }
                seen.add(current);
            }

            return current;
        });
    }

    function deserialize(text) {
        return JSON.parse(text, function reviver(key, current) {
            if (!isObject(current) || !current.__speciedexType) {
                return current;
            }

            switch (current.__speciedexType) {
                case "BigInt":
                    return typeof BigInt === "function"
                        ? BigInt(current.value)
                        : current.value;

                case "Date":
                    return new Date(current.value);

                case "Map":
                    return new Map(current.value || []);

                case "Set":
                    return new Set(current.value || []);

                case "RegExp":
                    return new RegExp(current.source || "", current.flags || "");

                case "Error": {
                    const error = new Error(current.message || "");
                    error.name = current.name || "Error";
                    error.stack = current.stack || error.stack;
                    return error;
                }

                case "Number":
                    if (current.value === "Infinity") {
                        return Infinity;
                    }
                    if (current.value === "-Infinity") {
                        return -Infinity;
                    }
                    return NaN;

                case "Undefined":
                    return undefined;

                default:
                    return current;
            }
        });
    }

    class MemoryBackend {
        constructor(limit = DEFAULT_MAX_MEMORY_ENTRIES) {
            this.name = "memory";
            this.limit = Math.max(1, Number(limit) || DEFAULT_MAX_MEMORY_ENTRIES);
            this.data = new Map();
        }

        getItem(key) {
            if (!this.data.has(key)) {
                return null;
            }

            const value = this.data.get(key);
            this.data.delete(key);
            this.data.set(key, value);
            return value;
        }

        setItem(key, value) {
            if (this.data.has(key)) {
                this.data.delete(key);
            }

            this.data.set(key, String(value));

            while (this.data.size > this.limit) {
                const oldest = this.data.keys().next().value;
                this.data.delete(oldest);
            }
        }

        removeItem(key) {
            this.data.delete(key);
        }

        clear() {
            this.data.clear();
        }

        key(index) {
            return Array.from(this.data.keys())[index] ?? null;
        }

        get length() {
            return this.data.size;
        }
    }

    class WebStorageBackend {
        constructor(storage, name) {
            this.storage = storage;
            this.name = name;
        }

        getItem(key) {
            return this.storage.getItem(key);
        }

        setItem(key, value) {
            this.storage.setItem(key, value);
        }

        removeItem(key) {
            this.storage.removeItem(key);
        }

        clear() {
            this.storage.clear();
        }

        key(index) {
            return this.storage.key(index);
        }

        get length() {
            return this.storage.length;
        }
    }

    class StorageService extends EventTarget {
        constructor(options = {}) {
            super();

            if (typeof options === "string") {
                options = { namespace: options };
            }

            this.context = options.context || null;
            this.namespace = normalizeNamespace(options.namespace);
            this.version = Number(options.version) || DEFAULT_VERSION;
            this.backendName = normalizeBackend(options.backend);
            this.prefix = `${this.namespace}:`;
            this.memory = new MemoryBackend(
                options.maxMemoryEntries || DEFAULT_MAX_MEMORY_ENTRIES
            );
            this.backend = this._selectBackend(this.backendName);
            this.fallbackToMemory = options.fallbackToMemory !== false;
            this.defaultTTL = parseDuration(options.defaultTTL, 0);
            this.cloneValues = options.cloneValues !== false;
            this.crossTab = options.crossTab !== false;
            this.autoPrune = options.autoPrune !== false;
            this.watchers = new Map();
            this.globalWatchers = new Set();
            this.destroyed = false;
            this.lastError = null;
            this.operations = {
                reads: 0,
                writes: 0,
                deletes: 0,
                clears: 0,
                hits: 0,
                misses: 0,
                expired: 0,
                errors: 0
            };

            this._storageListener = this._handleStorageEvent.bind(this);

            if (this.crossTab && this.backend.name === "local") {
                window.addEventListener("storage", this._storageListener);
            }

            if (this.autoPrune) {
                this.pruneExpired();
            }
        }

        _selectBackend(name) {
            if (name === "memory") {
                return this.memory;
            }

            const candidate = name === "session"
                ? window.sessionStorage
                : window.localStorage;

            try {
                const probe = `${this.prefix}__probe__:${Math.random()}`;
                candidate.setItem(probe, "1");
                candidate.removeItem(probe);
                return new WebStorageBackend(candidate, name);
            } catch (error) {
                this.lastError = error;
                return this.memory;
            }
        }

        _assertActive() {
            if (this.destroyed) {
                throw new Error("Storage service has been destroyed.");
            }
        }

        key(key) {
            return `${this.prefix}${normalizeKey(key)}`;
        }

        unkey(fullKey) {
            const value = String(fullKey || "");
            return value.startsWith(this.prefix)
                ? value.slice(this.prefix.length)
                : value;
        }

        _createEnvelope(key, value, options = {}) {
            const timestamp = now();
            const ttl = parseDuration(options.ttl, this.defaultTTL);
            const expiresAt = options.expiresAt
                ? Number(options.expiresAt)
                : ttl > 0
                    ? timestamp + ttl
                    : null;

            return {
                [ENVELOPE_MARKER]: true,
                namespace: this.namespace,
                key,
                version: Number(options.version) || this.version,
                createdAt: Number(options.createdAt) || timestamp,
                updatedAt: timestamp,
                expiresAt: Number.isFinite(expiresAt) ? expiresAt : null,
                value
            };
        }

        _isEnvelope(value) {
            return isObject(value) && value[ENVELOPE_MARKER] === true;
        }

        _isExpired(envelope, timestamp = now()) {
            return Boolean(
                envelope &&
                Number.isFinite(envelope.expiresAt) &&
                envelope.expiresAt <= timestamp
            );
        }

        _readRaw(fullKey) {
            try {
                return this.backend.getItem(fullKey);
            } catch (error) {
                this._recordError(error);

                if (this.fallbackToMemory && this.backend !== this.memory) {
                    return this.memory.getItem(fullKey);
                }

                return null;
            }
        }

        _writeRaw(fullKey, serialized) {
            try {
                this.backend.setItem(fullKey, serialized);

                if (this.backend !== this.memory) {
                    this.memory.setItem(fullKey, serialized);
                }

                return true;
            } catch (error) {
                this._recordError(error);

                if (this.fallbackToMemory && this.backend !== this.memory) {
                    try {
                        this.memory.setItem(fullKey, serialized);
                        return true;
                    } catch (memoryError) {
                        this._recordError(memoryError);
                    }
                }

                return false;
            }
        }

        _removeRaw(fullKey) {
            let removed = false;

            try {
                this.backend.removeItem(fullKey);
                removed = true;
            } catch (error) {
                this._recordError(error);
            }

            if (this.backend !== this.memory) {
                this.memory.removeItem(fullKey);
            }

            return removed;
        }

        _recordError(error) {
            this.lastError = error;
            this.operations.errors += 1;
            safeDispatch(this, "error", {
                error,
                namespace: this.namespace,
                backend: this.backend.name
            });
        }

        _emit(type, detail = {}) {
            const event = {
                type,
                namespace: this.namespace,
                backend: this.backend.name,
                timestamp: iso(),
                ...detail
            };

            safeDispatch(this, type, event);
            safeDispatch(this, "change", event);

            const key = detail.key;
            if (key && this.watchers.has(key)) {
                for (const callback of Array.from(this.watchers.get(key))) {
                    try {
                        callback(event);
                    } catch (error) {
                        this._recordError(error);
                    }
                }
            }

            for (const callback of Array.from(this.globalWatchers)) {
                try {
                    callback(event);
                } catch (error) {
                    this._recordError(error);
                }
            }

            try {
                this.context?.events?.emit?.(`storage:${type}`, event);
            } catch (error) {
                this._recordError(error);
            }

            return event;
        }

        _handleStorageEvent(event) {
            if (
                this.destroyed ||
                !event ||
                !event.key ||
                !event.key.startsWith(this.prefix)
            ) {
                return;
            }

            const key = this.unkey(event.key);
            let value;
            let previous;

            try {
                const nextEnvelope = event.newValue
                    ? deserialize(event.newValue)
                    : null;
                const previousEnvelope = event.oldValue
                    ? deserialize(event.oldValue)
                    : null;

                value = this._isEnvelope(nextEnvelope)
                    ? nextEnvelope.value
                    : nextEnvelope;
                previous = this._isEnvelope(previousEnvelope)
                    ? previousEnvelope.value
                    : previousEnvelope;
            } catch (error) {
                this._recordError(error);
            }

            this._emit("external", {
                key,
                value: clone(value),
                previous: clone(previous),
                source: "storage-event"
            });
        }

        get(key, fallback = null, options = {}) {
            this._assertActive();

            key = normalizeKey(key);
            const fullKey = this.key(key);
            this.operations.reads += 1;

            const raw = this._readRaw(fullKey);

            if (raw === null) {
                this.operations.misses += 1;
                return typeof fallback === "function" ? fallback(key) : fallback;
            }

            try {
                const parsed = deserialize(raw);
                const envelope = this._isEnvelope(parsed)
                    ? parsed
                    : {
                        [ENVELOPE_MARKER]: false,
                        key,
                        value: parsed,
                        expiresAt: null
                    };

                if (this._isExpired(envelope)) {
                    this.operations.expired += 1;
                    this.operations.misses += 1;
                    this._removeRaw(fullKey);

                    this._emit("expire", {
                        key,
                        previous: clone(envelope.value)
                    });

                    return typeof fallback === "function"
                        ? fallback(key)
                        : fallback;
                }

                this.operations.hits += 1;

                if (options.touch === true && this._isEnvelope(parsed)) {
                    this.set(key, envelope.value, {
                        ttl: options.ttl ?? this.defaultTTL,
                        createdAt: envelope.createdAt,
                        version: envelope.version,
                        silent: true
                    });
                }

                return this.cloneValues && options.clone !== false
                    ? clone(envelope.value)
                    : envelope.value;
            } catch (error) {
                this._recordError(error);
                this.operations.misses += 1;

                if (options.deleteCorrupt !== false) {
                    this._removeRaw(fullKey);
                }

                return typeof fallback === "function" ? fallback(key) : fallback;
            }
        }

        getEntry(key, options = {}) {
            this._assertActive();

            key = normalizeKey(key);
            const raw = this._readRaw(this.key(key));

            if (raw === null) {
                return null;
            }

            try {
                const parsed = deserialize(raw);
                const envelope = this._isEnvelope(parsed)
                    ? parsed
                    : this._createEnvelope(key, parsed, {
                        createdAt: 0,
                        expiresAt: null
                    });

                if (this._isExpired(envelope)) {
                    if (options.includeExpired !== true) {
                        this.delete(key);
                        return null;
                    }
                }

                return {
                    key,
                    value: this.cloneValues ? clone(envelope.value) : envelope.value,
                    version: envelope.version,
                    createdAt: envelope.createdAt,
                    updatedAt: envelope.updatedAt,
                    expiresAt: envelope.expiresAt,
                    expired: this._isExpired(envelope),
                    size: byteLength(raw)
                };
            } catch (error) {
                this._recordError(error);
                return null;
            }
        }

        set(key, value, options = {}) {
            this._assertActive();

            key = normalizeKey(key);

            if (value === undefined && options.allowUndefined !== true) {
                return this.delete(key);
            }

            const previous = options.capturePrevious === false
                ? undefined
                : this.get(key, undefined, { clone: true });

            const envelope = this._createEnvelope(
                key,
                this.cloneValues ? clone(value) : value,
                options
            );

            let serialized;

            try {
                serialized = serialize(envelope);
            } catch (error) {
                this._recordError(error);
                throw error;
            }

            if (!this._writeRaw(this.key(key), serialized)) {
                throw this.lastError || new Error(`Unable to store "${key}".`);
            }

            this.operations.writes += 1;

            if (options.silent !== true) {
                this._emit("set", {
                    key,
                    value: this.cloneValues ? clone(value) : value,
                    previous,
                    expiresAt: envelope.expiresAt,
                    size: byteLength(serialized)
                });
            }

            return this.cloneValues ? clone(value) : value;
        }

        setMany(entries, options = {}) {
            this._assertActive();

            const pairs = entries instanceof Map
                ? Array.from(entries.entries())
                : Array.isArray(entries)
                    ? entries.map((item) => Array.isArray(item)
                        ? item
                        : [item.key, item.value, item.options])
                    : Object.entries(entries || {});

            const results = {};
            const rollback = [];

            try {
                for (const pair of pairs) {
                    const [key, value, itemOptions] = pair;
                    const normalized = normalizeKey(key);
                    rollback.push({
                        key: normalized,
                        entry: this.getEntry(normalized, {
                            includeExpired: true
                        })
                    });

                    results[normalized] = this.set(normalized, value, {
                        ...options,
                        ...(isObject(itemOptions) ? itemOptions : {}),
                        silent: true
                    });
                }
            } catch (error) {
                if (options.atomic !== false) {
                    for (const item of rollback.reverse()) {
                        if (item.entry) {
                            this.set(item.key, item.entry.value, {
                                expiresAt: item.entry.expiresAt,
                                createdAt: item.entry.createdAt,
                                version: item.entry.version,
                                silent: true,
                                allowUndefined: true
                            });
                        } else {
                            this.delete(item.key, { silent: true });
                        }
                    }
                }
                throw error;
            }

            if (options.silent !== true) {
                this._emit("batch", {
                    keys: Object.keys(results),
                    count: Object.keys(results).length
                });
            }

            return results;
        }

        has(key) {
            const sentinel = Symbol("missing");
            return this.get(key, sentinel) !== sentinel;
        }

        delete(key, options = {}) {
            this._assertActive();

            key = normalizeKey(key);
            const fullKey = this.key(key);
            const existed = this._readRaw(fullKey) !== null;
            const previous = existed
                ? this.get(key, undefined, { clone: true })
                : undefined;

            this._removeRaw(fullKey);

            if (existed) {
                this.operations.deletes += 1;

                if (options.silent !== true) {
                    this._emit("delete", {
                        key,
                        previous
                    });
                }
            }

            return existed;
        }

        deleteMany(keys, options = {}) {
            const deleted = [];

            for (const key of keys || []) {
                const normalized = normalizeKey(key);
                if (this.delete(normalized, { silent: true })) {
                    deleted.push(normalized);
                }
            }

            if (deleted.length && options.silent !== true) {
                this._emit("deleteMany", {
                    keys: deleted,
                    count: deleted.length
                });
            }

            return deleted;
        }

        keys(options = {}) {
            this._assertActive();

            const output = [];
            const source = this.backend;
            const prefix = options.prefix
                ? normalizeKey(options.prefix)
                : "";

            try {
                for (let index = 0; index < source.length; index += 1) {
                    const fullKey = source.key(index);

                    if (!fullKey || !fullKey.startsWith(this.prefix)) {
                        continue;
                    }

                    const key = this.unkey(fullKey);

                    if (prefix && !key.startsWith(prefix)) {
                        continue;
                    }

                    if (options.includeExpired !== true) {
                        const entry = this.getEntry(key);
                        if (!entry) {
                            continue;
                        }
                    }

                    output.push(key);
                }
            } catch (error) {
                this._recordError(error);
            }

            return output.sort((a, b) => a.localeCompare(b));
        }

        entries(options = {}) {
            return this.keys(options)
                .map((key) => this.getEntry(key, options))
                .filter(Boolean);
        }

        values(options = {}) {
            return this.entries(options).map((entry) => entry.value);
        }

        size(options = {}) {
            return this.keys(options).length;
        }

        clear(options = {}) {
            this._assertActive();

            const prefix = options.prefix
                ? normalizeKey(options.prefix)
                : "";
            const keys = this.keys({
                prefix,
                includeExpired: true
            });

            for (const key of keys) {
                this._removeRaw(this.key(key));
            }

            this.operations.clears += 1;

            if (options.silent !== true) {
                this._emit("clear", {
                    prefix: prefix || null,
                    keys,
                    count: keys.length
                });
            }

            return keys.length;
        }

        pruneExpired() {
            this._assertActive();

            const timestamp = now();
            const removed = [];

            for (const key of this.keys({ includeExpired: true })) {
                const entry = this.getEntry(key, { includeExpired: true });

                if (entry && entry.expiresAt && entry.expiresAt <= timestamp) {
                    this._removeRaw(this.key(key));
                    removed.push(key);
                    this.operations.expired += 1;
                }
            }

            if (removed.length) {
                this._emit("prune", {
                    keys: removed,
                    count: removed.length
                });
            }

            return removed;
        }

        update(key, updater, fallback = null, options = {}) {
            if (typeof updater !== "function") {
                throw new TypeError("Storage updater must be a function.");
            }

            const current = this.get(key, fallback);
            const next = updater(clone(current), key, this);

            if (next === StorageService.DELETE) {
                this.delete(key, options);
                return undefined;
            }

            return this.set(key, next, options);
        }

        increment(key, amount = 1, options = {}) {
            const delta = Number(amount);

            if (!Number.isFinite(delta)) {
                throw new TypeError("Increment amount must be a finite number.");
            }

            return this.update(
                key,
                (value) => {
                    const current = Number(value || 0);
                    return (Number.isFinite(current) ? current : 0) + delta;
                },
                0,
                options
            );
        }

        append(key, value, options = {}) {
            return this.update(
                key,
                (current) => {
                    const list = Array.isArray(current) ? current : [];
                    list.push(value);

                    const limit = Number(options.limit);
                    if (Number.isFinite(limit) && limit > 0 && list.length > limit) {
                        list.splice(0, list.length - limit);
                    }

                    return list;
                },
                [],
                options
            );
        }

        watch(key, callback, options = {}) {
            if (typeof callback !== "function") {
                throw new TypeError("Storage watcher must be a function.");
            }

            key = normalizeKey(key);

            if (!this.watchers.has(key)) {
                this.watchers.set(key, new Set());
            }

            this.watchers.get(key).add(callback);

            if (options.immediate === true) {
                callback({
                    type: "initial",
                    key,
                    value: this.get(key),
                    namespace: this.namespace,
                    backend: this.backend.name,
                    timestamp: iso()
                });
            }

            return () => this.unwatch(key, callback);
        }

        unwatch(key, callback) {
            key = normalizeKey(key);
            const callbacks = this.watchers.get(key);

            if (!callbacks) {
                return false;
            }

            const removed = callbacks.delete(callback);

            if (!callbacks.size) {
                this.watchers.delete(key);
            }

            return removed;
        }

        watchAll(callback) {
            if (typeof callback !== "function") {
                throw new TypeError("Global storage watcher must be a function.");
            }

            this.globalWatchers.add(callback);
            return () => this.globalWatchers.delete(callback);
        }

        export(options = {}) {
            const entries = {};

            for (const entry of this.entries({
                includeExpired: options.includeExpired === true
            })) {
                entries[entry.key] = options.withMetadata === true
                    ? entry
                    : entry.value;
            }

            const payload = {
                schema: "speciedex-terminal-storage",
                schemaVersion: 1,
                namespace: this.namespace,
                storageVersion: this.version,
                backend: this.backend.name,
                exportedAt: iso(),
                entries
            };

            return options.stringify === false
                ? payload
                : serialize(payload);
        }

        import(input, options = {}) {
            this._assertActive();

            const payload = typeof input === "string"
                ? deserialize(input)
                : clone(input);

            if (!isObject(payload)) {
                throw new TypeError("Storage import must be an object or JSON string.");
            }

            const entries = isObject(payload.entries)
                ? payload.entries
                : payload;

            if (options.replace === true) {
                this.clear({ silent: true });
            }

            let imported = 0;
            const skipped = [];

            for (const [key, item] of Object.entries(entries)) {
                try {
                    if (
                        isObject(item) &&
                        Object.prototype.hasOwnProperty.call(item, "value") &&
                        (
                            Object.prototype.hasOwnProperty.call(item, "updatedAt") ||
                            Object.prototype.hasOwnProperty.call(item, "expiresAt")
                        )
                    ) {
                        this.set(key, item.value, {
                            expiresAt: item.expiresAt,
                            createdAt: item.createdAt,
                            version: item.version,
                            silent: true,
                            allowUndefined: true
                        });
                    } else {
                        this.set(key, item, {
                            silent: true,
                            allowUndefined: true
                        });
                    }
                    imported += 1;
                } catch (error) {
                    skipped.push({
                        key,
                        error: error.message
                    });

                    if (options.strict === true) {
                        throw error;
                    }
                }
            }

            this._emit("import", {
                imported,
                skipped
            });

            return {
                imported,
                skipped
            };
        }

        async estimate() {
            const entries = this.entries({ includeExpired: true });
            const used = entries.reduce((total, entry) => total + entry.size, 0);
            let quota = null;
            let usage = null;

            try {
                if (navigator.storage?.estimate) {
                    const estimate = await navigator.storage.estimate();
                    quota = estimate.quota ?? null;
                    usage = estimate.usage ?? null;
                }
            } catch (error) {
                this._recordError(error);
            }

            return {
                namespace: this.namespace,
                backend: this.backend.name,
                entries: entries.length,
                namespaceBytes: used,
                originUsageBytes: usage,
                originQuotaBytes: quota,
                originUsagePercent: quota && usage !== null
                    ? Number(((usage / quota) * 100).toFixed(4))
                    : null
            };
        }

        status() {
            return {
                name: "storage",
                module: MODULE_NAME,
                namespace: this.namespace,
                prefix: this.prefix,
                version: this.version,
                configuredBackend: this.backendName,
                activeBackend: this.backend.name,
                fallbackToMemory: this.fallbackToMemory,
                crossTab: this.crossTab,
                defaultTTL: this.defaultTTL,
                entries: this.size(),
                memoryEntries: this.memory.length,
                watchers: Array.from(this.watchers.values())
                    .reduce((total, set) => total + set.size, 0),
                globalWatchers: this.globalWatchers.size,
                destroyed: this.destroyed,
                operations: { ...this.operations },
                lastError: this.lastError
                    ? {
                        name: this.lastError.name,
                        message: this.lastError.message
                    }
                    : null
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            window.removeEventListener("storage", this._storageListener);
            this.watchers.clear();
            this.globalWatchers.clear();
            this.destroyed = true;

            safeDispatch(this, "destroy", {
                namespace: this.namespace,
                timestamp: iso()
            });

            return true;
        }
    }

    StorageService.DELETE = Symbol("StorageService.DELETE");

    function parseArguments(args = []) {
        const result = {
            action: "status",
            positional: [],
            options: {}
        };

        for (const argument of args) {
            const value = String(argument);

            if (value.startsWith("--")) {
                const [name, ...rest] = value.slice(2).split("=");
                result.options[name] = rest.length
                    ? rest.join("=")
                    : true;
            } else {
                result.positional.push(value);
            }
        }

        if (result.positional.length) {
            result.action = result.positional.shift().toLowerCase();
        }

        return result;
    }

    function parseInputValue(raw, options = {}) {
        if (options.string === true) {
            return String(raw ?? "");
        }

        if (raw === undefined) {
            return null;
        }

        try {
            return deserialize(String(raw));
        } catch (error) {
            return String(raw);
        }
    }

    function getService(context) {
        return context?.storage ||
            context?.services?.get?.("storage") ||
            context?.services?.storage ||
            null;
    }

    function writeOutput(writer, value, type) {
        if (typeof writer === "function") {
            return writer(value, type);
        }
        return value;
    }

    function initialize(context = {}) {
        const dataset = context.root?.dataset || {};
        const namespace =
            dataset.terminalStorageNamespace ||
            dataset.storageNamespace ||
            context.config?.storage?.namespace ||
            DEFAULT_NAMESPACE;

        const service = new StorageService({
            context,
            namespace,
            version:
                dataset.terminalStorageVersion ||
                context.config?.storage?.version ||
                DEFAULT_VERSION,
            backend:
                dataset.terminalStorageBackend ||
                context.config?.storage?.backend ||
                DEFAULT_BACKEND,
            defaultTTL:
                dataset.terminalStorageTtl ||
                context.config?.storage?.defaultTTL ||
                0,
            fallbackToMemory: parseBoolean(
                dataset.terminalStorageFallback,
                context.config?.storage?.fallbackToMemory !== false
            ),
            crossTab: parseBoolean(
                dataset.terminalStorageSync,
                context.config?.storage?.crossTab !== false
            ),
            autoPrune: parseBoolean(
                dataset.terminalStorageAutoPrune,
                context.config?.storage?.autoPrune !== false
            )
        });

        context.storage = service;
        context.registerService?.("storage", service);

        safeDispatch(document, "speciedex:terminal-storage-ready", {
            service,
            namespace: service.namespace,
            backend: service.backend.name
        });

        return service;
    }

    const commands = [{
        name: "storage",
        aliases: ["store"],
        category: "system",
        description: "Inspect and manage terminal-local namespaced storage.",
        usage:
            "storage [status|list|get|set|delete|clear|export|import|prune|estimate] " +
            "[key] [value] [--ttl=5m] [--prefix=name] [--json]",
        handler: async ({
            args = [],
            context,
            writeJSON,
            write,
            writeError
        }) => {
            const storage = getService(context);

            if (!storage) {
                throw new Error("Storage service is unavailable.");
            }

            const parsed = parseArguments(args);
            const action = parsed.action;
            const positional = parsed.positional;
            const options = parsed.options;

            try {
                switch (action) {
                    case "status":
                    case "show":
                    case "info":
                        return writeJSON(storage.status());

                    case "list":
                    case "keys": {
                        const entries = storage.entries({
                            prefix: options.prefix || positional[0] || "",
                            includeExpired: parseBoolean(
                                options["include-expired"],
                                false
                            )
                        });

                        return writeJSON({
                            namespace: storage.namespace,
                            count: entries.length,
                            entries
                        });
                    }

                    case "get": {
                        const key = positional[0];

                        if (!key) {
                            throw new Error("Usage: storage get <key>");
                        }

                        const entry = storage.getEntry(key, {
                            includeExpired: parseBoolean(
                                options["include-expired"],
                                false
                            )
                        });

                        if (!entry) {
                            return writeOutput(
                                write,
                                `Storage key not found: ${key}`,
                                "warning"
                            );
                        }

                        return writeJSON(entry);
                    }

                    case "set": {
                        const key = positional.shift();

                        if (!key) {
                            throw new Error(
                                "Usage: storage set <key> <JSON-or-text> [--ttl=5m]"
                            );
                        }

                        const raw = positional.join(" ");
                        const value = parseInputValue(raw, {
                            string: options.string === true
                        });

                        storage.set(key, value, {
                            ttl: parseDuration(options.ttl, 0),
                            allowUndefined: true
                        });

                        return writeJSON(storage.getEntry(key));
                    }

                    case "delete":
                    case "remove":
                    case "rm": {
                        const key = positional[0];

                        if (!key) {
                            throw new Error("Usage: storage delete <key>");
                        }

                        const deleted = storage.delete(key);

                        return writeOutput(
                            write,
                            deleted
                                ? `Deleted storage key: ${key}`
                                : `Storage key not found: ${key}`,
                            deleted ? "success" : "warning"
                        );
                    }

                    case "clear": {
                        const count = storage.clear({
                            prefix: options.prefix || positional[0] || ""
                        });

                        return writeOutput(
                            write,
                            `Cleared ${count} terminal storage entr${count === 1 ? "y" : "ies"}.`,
                            "success"
                        );
                    }

                    case "prune": {
                        const removed = storage.pruneExpired();
                        return writeJSON({
                            removed: removed.length,
                            keys: removed
                        });
                    }

                    case "export": {
                        const exported = storage.export({
                            stringify: options.json !== true,
                            withMetadata: options.metadata === true,
                            includeExpired: options["include-expired"] === true
                        });

                        return typeof exported === "string"
                            ? writeOutput(write, exported, "data")
                            : writeJSON(exported);
                    }

                    case "import": {
                        const raw = positional.join(" ");

                        if (!raw) {
                            throw new Error(
                                "Usage: storage import <JSON> [--replace]"
                            );
                        }

                        return writeJSON(storage.import(raw, {
                            replace: options.replace === true,
                            strict: options.strict === true
                        }));
                    }

                    case "estimate":
                    case "quota":
                        return writeJSON(await storage.estimate());

                    case "has": {
                        const key = positional[0];

                        if (!key) {
                            throw new Error("Usage: storage has <key>");
                        }

                        return writeJSON({
                            key,
                            exists: storage.has(key)
                        });
                    }

                    default:
                        throw new Error(
                            `Unknown storage action "${action}". ` +
                            "Use status, list, get, set, delete, clear, export, " +
                            "import, prune, or estimate."
                        );
                }
            } catch (error) {
                if (typeof writeError === "function") {
                    writeError(error.message);
                    return null;
                }
                throw error;
            }
        }
    }];

    const api = Object.freeze({
        name: MODULE_NAME,
        StorageService,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalStorage = api;
    window.SpeciedexTerminalModules = window.SpeciedexTerminalModules || {};
    window.SpeciedexTerminalModules[MODULE_NAME] = api;

    document.dispatchEvent(
        new CustomEvent("speciedex:terminal-module-available", {
            detail: {
                name: MODULE_NAME,
                module: api
            }
        })
    );
})(window, document);
