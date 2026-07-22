/*
========================================================================
Speciedex.org
Terminal Status Bar
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Statusbar";
    const SERVICE_NAME = "statusbar";
    const DEFAULT_VERSION = "1.0.0";
    const DEFAULT_PROVIDER = "none";
    const DEFAULT_NETWORK = "offline";
    const DEFAULT_RECORDS = 0;

    const ELEMENT_ALIASES = Object.freeze({
        root: ["statusbar", "statusBar", "terminalStatusbar", "terminalStatusBar"],
        provider: ["provider", "providerName", "activeProvider", "statusProvider"],
        records: ["recordCount", "records", "recordTotal", "statusRecords"],
        network: ["networkStatus", "network", "connectionStatus", "statusNetwork"],
        version: ["version", "appVersion", "terminalVersion", "statusVersion"],
        activity: ["activity", "statusActivity", "operation", "currentOperation"],
        latency: ["latency", "networkLatency", "statusLatency"],
        clock: ["clock", "statusClock", "terminalClock"],
        progress: ["progress", "statusProgress", "terminalProgress"]
    });

    const SELECTORS = Object.freeze({
        root: [
            "[data-terminal-statusbar]",
            "#terminal-statusbar",
            ".terminal-statusbar",
            ".statusbar"
        ],
        provider: [
            "[data-status-provider]",
            "#status-provider",
            "#provider-status",
            "[data-terminal-provider]"
        ],
        records: [
            "[data-status-records]",
            "#status-records",
            "#record-count",
            "[data-terminal-record-count]"
        ],
        network: [
            "[data-status-network]",
            "#status-network",
            "#network-status",
            "[data-terminal-network-status]"
        ],
        version: [
            "[data-status-version]",
            "#status-version",
            "#terminal-version",
            "[data-terminal-version]"
        ],
        activity: [
            "[data-status-activity]",
            "#status-activity",
            "#terminal-activity"
        ],
        latency: [
            "[data-status-latency]",
            "#status-latency",
            "#network-latency"
        ],
        clock: [
            "[data-status-clock]",
            "#status-clock",
            "#terminal-clock"
        ],
        progress: [
            "[data-status-progress]",
            "#status-progress",
            "progress[data-terminal-progress]"
        ]
    });

    const DEFAULT_STATE = Object.freeze({
        provider: DEFAULT_PROVIDER,
        records: DEFAULT_RECORDS,
        network: DEFAULT_NETWORK,
        version: DEFAULT_VERSION,
        activity: "idle",
        latency: null,
        progress: null,
        online: false,
        busy: false,
        updatedAt: null
    });

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function clone(value) {
        if (typeof structuredClone === "function") {
            try {
                return structuredClone(value);
            } catch (error) {
                // Fall through to the conservative clone below.
            }
        }

        if (Array.isArray(value)) {
            return value.map(clone);
        }

        if (isObject(value)) {
            const output = {};
            for (const key of Object.keys(value)) {
                output[key] = clone(value[key]);
            }
            return output;
        }

        return value;
    }

    function normalizeString(value, fallback = "") {
        if (value === null || value === undefined) {
            return fallback;
        }

        const text = String(value).trim();
        return text || fallback;
    }

    function normalizeNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function normalizeBoolean(value, fallback = false) {
        if (typeof value === "boolean") {
            return value;
        }

        if (typeof value === "number") {
            return value !== 0;
        }

        if (typeof value === "string") {
            const normalized = value.trim().toLowerCase();
            if (["true", "yes", "on", "online", "connected", "1"].includes(normalized)) {
                return true;
            }
            if (["false", "no", "off", "offline", "disconnected", "0"].includes(normalized)) {
                return false;
            }
        }

        return fallback;
    }

    function formatInteger(value) {
        const number = Math.max(0, Math.trunc(normalizeNumber(value, 0)));
        try {
            return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(number);
        } catch (error) {
            return String(number);
        }
    }

    function formatLatency(value) {
        if (value === null || value === undefined || value === "") {
            return "—";
        }

        const milliseconds = normalizeNumber(value, NaN);
        if (!Number.isFinite(milliseconds) || milliseconds < 0) {
            return "—";
        }

        if (milliseconds < 1) {
            return "<1 ms";
        }

        if (milliseconds >= 1000) {
            return `${(milliseconds / 1000).toFixed(milliseconds >= 10000 ? 0 : 1)} s`;
        }

        return `${Math.round(milliseconds)} ms`;
    }

    function formatClock(date = new Date()) {
        try {
            return new Intl.DateTimeFormat(undefined, {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            }).format(date);
        } catch (error) {
            return date.toLocaleTimeString();
        }
    }

    function clampProgress(value) {
        if (value === null || value === undefined || value === "") {
            return null;
        }

        const number = normalizeNumber(value, NaN);
        if (!Number.isFinite(number)) {
            return null;
        }

        return Math.min(100, Math.max(0, number));
    }

    function safeDispatch(target, name, detail) {
        if (!target || typeof target.dispatchEvent !== "function") {
            return;
        }

        try {
            target.dispatchEvent(new CustomEvent(name, { detail }));
        } catch (error) {
            // Custom events are non-critical to status rendering.
        }
    }

    class StatusBar extends EventTarget {
        constructor(context = {}, options = {}) {
            super();

            this.context = context;
            this.options = Object.assign({
                autoBind: true,
                autoClock: true,
                clockInterval: 1000,
                observeDOM: true,
                renderOnInitialize: true
            }, isObject(options) ? options : {});

            this.state = Object.assign({}, DEFAULT_STATE, {
                online: typeof navigator !== "undefined" ? Boolean(navigator.onLine) : false,
                network: typeof navigator !== "undefined" && navigator.onLine ? "online" : "offline",
                version: this.resolveInitialVersion()
            });

            this.elements = Object.create(null);
            this.destroyed = false;
            this.bound = false;
            this.renderQueued = false;
            this.clockTimer = null;
            this.observer = null;
            this.cleanup = [];
            this.stateUnsubscribers = [];
            this.lastRendered = Object.create(null);

            this.handleOnline = this.handleOnline.bind(this);
            this.handleOffline = this.handleOffline.bind(this);
            this.handleVisibilityChange = this.handleVisibilityChange.bind(this);
            this.handleStateChange = this.handleStateChange.bind(this);
            this.handleStatsUpdated = this.handleStatsUpdated.bind(this);
            this.handleProviderChanged = this.handleProviderChanged.bind(this);
            this.handleLoadingChanged = this.handleLoadingChanged.bind(this);

            this.resolveElements();

            if (this.options.autoBind) {
                this.bind();
            }

            if (this.options.renderOnInitialize) {
                this.render(true);
            }
        }

        resolveInitialVersion() {
            return normalizeString(
                this.context.version ||
                this.context.config?.version ||
                this.context.manifest?.version ||
                this.context.state?.get?.("runtime.version"),
                DEFAULT_VERSION
            );
        }

        findContextElement(name) {
            const sources = [
                this.context.elements,
                this.context.ui?.elements,
                this.context.dom,
                this.context.refs
            ];

            for (const source of sources) {
                if (!isObject(source)) {
                    continue;
                }

                for (const alias of ELEMENT_ALIASES[name] || []) {
                    const candidate = source[alias];
                    if (candidate && typeof candidate === "object") {
                        return candidate;
                    }
                }
            }

            return null;
        }

        findDocumentElement(name) {
            for (const selector of SELECTORS[name] || []) {
                const element = document.querySelector(selector);
                if (element) {
                    return element;
                }
            }
            return null;
        }

        resolveElements() {
            for (const name of Object.keys(SELECTORS)) {
                this.elements[name] = this.findContextElement(name) || this.findDocumentElement(name);
            }

            return this.elements;
        }

        bind() {
            if (this.bound || this.destroyed) {
                return this;
            }

            this.bound = true;

            window.addEventListener("online", this.handleOnline);
            window.addEventListener("offline", this.handleOffline);
            document.addEventListener("visibilitychange", this.handleVisibilityChange);
            document.addEventListener("speciedex:stats-updated", this.handleStatsUpdated);
            document.addEventListener("speciedex:provider-changed", this.handleProviderChanged);
            document.addEventListener("speciedex:loading-changed", this.handleLoadingChanged);

            this.cleanup.push(() => window.removeEventListener("online", this.handleOnline));
            this.cleanup.push(() => window.removeEventListener("offline", this.handleOffline));
            this.cleanup.push(() => document.removeEventListener("visibilitychange", this.handleVisibilityChange));
            this.cleanup.push(() => document.removeEventListener("speciedex:stats-updated", this.handleStatsUpdated));
            this.cleanup.push(() => document.removeEventListener("speciedex:provider-changed", this.handleProviderChanged));
            this.cleanup.push(() => document.removeEventListener("speciedex:loading-changed", this.handleLoadingChanged));

            this.bindContextEvents();
            this.bindStateStore();

            if (this.options.autoClock) {
                this.startClock();
            }

            if (this.options.observeDOM && typeof MutationObserver === "function") {
                this.observeDOM();
            }

            return this;
        }

        bindContextEvents() {
            const events = this.context.events;
            if (!events) {
                return;
            }

            const subscriptions = [
                ["stats:updated", this.handleStatsUpdated],
                ["provider:changed", this.handleProviderChanged],
                ["loading:changed", this.handleLoadingChanged],
                ["terminal:busy", detail => this.update({ busy: true, activity: detail?.activity || "busy" })],
                ["terminal:idle", () => this.update({ busy: false, activity: "idle", progress: null })],
                ["network:latency", detail => this.update({ latency: detail?.latency ?? detail })]
            ];

            for (const [name, handler] of subscriptions) {
                if (typeof events.on === "function") {
                    const unsubscribe = events.on(name, handler);
                    if (typeof unsubscribe === "function") {
                        this.cleanup.push(unsubscribe);
                    } else if (typeof events.off === "function") {
                        this.cleanup.push(() => events.off(name, handler));
                    }
                } else if (typeof events.addEventListener === "function") {
                    events.addEventListener(name, handler);
                    this.cleanup.push(() => events.removeEventListener(name, handler));
                }
            }
        }

        bindStateStore() {
            const store = this.context.state || this.context.stateStore || this.context.services?.get?.("state");
            if (!store) {
                return;
            }

            if (typeof store.addEventListener === "function") {
                store.addEventListener("change", this.handleStateChange);
                this.stateUnsubscribers.push(() => store.removeEventListener("change", this.handleStateChange));
            }

            if (typeof store.watch === "function") {
                const watches = [
                    ["runtime.online", value => this.update({ online: value, network: value ? "online" : "offline" })],
                    ["runtime.version", value => this.update({ version: value })],
                    ["terminal.busy", value => this.update({ busy: value })],
                    ["terminal.activity", value => this.update({ activity: value })],
                    ["terminal.progress", value => this.update({ progress: value })],
                    ["statistics.records", value => this.update({ records: value })],
                    ["statistics.totalRecords", value => this.update({ records: value })],
                    ["providers.active", value => this.update({ provider: value })],
                    ["providers.current", value => this.update({ provider: value })],
                    ["runtime.latency", value => this.update({ latency: value })]
                ];

                for (const [path, handler] of watches) {
                    const result = store.watch(path, handler);
                    if (typeof result === "function") {
                        this.stateUnsubscribers.push(result);
                    } else if (typeof store.unwatch === "function") {
                        this.stateUnsubscribers.push(() => store.unwatch(path, handler));
                    }
                }
            }

            this.refreshFromState(store);
        }

        refreshFromState(store = this.context.state || this.context.stateStore || this.context.services?.get?.("state")) {
            if (!store || typeof store.get !== "function") {
                return this;
            }

            const records =
                store.get("statistics.totalRecords") ??
                store.get("statistics.records") ??
                store.get("index.documents") ??
                this.state.records;

            const provider =
                store.get("providers.active") ??
                store.get("providers.current") ??
                store.get("library.active") ??
                this.state.provider;

            this.update({
                online: store.get("runtime.online", this.state.online),
                network: store.get("runtime.online", this.state.online) ? "online" : "offline",
                version: store.get("runtime.version", this.state.version),
                busy: store.get("terminal.busy", this.state.busy),
                activity: store.get("terminal.activity", this.state.activity),
                progress: store.get("terminal.progress", this.state.progress),
                latency: store.get("runtime.latency", this.state.latency),
                records,
                provider
            });

            return this;
        }

        handleStateChange(event) {
            const detail = event?.detail || event || {};
            const path = normalizeString(detail.path);
            const value = detail.value;

            const mapping = {
                "runtime.online": () => ({ online: value, network: value ? "online" : "offline" }),
                "runtime.version": () => ({ version: value }),
                "runtime.latency": () => ({ latency: value }),
                "terminal.busy": () => ({ busy: value }),
                "terminal.activity": () => ({ activity: value }),
                "terminal.progress": () => ({ progress: value }),
                "statistics.records": () => ({ records: value }),
                "statistics.totalRecords": () => ({ records: value }),
                "index.documents": () => ({ records: value }),
                "providers.active": () => ({ provider: value }),
                "providers.current": () => ({ provider: value }),
                "library.active": () => ({ provider: value })
            };

            if (mapping[path]) {
                this.update(mapping[path]());
            }
        }

        handleStatsUpdated(event) {
            const detail = event?.detail || event || {};
            this.update({
                records:
                    detail.records ??
                    detail.totalRecords ??
                    detail.statistics?.records ??
                    detail.statistics?.totalRecords ??
                    this.state.records
            });
        }

        handleProviderChanged(event) {
            const detail = event?.detail || event || {};
            this.update({
                provider: detail.provider ?? detail.name ?? detail.id ?? detail
            });
        }

        handleLoadingChanged(event) {
            const detail = event?.detail || event || {};
            this.update({
                busy: detail.busy ?? detail.active ?? this.state.busy,
                activity: detail.activity ?? detail.label ?? detail.message ?? this.state.activity,
                progress: detail.progress ?? detail.percent ?? this.state.progress
            });
        }

        handleOnline() {
            this.update({ online: true, network: "online" });
        }

        handleOffline() {
            this.update({ online: false, network: "offline", latency: null });
        }

        handleVisibilityChange() {
            if (!document.hidden) {
                this.render(true);
            }
        }

        observeDOM() {
            if (this.observer || !document.documentElement) {
                return;
            }

            this.observer = new MutationObserver(() => {
                const previous = Object.assign({}, this.elements);
                this.resolveElements();

                const changed = Object.keys(this.elements).some(name => previous[name] !== this.elements[name]);
                if (changed) {
                    this.render(true);
                }
            });

            this.observer.observe(document.documentElement, {
                childList: true,
                subtree: true
            });
        }

        startClock() {
            this.stopClock();

            const interval = Math.max(250, normalizeNumber(this.options.clockInterval, 1000));
            const tick = () => this.renderClock();

            tick();
            this.clockTimer = window.setInterval(tick, interval);
            return this;
        }

        stopClock() {
            if (this.clockTimer !== null) {
                window.clearInterval(this.clockTimer);
                this.clockTimer = null;
            }
            return this;
        }

        normalize(values = {}) {
            const normalized = {};

            if (Object.prototype.hasOwnProperty.call(values, "provider")) {
                normalized.provider = normalizeString(values.provider, DEFAULT_PROVIDER);
            }

            if (Object.prototype.hasOwnProperty.call(values, "records")) {
                normalized.records = Math.max(0, Math.trunc(normalizeNumber(values.records, 0)));
            }

            if (Object.prototype.hasOwnProperty.call(values, "network")) {
                normalized.network = normalizeString(values.network, DEFAULT_NETWORK).toLowerCase();
            }

            if (Object.prototype.hasOwnProperty.call(values, "version")) {
                normalized.version = normalizeString(values.version, DEFAULT_VERSION);
            }

            if (Object.prototype.hasOwnProperty.call(values, "activity")) {
                normalized.activity = normalizeString(values.activity, "idle");
            }

            if (Object.prototype.hasOwnProperty.call(values, "latency")) {
                const latency = normalizeNumber(values.latency, NaN);
                normalized.latency = Number.isFinite(latency) && latency >= 0 ? latency : null;
            }

            if (Object.prototype.hasOwnProperty.call(values, "progress")) {
                normalized.progress = clampProgress(values.progress);
            }

            if (Object.prototype.hasOwnProperty.call(values, "online")) {
                normalized.online = normalizeBoolean(values.online, false);
            }

            if (Object.prototype.hasOwnProperty.call(values, "busy")) {
                normalized.busy = normalizeBoolean(values.busy, false);
            }

            return normalized;
        }

        update(values = {}, options = {}) {
            if (this.destroyed || !isObject(values)) {
                return this.snapshot();
            }

            const normalized = this.normalize(values);
            const changed = {};

            for (const [key, value] of Object.entries(normalized)) {
                if (!Object.is(this.state[key], value)) {
                    changed[key] = {
                        previous: this.state[key],
                        value
                    };
                    this.state[key] = value;
                }
            }

            if (Object.keys(changed).length === 0) {
                return this.snapshot();
            }

            this.state.updatedAt = new Date().toISOString();

            if (!Object.prototype.hasOwnProperty.call(normalized, "network") &&
                Object.prototype.hasOwnProperty.call(normalized, "online")) {
                this.state.network = this.state.online ? "online" : "offline";
            }

            if (options.render !== false) {
                this.scheduleRender();
            }

            const detail = {
                changed: clone(changed),
                state: this.snapshot(),
                source: options.source || null
            };

            safeDispatch(this, "change", detail);
            safeDispatch(document, "speciedex:statusbar-updated", detail);
            this.context.events?.emit?.("statusbar:updated", detail);

            return detail.state;
        }

        set(name, value, options = {}) {
            return this.update({ [name]: value }, options);
        }

        get(name, fallback) {
            return Object.prototype.hasOwnProperty.call(this.state, name)
                ? this.state[name]
                : fallback;
        }

        snapshot() {
            return clone(this.state);
        }

        reset(options = {}) {
            const online = typeof navigator !== "undefined" ? Boolean(navigator.onLine) : false;
            this.state = Object.assign({}, DEFAULT_STATE, {
                online,
                network: online ? "online" : "offline",
                version: this.resolveInitialVersion(),
                updatedAt: new Date().toISOString()
            });

            if (options.render !== false) {
                this.render(true);
            }

            const detail = { state: this.snapshot() };
            safeDispatch(this, "reset", detail);
            safeDispatch(document, "speciedex:statusbar-reset", detail);

            return detail.state;
        }

        scheduleRender() {
            if (this.renderQueued || this.destroyed) {
                return;
            }

            this.renderQueued = true;
            const schedule = typeof window.requestAnimationFrame === "function"
                ? window.requestAnimationFrame.bind(window)
                : callback => window.setTimeout(callback, 0);

            schedule(() => {
                this.renderQueued = false;
                this.render();
            });
        }

        writeText(name, value, force = false) {
            const element = this.elements[name];
            if (!element) {
                return;
            }

            const text = String(value);
            if (!force && this.lastRendered[name] === text) {
                return;
            }

            element.textContent = text;
            this.lastRendered[name] = text;
        }

        renderProgress(force = false) {
            const element = this.elements.progress;
            if (!element) {
                return;
            }

            const progress = this.state.progress;
            const cacheValue = progress === null ? "indeterminate" : String(progress);
            if (!force && this.lastRendered.progress === cacheValue) {
                return;
            }

            if (element instanceof HTMLProgressElement) {
                if (progress === null) {
                    element.removeAttribute("value");
                } else {
                    element.max = 100;
                    element.value = progress;
                }
            } else {
                element.setAttribute("role", "progressbar");
                element.setAttribute("aria-valuemin", "0");
                element.setAttribute("aria-valuemax", "100");

                if (progress === null) {
                    element.removeAttribute("aria-valuenow");
                    element.dataset.indeterminate = "true";
                } else {
                    element.setAttribute("aria-valuenow", String(Math.round(progress)));
                    delete element.dataset.indeterminate;
                    element.style.setProperty("--status-progress", `${progress}%`);
                }
            }

            this.lastRendered.progress = cacheValue;
        }

        renderRoot(force = false) {
            const root = this.elements.root;
            if (!root) {
                return;
            }

            const status = [
                this.state.online ? "online" : "offline",
                this.state.busy ? "busy" : "idle"
            ].join(":");

            if (!force && this.lastRendered.root === status) {
                return;
            }

            root.dataset.network = this.state.network;
            root.dataset.online = String(this.state.online);
            root.dataset.busy = String(this.state.busy);
            root.dataset.activity = this.state.activity;
            root.setAttribute("aria-live", "polite");
            root.setAttribute("aria-atomic", "false");
            root.classList.toggle("is-online", this.state.online);
            root.classList.toggle("is-offline", !this.state.online);
            root.classList.toggle("is-busy", this.state.busy);
            root.classList.toggle("is-idle", !this.state.busy);

            this.lastRendered.root = status;
        }

        renderClock(force = false) {
            if (!this.elements.clock) {
                return;
            }
            this.writeText("clock", formatClock(), force);
        }

        render(force = false) {
            if (this.destroyed) {
                return this.snapshot();
            }

            if (!Object.values(this.elements).some(Boolean)) {
                this.resolveElements();
            }

            this.writeText("provider", this.state.provider, force);
            this.writeText("records", formatInteger(this.state.records), force);
            this.writeText("network", this.state.network, force);
            this.writeText("version", this.state.version, force);
            this.writeText("activity", this.state.activity, force);
            this.writeText("latency", formatLatency(this.state.latency), force);
            this.renderProgress(force);
            this.renderRoot(force);
            this.renderClock(force);

            const detail = { state: this.snapshot(), elements: this.elements };
            safeDispatch(this, "render", detail);

            return detail.state;
        }

        refresh() {
            this.resolveElements();
            this.refreshFromState();
            return this.render(true);
        }

        status() {
            return {
                name: SERVICE_NAME,
                module: MODULE_NAME,
                bound: this.bound,
                destroyed: this.destroyed,
                clockRunning: this.clockTimer !== null,
                observingDOM: this.observer !== null,
                elements: Object.fromEntries(
                    Object.entries(this.elements).map(([name, element]) => [name, Boolean(element)])
                ),
                state: this.snapshot()
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.destroyed = true;
            this.bound = false;
            this.stopClock();

            if (this.observer) {
                this.observer.disconnect();
                this.observer = null;
            }

            for (const unsubscribe of this.stateUnsubscribers.splice(0)) {
                try {
                    unsubscribe();
                } catch (error) {
                    // Cleanup must continue even if one subscriber fails.
                }
            }

            for (const cleanup of this.cleanup.splice(0).reverse()) {
                try {
                    cleanup();
                } catch (error) {
                    // Cleanup must continue even if one callback fails.
                }
            }

            safeDispatch(this, "destroy", { name: SERVICE_NAME });
            return true;
        }
    }

    function initialize(context = {}, options = {}) {
        const existing = context.statusbar || context.services?.get?.(SERVICE_NAME);
        if (existing instanceof StatusBar && !existing.destroyed) {
            existing.refresh();
            return existing;
        }

        const bar = new StatusBar(context, options);
        context.statusbar = bar;
        context.statusBar = bar;
        context.registerService?.(SERVICE_NAME, bar);

        safeDispatch(document, "speciedex:statusbar-ready", {
            name: MODULE_NAME,
            service: bar
        });

        context.events?.emit?.("statusbar:ready", bar);
        return bar;
    }

    const commands = [];

    const api = Object.freeze({
        name: MODULE_NAME,
        version: "2.0.0",
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        StatusBar,
        commands
    });

    window.SpeciedexTerminalStatusbar = api;
    window.SpeciedexTerminalStatusBar = api;
    window.SpeciedexTerminalModules = window.SpeciedexTerminalModules || {};
    window.SpeciedexTerminalModules[MODULE_NAME] = api;

    safeDispatch(document, "speciedex:terminal-module-available", {
        name: MODULE_NAME,
        module: api
    });
})(window, document);
