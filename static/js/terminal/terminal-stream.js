/*
========================================================================
Speciedex.org
Terminal Stream Module
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Stream";
    const DEFAULT_BUFFER_LIMIT = 1000;
    const DEFAULT_RECONNECT_DELAY = 1000;
    const DEFAULT_MAX_RECONNECT_DELAY = 30000;
    const DEFAULT_HEARTBEAT_TIMEOUT = 45000;
    const DEFAULT_TRANSPORT = "auto";

    function now() {
        return Date.now();
    }

    function iso(timestamp = now()) {
        return new Date(timestamp).toISOString();
    }

    function clone(value) {
        if (typeof structuredClone === "function") {
            try {
                return structuredClone(value);
            } catch (error) {
                /* Fall through. */
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

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
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

    function parseNumber(value, fallback = 0, minimum = -Infinity, maximum = Infinity) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return fallback;
        }

        return Math.min(maximum, Math.max(minimum, number));
    }

    function parseDuration(value, fallback = 0) {
        if (typeof value === "number" && Number.isFinite(value)) {
            return Math.max(0, value);
        }

        if (value === undefined || value === null || value === "") {
            return fallback;
        }

        const match = String(value)
            .trim()
            .toLowerCase()
            .match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|h)?$/);

        if (!match) {
            return fallback;
        }

        const amount = Number(match[1]);
        const multipliers = {
            ms: 1,
            s: 1000,
            m: 60000,
            h: 3600000
        };

        return Math.round(amount * multipliers[match[2] || "ms"]);
    }

    function safeDispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, { detail }));
        } catch (error) {
            /* Event propagation must never interrupt streaming. */
        }
    }

    function normalizeTransport(value) {
        const transport = String(value || DEFAULT_TRANSPORT).trim().toLowerCase();

        if (["auto", "sse", "eventsource", "websocket", "ws", "fetch", "ndjson"].includes(transport)) {
            if (transport === "eventsource") {
                return "sse";
            }
            if (transport === "ws") {
                return "websocket";
            }
            if (transport === "ndjson") {
                return "fetch";
            }
            return transport;
        }

        return DEFAULT_TRANSPORT;
    }

    function normalizeURL(value, base = document.baseURI) {
        if (!value) {
            return "";
        }

        try {
            return new URL(String(value), base).href;
        } catch (error) {
            throw new TypeError(`Invalid stream URL: ${value}`);
        }
    }

    function parsePayload(value) {
        if (typeof value !== "string") {
            return value;
        }

        const text = value.trim();

        if (!text) {
            return null;
        }

        try {
            return JSON.parse(text);
        } catch (error) {
            return value;
        }
    }

    function flattenArguments(args = []) {
        const parsed = {
            action: "status",
            positional: [],
            options: {}
        };

        for (const argument of args) {
            const value = String(argument);

            if (value.startsWith("--")) {
                const [key, ...rest] = value.slice(2).split("=");
                parsed.options[key] = rest.length ? rest.join("=") : true;
            } else {
                parsed.positional.push(value);
            }
        }

        if (parsed.positional.length) {
            parsed.action = parsed.positional.shift().toLowerCase();
        }

        return parsed;
    }

    class RingBuffer {
        constructor(limit = DEFAULT_BUFFER_LIMIT) {
            this.limit = Math.max(1, Number(limit) || DEFAULT_BUFFER_LIMIT);
            this.items = [];
        }

        push(value) {
            this.items.push(value);

            if (this.items.length > this.limit) {
                this.items.splice(0, this.items.length - this.limit);
            }

            return value;
        }

        clear() {
            const count = this.items.length;
            this.items.length = 0;
            return count;
        }

        toArray() {
            return this.items.map(clone);
        }

        get length() {
            return this.items.length;
        }
    }

    class StreamService extends EventTarget {
        constructor(context = {}, options = {}) {
            super();

            this.context = context;
            this.options = {
                url: normalizeURL(options.url || "", document.baseURI),
                transport: normalizeTransport(options.transport),
                autoReconnect: options.autoReconnect !== false,
                reconnectDelay: parseDuration(
                    options.reconnectDelay,
                    DEFAULT_RECONNECT_DELAY
                ),
                maxReconnectDelay: parseDuration(
                    options.maxReconnectDelay,
                    DEFAULT_MAX_RECONNECT_DELAY
                ),
                heartbeatTimeout: parseDuration(
                    options.heartbeatTimeout,
                    DEFAULT_HEARTBEAT_TIMEOUT
                ),
                bufferLimit: parseNumber(
                    options.bufferLimit,
                    DEFAULT_BUFFER_LIMIT,
                    1,
                    100000
                ),
                credentials: options.credentials || "same-origin",
                headers: isObject(options.headers) ? { ...options.headers } : {},
                protocols: Array.isArray(options.protocols)
                    ? [...options.protocols]
                    : options.protocols
                        ? [String(options.protocols)]
                        : [],
                parse: options.parse !== false
            };

            this.buffer = new RingBuffer(this.options.bufferLimit);
            this.subscribers = new Set();
            this.filters = new Map();
            this.transport = null;
            this.abortController = null;
            this.reconnectTimer = null;
            this.heartbeatTimer = null;
            this.destroyed = false;
            this.manualClose = false;
            this.reconnectAttempts = 0;
            this.lastError = null;
            this.lastRecord = null;
            this.lastMessageAt = null;
            this.startedAt = null;
            this.connectedAt = null;
            this.disconnectedAt = null;
            this.sequence = 0;

            this.metrics = {
                received: 0,
                accepted: 0,
                rejected: 0,
                bytes: 0,
                reconnects: 0,
                errors: 0,
                opens: 0,
                closes: 0,
                rate: 0,
                peakRate: 0
            };

            this.rateWindow = [];
            this._boundOnline = this._handleOnline.bind(this);
            this._boundOffline = this._handleOffline.bind(this);

            window.addEventListener("online", this._boundOnline);
            window.addEventListener("offline", this._boundOffline);

            this._syncState();
        }

        _assertActive() {
            if (this.destroyed) {
                throw new Error("Stream service has been destroyed.");
            }
        }

        _emit(type, detail = {}) {
            const event = {
                type,
                timestamp: iso(),
                ...detail
            };

            safeDispatch(this, type, event);
            safeDispatch(this, "change", event);

            try {
                this.context.events?.emit?.(`stream:${type}`, event);
            } catch (error) {
                this._recordError(error);
            }

            return event;
        }

        _recordError(error, phase = "runtime") {
            this.lastError = error instanceof Error
                ? error
                : new Error(String(error));
            this.metrics.errors += 1;

            this._emit("error", {
                phase,
                error: {
                    name: this.lastError.name,
                    message: this.lastError.message,
                    stack: this.lastError.stack || ""
                }
            });

            this._syncState();
        }

        _resolveTransport(url = this.options.url, requested = this.options.transport) {
            const transport = normalizeTransport(requested);

            if (transport !== "auto") {
                return transport;
            }

            const parsed = new URL(url, document.baseURI);

            if (["ws:", "wss:"].includes(parsed.protocol)) {
                return "websocket";
            }

            if (typeof EventSource === "function") {
                return "sse";
            }

            return "fetch";
        }

        _syncState() {
            const state = this.context.state || this.context.stateStore;
            const snapshot = this.status();

            try {
                state?.set?.("stream", {
                    ...(state.get?.("stream", {}) || {}),
                    connected: snapshot.connected,
                    connecting: snapshot.connecting,
                    state: snapshot.state,
                    transport: snapshot.transport,
                    url: snapshot.url,
                    records: snapshot.metrics.accepted,
                    received: snapshot.metrics.received,
                    rejected: snapshot.metrics.rejected,
                    rate: snapshot.metrics.rate,
                    buffered: snapshot.buffered,
                    reconnectAttempts: snapshot.reconnectAttempts,
                    lastRecord: clone(snapshot.lastRecord),
                    lastMessageAt: snapshot.lastMessageAt,
                    lastError: snapshot.lastError
                });
            } catch (error) {
                /* State synchronization is advisory. */
            }
        }

        _setHeartbeat() {
            clearTimeout(this.heartbeatTimer);
            this.heartbeatTimer = null;

            if (!this.options.heartbeatTimeout) {
                return;
            }

            this.heartbeatTimer = window.setTimeout(() => {
                this._recordError(
                    new Error("Stream heartbeat timeout."),
                    "heartbeat"
                );
                this._closeTransport();
                this._scheduleReconnect();
            }, this.options.heartbeatTimeout);
        }

        _recordRate() {
            const timestamp = now();
            this.rateWindow.push(timestamp);

            const cutoff = timestamp - 10000;
            while (this.rateWindow.length && this.rateWindow[0] < cutoff) {
                this.rateWindow.shift();
            }

            const elapsed = this.rateWindow.length > 1
                ? Math.max(1, timestamp - this.rateWindow[0])
                : 1000;

            this.metrics.rate = Number(
                ((this.rateWindow.length / elapsed) * 1000).toFixed(3)
            );
            this.metrics.peakRate = Math.max(
                this.metrics.peakRate,
                this.metrics.rate
            );
        }

        _applyFilters(record) {
            for (const [name, filter] of this.filters) {
                try {
                    if (!filter(record, this)) {
                        return {
                            accepted: false,
                            filter: name
                        };
                    }
                } catch (error) {
                    this._recordError(error, `filter:${name}`);
                    return {
                        accepted: false,
                        filter: name
                    };
                }
            }

            return {
                accepted: true,
                filter: null
            };
        }

        _ingest(payload, metadata = {}) {
            const raw = payload;
            const record = this.options.parse ? parsePayload(payload) : payload;
            const size = typeof raw === "string"
                ? new Blob([raw]).size
                : new Blob([JSON.stringify(raw ?? null)]).size;

            this.metrics.received += 1;
            this.metrics.bytes += size;
            this.lastMessageAt = iso();
            this._setHeartbeat();
            this._recordRate();

            const decision = this._applyFilters(record);

            if (!decision.accepted) {
                this.metrics.rejected += 1;
                this._emit("reject", {
                    record: clone(record),
                    filter: decision.filter,
                    metadata: clone(metadata)
                });
                this._syncState();
                return null;
            }

            const entry = {
                sequence: ++this.sequence,
                receivedAt: this.lastMessageAt,
                record: clone(record),
                metadata: clone(metadata)
            };

            this.metrics.accepted += 1;
            this.lastRecord = entry;
            this.buffer.push(entry);

            for (const subscriber of Array.from(this.subscribers)) {
                try {
                    subscriber(clone(entry), this);
                } catch (error) {
                    this._recordError(error, "subscriber");
                }
            }

            this._emit("record", clone(entry));
            this._syncState();
            return entry;
        }

        async _openFetch(url) {
            this.abortController = new AbortController();

            const response = await fetch(url, {
                method: "GET",
                headers: {
                    Accept: "application/x-ndjson, application/json, text/event-stream, text/plain",
                    ...this.options.headers
                },
                credentials: this.options.credentials,
                cache: "no-store",
                signal: this.abortController.signal
            });

            if (!response.ok) {
                throw new Error(
                    `Stream request failed with HTTP ${response.status}.`
                );
            }

            if (!response.body || typeof response.body.getReader !== "function") {
                const text = await response.text();
                for (const line of text.split(/\r?\n/)) {
                    if (line.trim()) {
                        this._ingest(line, {
                            transport: "fetch",
                            url
                        });
                    }
                }
                return;
            }

            this._handleOpen("fetch", url);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let pending = "";

            while (!this.manualClose && !this.destroyed) {
                const result = await reader.read();

                if (result.done) {
                    break;
                }

                pending += decoder.decode(result.value, { stream: true });
                const lines = pending.split(/\r?\n/);
                pending = lines.pop() || "";

                for (let line of lines) {
                    line = line.trim();

                    if (!line || line.startsWith(":")) {
                        continue;
                    }

                    if (line.startsWith("data:")) {
                        line = line.slice(5).trim();
                    }

                    if (line) {
                        this._ingest(line, {
                            transport: "fetch",
                            url
                        });
                    }
                }
            }

            pending += decoder.decode();

            if (pending.trim()) {
                this._ingest(pending.trim(), {
                    transport: "fetch",
                    url
                });
            }

            if (!this.manualClose) {
                this._handleClose("fetch", url);
                this._scheduleReconnect();
            }
        }

        _openSSE(url) {
            const source = new EventSource(url, {
                withCredentials: this.options.credentials === "include"
            });

            this.transport = source;

            source.onopen = () => this._handleOpen("sse", url);

            source.onmessage = (event) => {
                this._ingest(event.data, {
                    transport: "sse",
                    url,
                    eventId: event.lastEventId || null,
                    origin: event.origin || null
                });
            };

            source.onerror = () => {
                if (source.readyState === EventSource.CLOSED) {
                    this._handleClose("sse", url);
                    this._scheduleReconnect();
                } else {
                    this._recordError(
                        new Error("Server-Sent Events stream error."),
                        "sse"
                    );
                }
            };
        }

        _openWebSocket(url) {
            const protocols = this.options.protocols.length
                ? this.options.protocols
                : undefined;
            const socket = new WebSocket(url, protocols);

            this.transport = socket;

            socket.addEventListener("open", () => {
                this._handleOpen("websocket", url);
            });

            socket.addEventListener("message", (event) => {
                if (event.data instanceof Blob) {
                    event.data.text()
                        .then((text) => this._ingest(text, {
                            transport: "websocket",
                            url,
                            binary: true
                        }))
                        .catch((error) => this._recordError(error, "websocket-blob"));
                    return;
                }

                this._ingest(event.data, {
                    transport: "websocket",
                    url,
                    binary: event.data instanceof ArrayBuffer
                });
            });

            socket.addEventListener("error", () => {
                this._recordError(
                    new Error("WebSocket stream error."),
                    "websocket"
                );
            });

            socket.addEventListener("close", (event) => {
                this._handleClose("websocket", url, {
                    code: event.code,
                    reason: event.reason,
                    clean: event.wasClean
                });

                if (!this.manualClose) {
                    this._scheduleReconnect();
                }
            });
        }

        _handleOpen(transport, url) {
            this.connectedAt = iso();
            this.disconnectedAt = null;
            this.reconnectAttempts = 0;
            this.metrics.opens += 1;
            this._setHeartbeat();

            this._emit("open", {
                transport,
                url
            });

            this._syncState();
        }

        _handleClose(transport, url, details = {}) {
            clearTimeout(this.heartbeatTimer);
            this.heartbeatTimer = null;
            this.disconnectedAt = iso();
            this.metrics.closes += 1;

            this._emit("close", {
                transport,
                url,
                ...details
            });

            this._syncState();
        }

        _closeTransport() {
            clearTimeout(this.heartbeatTimer);
            this.heartbeatTimer = null;

            if (this.abortController) {
                try {
                    this.abortController.abort();
                } catch (error) {
                    /* Ignore abort failures. */
                }
                this.abortController = null;
            }

            if (this.transport) {
                try {
                    this.transport.close();
                } catch (error) {
                    /* Ignore transport close failures. */
                }
                this.transport = null;
            }
        }

        _scheduleReconnect() {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;

            if (
                this.manualClose ||
                this.destroyed ||
                !this.options.autoReconnect ||
                navigator.onLine === false
            ) {
                return;
            }

            this.reconnectAttempts += 1;
            this.metrics.reconnects += 1;

            const exponential = this.options.reconnectDelay *
                Math.pow(2, Math.max(0, this.reconnectAttempts - 1));
            const delay = Math.min(
                this.options.maxReconnectDelay,
                exponential
            );
            const jitter = Math.round(delay * 0.2 * Math.random());
            const scheduledDelay = delay + jitter;

            this._emit("reconnect", {
                attempt: this.reconnectAttempts,
                delay: scheduledDelay
            });

            this.reconnectTimer = window.setTimeout(() => {
                this.connect().catch((error) => {
                    this._recordError(error, "reconnect");
                    this._scheduleReconnect();
                });
            }, scheduledDelay);

            this._syncState();
        }

        _handleOnline() {
            this._emit("online", { online: true });

            if (!this.manualClose && !this.isConnected() && this.options.url) {
                this.connect().catch((error) => {
                    this._recordError(error, "online-reconnect");
                });
            }
        }

        _handleOffline() {
            this._emit("offline", { online: false });
            this._closeTransport();
            this._syncState();
        }

        isConnected() {
            if (this.abortController) {
                return Boolean(this.connectedAt && !this.disconnectedAt);
            }

            if (this.transport instanceof EventSource) {
                return this.transport.readyState === EventSource.OPEN;
            }

            if (this.transport instanceof WebSocket) {
                return this.transport.readyState === WebSocket.OPEN;
            }

            return false;
        }

        isConnecting() {
            if (this.transport instanceof EventSource) {
                return this.transport.readyState === EventSource.CONNECTING;
            }

            if (this.transport instanceof WebSocket) {
                return this.transport.readyState === WebSocket.CONNECTING;
            }

            return Boolean(this.abortController && !this.connectedAt);
        }

        async connect(options = {}) {
            this._assertActive();

            if (isObject(options)) {
                if (options.url !== undefined) {
                    this.options.url = normalizeURL(options.url, document.baseURI);
                }

                if (options.transport !== undefined) {
                    this.options.transport = normalizeTransport(options.transport);
                }

                if (options.autoReconnect !== undefined) {
                    this.options.autoReconnect = Boolean(options.autoReconnect);
                }

                if (options.headers && isObject(options.headers)) {
                    this.options.headers = {
                        ...this.options.headers,
                        ...options.headers
                    };
                }
            }

            if (!this.options.url) {
                throw new Error("A stream URL is required.");
            }

            if (this.isConnected() || this.isConnecting()) {
                return this.status();
            }

            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
            this.manualClose = false;
            this.startedAt = this.startedAt || iso();
            this.connectedAt = null;
            this.disconnectedAt = null;
            this.lastError = null;

            const transport = this._resolveTransport(
                this.options.url,
                this.options.transport
            );

            this._emit("connecting", {
                transport,
                url: this.options.url
            });

            this._syncState();

            if (transport === "sse") {
                this._openSSE(this.options.url);
                return this.status();
            }

            if (transport === "websocket") {
                this._openWebSocket(this.options.url);
                return this.status();
            }

            try {
                await this._openFetch(this.options.url);
            } catch (error) {
                if (error?.name !== "AbortError") {
                    this._recordError(error, "fetch");
                    this._scheduleReconnect();
                    throw error;
                }
            }

            return this.status();
        }

        disconnect(reason = "manual") {
            this._assertActive();

            this.manualClose = true;
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
            this._closeTransport();
            this.disconnectedAt = iso();

            this._emit("disconnect", {
                reason
            });

            this._syncState();
            return this.status();
        }

        reconnect() {
            this.disconnect("reconnect");
            this.manualClose = false;
            return this.connect();
        }

        send(payload) {
            this._assertActive();

            if (!(this.transport instanceof WebSocket)) {
                throw new Error("Sending is only supported for WebSocket streams.");
            }

            if (this.transport.readyState !== WebSocket.OPEN) {
                throw new Error("WebSocket stream is not connected.");
            }

            const data = typeof payload === "string"
                ? payload
                : JSON.stringify(payload);

            this.transport.send(data);

            this._emit("send", {
                bytes: new Blob([data]).size
            });

            return true;
        }

        subscribe(callback, options = {}) {
            if (typeof callback !== "function") {
                throw new TypeError("Stream subscriber must be a function.");
            }

            this.subscribers.add(callback);

            if (options.replay === true) {
                const count = parseNumber(
                    options.limit,
                    this.buffer.length,
                    0,
                    this.buffer.length
                );

                for (const entry of this.buffer.toArray().slice(-count)) {
                    callback(entry, this);
                }
            }

            return () => this.unsubscribe(callback);
        }

        unsubscribe(callback) {
            return this.subscribers.delete(callback);
        }

        addFilter(name, callback) {
            if (typeof callback !== "function") {
                throw new TypeError("Stream filter must be a function.");
            }

            this.filters.set(String(name || `filter-${this.filters.size + 1}`), callback);
            return this;
        }

        removeFilter(name) {
            return this.filters.delete(String(name));
        }

        clearFilters() {
            const count = this.filters.size;
            this.filters.clear();
            return count;
        }

        clearBuffer() {
            const count = this.buffer.clear();

            this._emit("bufferClear", {
                count
            });

            this._syncState();
            return count;
        }

        records(options = {}) {
            let records = this.buffer.toArray();

            if (options.since) {
                const since = new Date(options.since).getTime();
                records = records.filter((entry) => {
                    return new Date(entry.receivedAt).getTime() >= since;
                });
            }

            const limit = parseNumber(
                options.limit,
                records.length,
                0,
                records.length
            );

            return limit ? records.slice(-limit) : [];
        }

        inject(payload, metadata = {}) {
            this._assertActive();

            return this._ingest(payload, {
                transport: "injected",
                ...metadata
            });
        }

        configure(options = {}) {
            this._assertActive();

            if (!isObject(options)) {
                throw new TypeError("Stream configuration must be an object.");
            }

            if (options.url !== undefined) {
                this.options.url = normalizeURL(options.url, document.baseURI);
            }

            if (options.transport !== undefined) {
                this.options.transport = normalizeTransport(options.transport);
            }

            if (options.autoReconnect !== undefined) {
                this.options.autoReconnect = Boolean(options.autoReconnect);
            }

            if (options.reconnectDelay !== undefined) {
                this.options.reconnectDelay = parseDuration(
                    options.reconnectDelay,
                    this.options.reconnectDelay
                );
            }

            if (options.maxReconnectDelay !== undefined) {
                this.options.maxReconnectDelay = parseDuration(
                    options.maxReconnectDelay,
                    this.options.maxReconnectDelay
                );
            }

            if (options.heartbeatTimeout !== undefined) {
                this.options.heartbeatTimeout = parseDuration(
                    options.heartbeatTimeout,
                    this.options.heartbeatTimeout
                );
            }

            if (options.headers && isObject(options.headers)) {
                this.options.headers = {
                    ...this.options.headers,
                    ...options.headers
                };
            }

            this._emit("configure", {
                options: clone(this.options)
            });

            this._syncState();
            return this.status();
        }

        resetMetrics() {
            this.metrics = {
                received: 0,
                accepted: 0,
                rejected: 0,
                bytes: 0,
                reconnects: 0,
                errors: 0,
                opens: 0,
                closes: 0,
                rate: 0,
                peakRate: 0
            };
            this.rateWindow.length = 0;
            this.sequence = 0;
            this.lastRecord = null;
            this.lastMessageAt = null;
            this.lastError = null;

            this._emit("metricsReset", {});
            this._syncState();
            return this.status();
        }

        status() {
            let state = "idle";

            if (this.destroyed) {
                state = "destroyed";
            } else if (this.isConnected()) {
                state = "connected";
            } else if (this.isConnecting()) {
                state = "connecting";
            } else if (this.reconnectTimer) {
                state = "reconnecting";
            } else if (this.disconnectedAt) {
                state = "disconnected";
            }

            return {
                name: "stream",
                module: MODULE_NAME,
                state,
                connected: state === "connected",
                connecting: state === "connecting",
                online: navigator.onLine !== false,
                url: this.options.url || null,
                transport: this.options.url
                    ? this._resolveTransport(
                        this.options.url,
                        this.options.transport
                    )
                    : this.options.transport,
                autoReconnect: this.options.autoReconnect,
                reconnectAttempts: this.reconnectAttempts,
                buffered: this.buffer.length,
                bufferLimit: this.buffer.limit,
                subscribers: this.subscribers.size,
                filters: Array.from(this.filters.keys()),
                startedAt: this.startedAt,
                connectedAt: this.connectedAt,
                disconnectedAt: this.disconnectedAt,
                lastMessageAt: this.lastMessageAt,
                lastRecord: clone(this.lastRecord),
                metrics: { ...this.metrics },
                lastError: this.lastError
                    ? {
                        name: this.lastError.name,
                        message: this.lastError.message
                    }
                    : null,
                destroyed: this.destroyed
            };
        }

        async run(parameters = {}) {
            const args = Array.isArray(parameters.args)
                ? parameters.args
                : [];
            const parsed = flattenArguments(args);

            switch (parsed.action) {
                case "connect":
                case "start":
                    return this.connect({
                        url: parsed.options.url || parsed.positional[0] || this.options.url,
                        transport: parsed.options.transport || this.options.transport,
                        autoReconnect: !parseBoolean(parsed.options["no-reconnect"], false)
                    });

                case "disconnect":
                case "stop":
                    return this.disconnect("command");

                case "reconnect":
                case "restart":
                    return this.reconnect();

                case "records":
                case "buffer":
                    return {
                        count: this.buffer.length,
                        records: this.records({
                            limit: parseNumber(
                                parsed.options.limit || parsed.positional[0],
                                this.buffer.length,
                                0,
                                this.buffer.length
                            ),
                            since: parsed.options.since
                        })
                    };

                case "clear":
                    return {
                        cleared: this.clearBuffer()
                    };

                case "inject":
                    return this.inject(
                        parsePayload(parsed.positional.join(" "))
                    );

                case "reset":
                    return this.resetMetrics();

                case "config":
                case "configure":
                    return this.configure({
                        url: parsed.options.url,
                        transport: parsed.options.transport,
                        autoReconnect: parsed.options.reconnect === undefined
                            ? undefined
                            : parseBoolean(parsed.options.reconnect, true),
                        reconnectDelay: parsed.options.delay,
                        maxReconnectDelay: parsed.options["max-delay"],
                        heartbeatTimeout: parsed.options.heartbeat
                    });

                case "status":
                case "show":
                case "info":
                default:
                    return this.status();
            }
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.manualClose = true;
            clearTimeout(this.reconnectTimer);
            clearTimeout(this.heartbeatTimer);
            this.reconnectTimer = null;
            this.heartbeatTimer = null;
            this._closeTransport();

            window.removeEventListener("online", this._boundOnline);
            window.removeEventListener("offline", this._boundOffline);

            this.subscribers.clear();
            this.filters.clear();
            this.destroyed = true;

            this._emit("destroy", {});
            this._syncState();
            return true;
        }
    }

    function getService(context) {
        return context?.stream ||
            context?.services?.get?.("stream") ||
            context?.services?.stream ||
            null;
    }

    function initialize(context = {}) {
        const dataset = context.root?.dataset || {};
        const config = context.config?.stream || {};

        const service = new StreamService(context, {
            url:
                dataset.terminalStreamUrl ||
                dataset.streamUrl ||
                config.url ||
                "",
            transport:
                dataset.terminalStreamTransport ||
                config.transport ||
                DEFAULT_TRANSPORT,
            autoReconnect: parseBoolean(
                dataset.terminalStreamReconnect,
                config.autoReconnect !== false
            ),
            reconnectDelay:
                dataset.terminalStreamReconnectDelay ||
                config.reconnectDelay ||
                DEFAULT_RECONNECT_DELAY,
            maxReconnectDelay:
                dataset.terminalStreamMaxReconnectDelay ||
                config.maxReconnectDelay ||
                DEFAULT_MAX_RECONNECT_DELAY,
            heartbeatTimeout:
                dataset.terminalStreamHeartbeat ||
                config.heartbeatTimeout ||
                DEFAULT_HEARTBEAT_TIMEOUT,
            bufferLimit:
                dataset.terminalStreamBuffer ||
                config.bufferLimit ||
                DEFAULT_BUFFER_LIMIT,
            credentials:
                dataset.terminalStreamCredentials ||
                config.credentials ||
                "same-origin",
            headers: config.headers || {},
            protocols: config.protocols || [],
            parse: parseBoolean(
                dataset.terminalStreamParse,
                config.parse !== false
            )
        });

        context.stream = service;
        context.registerService?.("stream", service);

        safeDispatch(document, "speciedex:terminal-stream-ready", {
            service,
            status: service.status()
        });

        if (
            parseBoolean(dataset.terminalStreamAutostart, config.autostart === true) &&
            service.options.url
        ) {
            service.connect().catch((error) => {
                service._recordError(error, "autostart");
            });
        }

        return service;
    }

    const commands = [{
        name: "stream",
        aliases: ["streams"],
        category: "data",
        description: "Consume, inspect, and manage incremental Speciedex data streams.",
        usage:
            "stream [status|connect|disconnect|reconnect|records|clear|inject|reset|config] " +
            "[url] [--transport=auto|sse|websocket|fetch] [--limit=100]",
        handler: async ({
            args = [],
            context,
            writeJSON,
            write,
            writeError
        }) => {
            const service = getService(context);

            if (!service) {
                throw new Error("Stream service is unavailable.");
            }

            try {
                const result = await service.run({ args });

                if (
                    result &&
                    typeof result === "object" &&
                    typeof writeJSON === "function"
                ) {
                    return writeJSON(result);
                }

                if (typeof write === "function") {
                    return write(String(result ?? ""), "data");
                }

                return result;
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
        StreamService,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalStream = api;
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
