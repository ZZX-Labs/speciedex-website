/*
========================================================================
Speciedex.org
SpeciedexTerminal Application Wrapper
========================================================================

Primary integration layer for every module under:

    /static/js/terminal/
    /static/js/terminal/archive/
    /static/js/terminal/providers/
    /static/js/terminal/taxa/
    /static/js/terminal/visualization/
    /static/js/terminal/workers/

This wrapper discovers compatible module exports, builds a shared application
context, mounts the terminal interface, registers commands, coordinates
workers, and exposes one stable public API.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/
(function (window, document) {
    "use strict";

    const APP_NAME = "SpeciedexTerminalApp";
    const VERSION = "2.2.0";
    const ROOT_SELECTOR = "[data-speciedex-terminal], [data-terminal]";
    const INSTANCE_SYMBOL = Symbol.for("speciedex.terminal.instance");

    const MODULE_GROUPS = Object.freeze({
        foundation: [
            "State",
            "Storage",
            "Events",
            "Log",
            "Loading",
            "Theme",
            "Settings",
            "Library",
            "Index"
        ],
        interface: [
            "Layout",
            "Windows",
            "Toolbar",
            "Statusbar",
            "Notifications",
            "Progress",
            "Console",
            "Keyboard",
            "Contextmenu",
            "History",
            "Bookmarks",
            "Recent"
        ],
        renderers: [
            "Table",
            "Lists",
            "Grid",
            "Tree",
            "Charts",
            "Graphs",
            "Map",
            "Heatmap",
            "Matrix",
            "Timeline"
        ],
        data: [
            "API",
            "Router",
            "Search",
            "Scan",
            "Stream",
            "Import",
            "Export",
            "Stats",
            "Tags",
            "ProviderHealth",
            "ProviderManager"
        ],
        archive: [
            "Checksums",
            "Manifests",
            "Releases",
            "Volumes",
            "RecordsArchived",
            "SourceAssertions",
            "Synonyms",
            "UnresolvedConflicts",
            "ArchiveHistory",
            "LastUpdated"
        ],
        providers: [
            "Providers",
            "EnabledProviders",
            "EligibleProviders",
            "ProviderAssertions",
            "ProviderDocumentation",
            "ProviderErrors",
            "ProviderLatency",
            "ProviderOverlap",
            "ProviderSpecies",
            "ProviderStatistics"
        ],
        taxa: [
            "Ranks",
            "Domains",
            "Kingdoms",
            "Phyla",
            "Classes",
            "Orders",
            "Families",
            "Tribes",
            "Genera",
            "Species",
            "Subspecies",
            "Varieties",
            "Forms",
            "Clades"
        ],
        visualization: [
            "CMatrix",
            "Constellation",
            "Density",
            "ForceGraph",
            "Globe",
            "HeatMesh",
            "HexMap",
            "Network",
            "Phylogeny",
            "ProviderMatrix",
            "Radial",
            "RangeMap",
            "Sankey",
            "StreamGraph",
            "TaxonomyTree",
            "TimeSlider",
            "WordCloud",
            "ZMatrix",
            "Splash"
        ],
        help: [
            "Help"
        ]
    });

    const COMMAND_ALIASES = Object.freeze({
        cls: "clear",
        "?": "help",
        quit: "exit",
        providers: "provider",
        taxa: "taxonomy",
        viz: "visualize",
        ls: "list",
        find: "search",
        lookup: "search",
        query: "search"
    });

    const instances = new Set();
    const plugins = new Set();

    function iso(value = Date.now()) {
        const date = value instanceof Date
            ? value
            : new Date(value);

        if (Number.isNaN(date.getTime())) {
            return new Date().toISOString();
        }

        return date.toISOString();
    }

    function emit(target, name, detail = {}) {
        if (!target || typeof target.dispatchEvent !== "function") {
            return false;
        }

        try {
            return target.dispatchEvent(new CustomEvent(name, {
                bubbles: true,
                detail
            }));
        } catch (error) {
            console.warn(
                `[SpeciedexTerminal] Unable to dispatch "${name}":`,
                error
            );
            return false;
        }
    }

    function isElement(value) {
        return Boolean(
            value &&
            value.nodeType === 1 &&
            typeof value.querySelector === "function"
        );
    }

    function isNode(value) {
        return Boolean(
            value &&
            typeof value.nodeType === "number" &&
            typeof value.cloneNode === "function"
        );
    }

    function isPromiseLike(value) {
        return Boolean(
            value &&
            (
                typeof value === "object" ||
                typeof value === "function"
            ) &&
            typeof value.then === "function"
        );
    }

    function errorMessage(error) {
        if (error instanceof Error) {
            return error.message;
        }

        if (isObject(error) && error.message) {
            return String(error.message);
        }

        return String(error || "Unknown terminal error.");
    }

    function isObject(value) {
        return value !== null &&
            typeof value === "object" &&
            !Array.isArray(value);
    }

    function normalizeName(value) {
        return String(value || "")
            .trim()
            .replace(/[^a-zA-Z0-9]+(.)?/g, (_, character) =>
                character ? character.toUpperCase() : ""
            )
            .replace(/^./, character => character.toUpperCase());
    }

    function kebab(value) {
        return String(value || "")
            .trim()
            .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
            .replace(/[^a-zA-Z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "")
            .toLowerCase();
    }

    function parseBoolean(value, fallback = false) {
        if (value === undefined || value === null || value === "") {
            return fallback;
        }

        return !["false", "0", "no", "off"].includes(
            String(value).toLowerCase()
        );
    }

    function clampInteger(value, fallback, minimum, maximum) {
        const parsed = Number.parseInt(value, 10);

        if (!Number.isFinite(parsed)) {
            return fallback;
        }

        return Math.min(maximum, Math.max(minimum, parsed));
    }

    function safeStorage() {
        try {
            const key = "__speciedex_terminal_probe__";
            window.localStorage.setItem(key, key);
            window.localStorage.removeItem(key);
            return window.localStorage;
        } catch (error) {
            return null;
        }
    }

    function tokenize(input) {
        const result = [];
        let current = "";
        let quote = null;
        let escaped = false;

        for (const character of String(input || "").trim()) {
            if (escaped) {
                current += character;
                escaped = false;
                continue;
            }

            if (character === "\\") {
                escaped = true;
                continue;
            }

            if (quote) {
                if (character === quote) {
                    quote = null;
                } else {
                    current += character;
                }
                continue;
            }

            if (character === "'" || character === '"') {
                quote = character;
                continue;
            }

            if (/\s/.test(character)) {
                if (current) {
                    result.push(current);
                    current = "";
                }
                continue;
            }

            current += character;
        }

        if (quote) {
            throw new Error("Unterminated quoted string.");
        }

        if (escaped) {
            current += "\\";
        }

        if (current) {
            result.push(current);
        }

        return result;
    }

    function parseCommand(input) {
        const tokens = tokenize(input);
        const name = (tokens.shift() || "").toLowerCase();
        const args = [];
        const flags = {};
        const options = {};

        while (tokens.length) {
            const token = tokens.shift();

            if (token === "--") {
                args.push(...tokens);
                break;
            }

            if (token.startsWith("--")) {
                const body = token.slice(2);
                const separator = body.indexOf("=");

                if (body.startsWith("no-") && separator < 0) {
                    flags[body.slice(3)] = false;
                } else if (separator >= 0) {
                    const key = body.slice(0, separator);
                    const value = body.slice(separator + 1);

                    if (options[key] === undefined) {
                        options[key] = value;
                    } else if (Array.isArray(options[key])) {
                        options[key].push(value);
                    } else {
                        options[key] = [options[key], value];
                    }
                } else if (tokens[0] && !tokens[0].startsWith("-")) {
                    const value = tokens.shift();

                    if (options[body] === undefined) {
                        options[body] = value;
                    } else if (Array.isArray(options[body])) {
                        options[body].push(value);
                    } else {
                        options[body] = [options[body], value];
                    }
                } else {
                    flags[body] = true;
                }
                continue;
            }

            if (/^-[a-zA-Z]+$/.test(token)) {
                for (const flag of token.slice(1)) {
                    flags[flag] = true;
                }
                continue;
            }

            args.push(token);
        }

        return {
            raw: String(input || "").trim(),
            name: COMMAND_ALIASES[name] || name,
            invokedAs: name,
            args,
            flags,
            options
        };
    }

    function possibleGlobals(moduleName) {
        const normalized = normalizeName(moduleName);
        const compact = normalized.replace(/[^a-zA-Z0-9]/g, "");

        return [
            `SpeciedexTerminal${compact}`,
            `Terminal${compact}`,
            `Speciedex${compact}`,
            compact
        ];
    }

    function discoverModule(moduleName) {
        for (const globalName of possibleGlobals(moduleName)) {
            if (window[globalName] !== undefined) {
                return {
                    name: moduleName,
                    globalName,
                    value: window[globalName]
                };
            }
        }

        const registries = [
            window.SpeciedexTerminalModules,
            window.TerminalModules,
            window.SpeciedexModules
        ];

        for (const registry of registries) {
            if (!registry) {
                continue;
            }

            const keys = [
                moduleName,
                normalizeName(moduleName),
                kebab(moduleName),
                moduleName.toLowerCase()
            ];

            for (const key of keys) {
                if (registry[key] !== undefined) {
                    return {
                        name: moduleName,
                        globalName: `registry:${key}`,
                        value: registry[key]
                    };
                }
            }
        }

        return null;
    }

    function discoverAllModules() {
        const discovered = new Map();
        const missing = [];

        for (const [group, names] of Object.entries(MODULE_GROUPS)) {
            for (const name of names) {
                const module = discoverModule(name);

                if (module) {
                    module.group = group;
                    discovered.set(name, module);
                } else {
                    missing.push({ group, name });
                }
            }
        }

        return { discovered, missing };
    }

    async function invokeCompatible(target, methods, ...args) {
        if (!target) {
            return undefined;
        }

        if (typeof target === "function") {
            return target(...args);
        }

        for (const method of methods) {
            if (typeof target[method] === "function") {
                return target[method](...args);
            }
        }

        return undefined;
    }

    class CommandRegistry {
        constructor(app) {
            this.app = app;
            this.commands = new Map();
            this.aliases = new Map();
        }

        register(definition) {
            if (!definition || typeof definition !== "object") {
                throw new TypeError("Command definition must be an object.");
            }

            const name = String(definition.name || "").trim().toLowerCase();

            if (!/^[a-z0-9][a-z0-9:_-]*$/.test(name)) {
                throw new Error(`Invalid command name: ${name || "(empty)"}`);
            }

            if (typeof definition.handler !== "function") {
                throw new TypeError(`Command "${name}" requires a handler.`);
            }

            const normalized = {
                name,
                aliases: Array.isArray(definition.aliases)
                    ? definition.aliases.map(value => String(value).toLowerCase())
                    : [],
                description: String(definition.description || "No description."),
                usage: String(definition.usage || name),
                category: String(definition.category || "general"),
                hidden: definition.hidden === true,
                completer: typeof definition.completer === "function"
                    ? definition.completer
                    : null,
                handler: definition.handler,
                source: definition.source || "application"
            };

            this.commands.set(name, normalized);

            for (const alias of normalized.aliases) {
                this.aliases.set(alias, name);
            }

            return normalized;
        }

        unregister(name) {
            const command = this.get(name);

            if (!command) {
                return false;
            }

            this.commands.delete(command.name);

            for (const alias of command.aliases) {
                this.aliases.delete(alias);
            }

            return true;
        }

        get(name) {
            const normalized = String(name || "").toLowerCase();
            return this.commands.get(
                this.aliases.get(normalized) || normalized
            ) || null;
        }

        list({ includeHidden = false } = {}) {
            return [...this.commands.values()]
                .filter(command => includeHidden || !command.hidden)
                .sort((a, b) =>
                    a.category.localeCompare(b.category) ||
                    a.name.localeCompare(b.name)
                );
        }

        complete(prefix) {
            const normalized = String(prefix || "").toLowerCase();
            const names = [
                ...this.commands.keys(),
                ...this.aliases.keys()
            ];

            return [...new Set(names)]
                .filter(name => name.startsWith(normalized))
                .sort();
        }

        async execute(parsed) {
            const command = this.get(parsed.name);

            if (!command) {
                throw new Error(
                    `Command not found: ${parsed.invokedAs || parsed.name}`
                );
            }

            return command.handler({
                app: this.app,
                terminal: this.app,
                command,
                parsed,
                args: parsed.args,
                flags: parsed.flags,
                options: parsed.options,
                context: this.app.context,
                modules: this.app.modules,
                workers: this.app.workers,
                write: this.app.write.bind(this.app),
                writeJSON: this.app.writeJSON.bind(this.app),
                writeTable: this.app.writeTable.bind(this.app),
                setStatus: this.app.setStatus.bind(this.app)
            });
        }
    }

    class WorkerPool {
        constructor(app) {
            this.app = app;
            this.loader = window.SpeciedexTerminalLoader || null;
            this.workers = new Map();
            this.pending = new Map();
            this.sequence = 0;
        }

        has(name) {
            return Boolean(this.loader?.WORKERS?.[name]);
        }

        status() {
            return {
                available: Object.keys(this.loader?.WORKERS || {}),
                running: [...this.workers.keys()],
                pending: this.pending.size
            };
        }

        get(name) {
            if (this.workers.has(name)) {
                return this.workers.get(name);
            }

            if (!this.loader || typeof this.loader.createWorker !== "function") {
                throw new Error("SpeciedexTerminal worker loader is unavailable.");
            }

            const worker = this.loader.createWorker(name);
            worker.addEventListener("message", event =>
                this.onMessage(name, event)
            );
            worker.addEventListener("error", event =>
                this.onError(name, event)
            );

            this.workers.set(name, worker);
            return worker;
        }

        request(name, type, payload = {}, options = {}) {
            const worker = this.get(name);
            const id = `${name}:${Date.now()}:${++this.sequence}`;
            const timeout = Math.max(
                100,
                Number(options.timeout) || 30000
            );
            const signal = options.signal || null;
            const transfer = Array.isArray(options.transfer)
                ? options.transfer
                : [];

            if (signal?.aborted) {
                return Promise.reject(
                    signal.reason instanceof Error
                        ? signal.reason
                        : new DOMException(
                            "Worker request aborted.",
                            "AbortError"
                        )
                );
            }

            return new Promise((resolve, reject) => {
                let abortHandler = null;

                const cleanup = () => {
                    const request = this.pending.get(id);

                    if (request) {
                        window.clearTimeout(request.timer);
                        request.signal?.removeEventListener(
                            "abort",
                            request.abortHandler
                        );
                    }

                    this.pending.delete(id);
                };

                const timer = window.setTimeout(() => {
                    cleanup();
                    reject(new Error(
                        `Worker request timed out: ${name}/${type}`
                    ));
                }, timeout);

                if (signal) {
                    abortHandler = () => {
                        cleanup();
                        reject(
                            signal.reason instanceof Error
                                ? signal.reason
                                : new DOMException(
                                    "Worker request aborted.",
                                    "AbortError"
                                )
                        );
                    };

                    signal.addEventListener(
                        "abort",
                        abortHandler,
                        { once: true }
                    );
                }

                this.pending.set(id, {
                    resolve,
                    reject,
                    timer,
                    name,
                    type,
                    signal,
                    abortHandler
                });

                try {
                    worker.postMessage(
                        {
                            id,
                            type,
                            payload
                        },
                        transfer
                    );
                } catch (error) {
                    cleanup();
                    reject(error);
                }
            });
        }

        onMessage(name, event) {
            const message = event.data || {};
            const request = this.pending.get(message.id);

            if (!request) {
                emit(this.app.root, "speciedex:terminal-worker-message", {
                    name,
                    message
                });
                return;
            }

            window.clearTimeout(request.timer);
            request.signal?.removeEventListener(
                "abort",
                request.abortHandler
            );
            this.pending.delete(message.id);

            if (message.error) {
                request.reject(new Error(
                    message.error.message || String(message.error)
                ));
            } else {
                request.resolve(
                    message.result !== undefined ? message.result : message
                );
            }
        }

        onError(name, event) {
            console.error(`[SpeciedexTerminal] Worker "${name}" failed:`, event);

            for (const [id, request] of this.pending.entries()) {
                if (request.name !== name) {
                    continue;
                }

                window.clearTimeout(request.timer);
                request.signal?.removeEventListener(
                    "abort",
                    request.abortHandler
                );
                request.reject(new Error(
                    `Worker "${name}" failed while processing "${request.type}".`
                ));
                this.pending.delete(id);
            }

            emit(this.app.root, "speciedex:terminal-worker-error", {
                name,
                event
            });
        }

        terminate(name) {
            const worker = this.workers.get(name);

            if (!worker) {
                return false;
            }

            worker.terminate();
            this.workers.delete(name);

            for (const [id, request] of this.pending.entries()) {
                if (request.name !== name) {
                    continue;
                }

                window.clearTimeout(request.timer);
                request.signal?.removeEventListener(
                    "abort",
                    request.abortHandler
                );
                request.reject(new Error(
                    `Worker "${name}" was terminated.`
                ));
                this.pending.delete(id);
            }

            return true;
        }

        destroy() {
            for (const worker of this.workers.values()) {
                worker.terminate();
            }

            for (const request of this.pending.values()) {
                window.clearTimeout(request.timer);
                request.reject(new Error("Terminal worker pool was destroyed."));
            }

            this.workers.clear();
            this.pending.clear();
        }
    }

    class SpeciedexTerminalApplication {
        constructor(root, options = {}) {
            if (!isElement(root)) {
                throw new TypeError("A terminal root element is required.");
            }

            if (root[INSTANCE_SYMBOL]) {
                return root[INSTANCE_SYMBOL];
            }

            this.root = root;
            this.options = {
                promptUser:
                    options.promptUser ||
                    root.dataset.terminalPromptUser ||
                    "public",
                promptHost:
                    options.promptHost ||
                    root.dataset.terminalPromptHost ||
                    "speciedex",
                promptPath:
                    options.promptPath ||
                    root.dataset.terminalPromptPath ||
                    "~",
                promptSymbol:
                    options.promptSymbol ||
                    root.dataset.terminalPromptSymbol ||
                    "$",
                maxOutputEntries:
                    clampInteger(
                        options.maxOutputEntries ??
                        root.dataset.terminalMaxLines,
                        1000,
                        25,
                        10000
                    ),
                historyLimit:
                    clampInteger(
                        options.historyLimit ??
                        root.dataset.terminalMaxHistory,
                        250,
                        10,
                        5000
                    ),
                persistHistory:
                    options.persistHistory ??
                    parseBoolean(
                        root.dataset.terminalPersistHistory,
                        true
                    ),
                autofocus:
                    options.autofocus ??
                    parseBoolean(
                        root.dataset.terminalAutofocus,
                        false
                    ),
                welcome:
                    options.welcome !== false,
                ...options
            };

            this.elements = {};
            this.context = {};
            this.modules = new Map();
            this.missingModules = [];
            this.moduleInstances = new Map();
            this.commandRegistry = new CommandRegistry(this);
            this.workers = new WorkerPool(this);
            this.storage = safeStorage();
            this.history = [];
            this.historyIndex = 0;
            this.busy = false;
            this.mounted = false;
            this.mounting = false;
            this.mountPromise = null;
            this.initializationErrors = [];
            this.destroyed = false;
            this.executionAbortController = null;
            this.abortController = new AbortController();
            this.startedAt = null;
            this.metrics = {
                commandsExecuted: 0,
                commandErrors: 0,
                modulesMounted: 0,
                moduleErrors: 0,
                outputsWritten: 0,
                outputsTrimmed: 0,
                restarts: 0
            };
            this.storageKey =
                `speciedex-terminal:history:${root.dataset.terminalInstance || "default"}`;

            this.captureElements();
            this.context = this.createContext();
            this.installBuiltinCommands();

            root[INSTANCE_SYMBOL] = this;
            instances.add(this);
        }

        captureElements() {
            const find = selector => this.root.querySelector(selector);

            this.elements.shell =
                find("[data-terminal-shell]") || this.root;
            this.elements.screen =
                find("[data-terminal-screen]") || this.root;
            this.elements.output =
                find("[data-terminal-output]");
            this.elements.form =
                find("[data-terminal-form]");
            this.elements.input =
                find("[data-terminal-input]");
            this.elements.status =
                find("[data-terminal-status]");
            this.elements.statusIndicator =
                find("[data-terminal-status-indicator]");
            this.elements.completion =
                find("[data-terminal-completion]");
            this.elements.hint =
                find("[data-terminal-hint]");
            this.elements.version =
                find("[data-terminal-version]");
            this.elements.provider =
                find("[data-terminal-provider]");
            this.elements.recordCount =
                find("[data-terminal-record-count]");
            this.elements.networkStatus =
                find("[data-terminal-network-status]");
            this.elements.regions =
                find("[data-terminal-regions]");
            this.elements.splash =
                find("[data-terminal-splash]");
            this.elements.consoleRegion =
                find("[data-terminal-console-region]");
            this.elements.toggleTerminal =
                find("[data-terminal-toggle-terminal]");
            this.elements.toggleSplash =
                find("[data-terminal-toggle-splash]");
            this.elements.toggleConsole =
                find("[data-terminal-toggle-console]");

            if (
                !this.elements.output ||
                !this.elements.form ||
                !this.elements.input
            ) {
                throw new Error(
                    "Terminal markup requires output, form, and input hooks."
                );
            }
        }

        createContext() {
            return {
                app: this,
                root: this.root,
                elements: this.elements,
                options: this.options,
                commands: this.commandRegistry,
                workers: this.workers,
                state: new Map(),
                services: new Map(),
                renderers: new Map(),
                routes: new Map(),
                providers: new Map(),
                taxa: new Map(),
                visualizations: new Map(),
                archive: new Map(),
                events: null,
                terminalSplash: null,
                terminalVisibility: null,
                registerCommand: definition =>
                    this.commandRegistry.register(definition),
                unregisterCommand: name =>
                    this.commandRegistry.unregister(name),
                registerService: (name, service) =>
                    this.context.services.set(name, service),
                registerRenderer: (name, renderer) =>
                    this.context.renderers.set(name, renderer),
                registerRoute: (name, route) =>
                    this.context.routes.set(name, route),
                registerProvider: (name, provider) =>
                    this.context.providers.set(name, provider),
                registerTaxon: (name, handler) =>
                    this.context.taxa.set(name, handler),
                registerVisualization: (name, visualization) =>
                    this.context.visualizations.set(name, visualization),
                registerArchiveService: (name, service) =>
                    this.context.archive.set(name, service),
                write: this.write.bind(this),
                writeJSON: this.writeJSON.bind(this),
                writeTable: this.writeTable.bind(this),
                clear: this.clear.bind(this),
                execute: this.execute.bind(this),
                setStatus: this.setStatus.bind(this),
                focus: this.focus.bind(this),
                toggleRegion: this.toggleRegion.bind(this),
                setRegionVisibility: this.setRegionVisibility.bind(this),
                getRegionVisibility: this.getRegionVisibility.bind(this),
                emit: (name, detail) => emit(this.root, name, detail),
                getModule: name =>
                    this.moduleInstances.get(normalizeName(name)) ||
                    this.moduleInstances.get(String(name)) ||
                    null,
                getService: name =>
                    this.context.services.get(String(name)) || null,
                getRenderer: name =>
                    this.context.renderers.get(String(name)) || null,
                getVisualization: name =>
                    this.context.visualizations.get(String(name)) || null,
                requestWorker: (name, type, payload, options) =>
                    this.workers.request(name, type, payload, options),
                signal: this.abortController.signal
            };
        }

        async mount() {
            if (this.mounted) {
                return this;
            }

            if (this.mountPromise) {
                return this.mountPromise;
            }

            this.mountPromise = this.performMount().catch(error => {
                this.mountPromise = null;
                throw error;
            });

            return this.mountPromise;
        }

        async performMount() {
            this.mounting = true;
            this.startedAt = iso();
            this.initializationErrors = [];

            try {
                this.setStatus("Loading modules", "loading");
                this.restoreHistory();
                this.bindEvents();

                const discovery = discoverAllModules();
                this.modules = discovery.discovered;
                this.missingModules = discovery.missing;

                await this.runInitializationPhase(
                    "module initialization",
                    () => this.initializeModuleGroups()
                );

                await this.runInitializationPhase(
                    "command registration",
                    () => this.installModuleCommands()
                );

                await this.runInitializationPhase(
                    "plugin installation",
                    () => this.installPlugins()
                );

                this.verifyRuntime();
                this.updateMetadata();
                this.removeBootstrapMessage();

                if (this.options.welcome) {
                    this.printWelcome();
                }

                if (this.initializationErrors.length) {
                    this.write(
                        `Terminal initialized in degraded mode with ${this.initializationErrors.length} warning(s).`,
                        "warning"
                    );

                    for (const failure of this.initializationErrors) {
                        this.write(
                            `${failure.phase}: ${errorMessage(failure.error)}`,
                            "warning"
                        );
                    }

                    this.setStatus("Ready (degraded)", "warning");
                    this.root.dataset.terminalState = "degraded";
                } else {
                    this.setStatus("Ready", "ready");
                    this.root.dataset.terminalState = "ready";
                }

                this.root.dataset.terminalReady = "true";
                this.root.removeAttribute("aria-busy");
                delete this.root.dataset.terminalError;
                this.mounted = true;

                emit(this.root, "speciedex:terminal-application-ready", {
                    app: this,
                    modules: [...this.modules.keys()],
                    missingModules: [...this.missingModules],
                    initializationErrors: this.initializationErrors.slice()
                });

                if (this.options.autofocus) {
                    window.requestAnimationFrame(() => this.focus());
                }

                return this;
            } catch (error) {
                const message = errorMessage(error);

                this.root.dataset.terminalReady = "error";
                this.root.dataset.terminalState = "error";
                this.root.dataset.terminalError = message;
                this.root.removeAttribute("aria-busy");
                this.setStatus(`Initialization failed: ${message}`, "error");

                try {
                    this.write(`Initialization failed: ${message}`, "error");
                } catch (writeError) {
                    console.error(
                        "[SpeciedexTerminal] Unable to render initialization error:",
                        writeError
                    );
                }

                emit(this.root, "speciedex:terminal-application-error", {
                    app: this,
                    error
                });

                throw error;
            } finally {
                this.mounting = false;
            }
        }

        async runInitializationPhase(phase, callback) {
            try {
                return await callback();
            } catch (error) {
                this.initializationErrors.push({ phase, error });
                console.error(
                    `[SpeciedexTerminal] ${phase} failed:`,
                    error
                );
                return null;
            }
        }

        async initializeModuleGroups() {
            for (const groupName of Object.keys(MODULE_GROUPS)) {
                await this.initializeGroup(groupName);
            }
        }

        async initializeGroup(groupName) {
            const names = MODULE_GROUPS[groupName] || [];

            for (const name of names) {
                const record = this.modules.get(name);

                if (!record) {
                    continue;
                }

                try {
                    const instance = await this.initializeModule(record);
                    const mounted = instance ?? record.value;

                    this.moduleInstances.set(name, mounted);
                    this.metrics.modulesMounted += 1;

                    if (name === "Events") {
                        this.context.events = mounted;
                    }

                    if (name === "Splash") {
                        this.context.terminalSplash = mounted;
                    }

                    emit(this.root, "speciedex:terminal-module-mounted", {
                        app: this,
                        group: groupName,
                        name,
                        instance: instance ?? record.value
                    });
                } catch (error) {
                    this.metrics.moduleErrors += 1;
                    console.error(
                        `[SpeciedexTerminal] Module "${name}" failed to initialize:`,
                        error
                    );

                    this.write(
                        `Module initialization warning: ${name}: ${error.message}`,
                        "warning"
                    );
                }
            }
        }

        async initializeModule(record) {
            const value = record.value;

            if (typeof value === "function") {
                try {
                    return new value(this.context);
                } catch (constructorError) {
                    return value(this.context);
                }
            }

            return invokeCompatible(
                value,
                [
                    "initialize",
                    "init",
                    "setup",
                    "register",
                    "start",
                    "mount"
                ],
                this.context
            );
        }

        async installModuleCommands() {
            for (const [name, record] of this.modules.entries()) {
                const targets = [...new Set([
                    this.moduleInstances.get(name),
                    record.value
                ].filter(Boolean))];

                for (const target of targets) {
                    try {
                        const commandSources = [
                            target.commands,
                            target.command,
                            typeof target.getCommands === "function"
                                ? await target.getCommands(this.context)
                                : null
                        ];

                        for (const source of commandSources) {
                            this.registerCommandSource(source, name);
                        }

                        if (typeof target.registerCommands === "function") {
                            await target.registerCommands(
                                this.commandRegistry,
                                this.context
                            );
                        }
                    } catch (error) {
                        this.metrics.moduleErrors += 1;
                        this.initializationErrors.push({
                            phase: `command registration (${name})`,
                            error
                        });
                        console.error(
                            `[SpeciedexTerminal] Command registration failed for "${name}":`,
                            error
                        );
                    }
                }
            }
        }

        registerCommandSource(source, moduleName) {
            if (!source) {
                return;
            }

            const entries = Array.isArray(source)
                ? source
                : source instanceof Map
                    ? [...source.values()]
                    : typeof source === "object" &&
                      typeof source.handler !== "function"
                        ? Object.entries(source).map(([name, value]) => {
                            if (typeof value === "function") {
                                return { name, handler: value };
                            }
                            return { name, ...value };
                        })
                        : [source];

            for (const definition of entries) {
                try {
                    this.commandRegistry.register({
                        category: kebab(moduleName),
                        source: moduleName,
                        ...definition
                    });
                } catch (error) {
                    console.warn(
                        `[SpeciedexTerminal] Could not register command from ${moduleName}:`,
                        error
                    );
                }
            }
        }

        async installPlugins() {
            for (const plugin of plugins) {
                try {
                    await invokeCompatible(
                        plugin,
                        ["mount", "install", "initialize", "init", "use"],
                        this.context
                    );
                } catch (error) {
                    this.initializationErrors.push({
                        phase: "plugin installation",
                        error
                    });
                    console.error(
                        "[SpeciedexTerminal] Plugin installation failed:",
                        error
                    );
                }
            }
        }

        bindEvents() {
            const signal = this.abortController.signal;

            this.elements.form.addEventListener("submit", event => {
                event.preventDefault();
                this.execute(this.elements.input.value);
            }, { signal });

            this.elements.input.addEventListener("keydown", event =>
                this.handleKeydown(event),
                { signal }
            );

            this.root.addEventListener("click", event =>
                this.handleClick(event),
                { signal }
            );

            window.addEventListener("online", () => {
                this.updateMetadata();
                this.setStatus("Online", "ready");
            }, { signal });

            window.addEventListener("offline", () => {
                this.updateMetadata();
                this.setStatus("Offline", "warning");
            }, { signal });

            document.addEventListener(
                "speciedex:terminal-module-available",
                event => {
                    this.registerLateModule(
                        event.detail?.name,
                        event.detail?.module
                    );
                },
                { signal }
            );

            document.addEventListener(
                "fullscreenchange",
                () => {
                    const button = this.root.querySelector(
                        '[data-terminal-action="fullscreen"]'
                    );

                    button?.setAttribute(
                        "aria-pressed",
                        String(
                            document.fullscreenElement ===
                            this.elements.shell
                        )
                    );
                },
                { signal }
            );
        }

        async registerLateModule(name, value) {
            const normalized = normalizeName(name);

            if (
                !normalized ||
                !value ||
                this.modules.has(normalized) ||
                this.destroyed
            ) {
                return null;
            }

            let group = "data";

            for (const [candidate, names] of Object.entries(MODULE_GROUPS)) {
                if (names.includes(normalized)) {
                    group = candidate;
                    break;
                }
            }

            const record = {
                name: normalized,
                globalName: "event:module-available",
                value,
                group
            };

            this.modules.set(normalized, record);
            this.missingModules = this.missingModules.filter(
                item => item.name !== normalized
            );

            try {
                const instance = await this.initializeModule(record);
                const mounted = instance ?? value;

                this.moduleInstances.set(normalized, mounted);
                this.metrics.modulesMounted += 1;
                this.registerCommandSource(
                    mounted?.commands || value?.commands,
                    normalized
                );

                emit(
                    this.root,
                    "speciedex:terminal-module-mounted",
                    {
                        app: this,
                        group,
                        name: normalized,
                        instance: mounted,
                        late: true
                    }
                );

                this.updateMetadata();
                return mounted;
            } catch (error) {
                this.metrics.moduleErrors += 1;
                this.write(
                    `Late module initialization warning: ${normalized}: ${errorMessage(error)}`,
                    "warning"
                );
                return null;
            }
        }

        handleKeydown(event) {
            if (event.key === "ArrowUp") {
                event.preventDefault();
                this.navigateHistory(-1);
                return;
            }

            if (event.key === "ArrowDown") {
                event.preventDefault();
                this.navigateHistory(1);
                return;
            }

            if (event.key === "Tab") {
                event.preventDefault();
                this.complete();
                return;
            }

            if (event.key === "Escape") {
                this.hideCompletion();
                return;
            }

            if (event.ctrlKey && event.key.toLowerCase() === "l") {
                event.preventDefault();
                this.clear();
                return;
            }

            if (event.ctrlKey && event.key.toLowerCase() === "c") {
                this.executionAbortController?.abort(
                    new DOMException(
                        "Command interrupted by user.",
                        "AbortError"
                    )
                );

                emit(this.root, "speciedex:terminal-interrupt", {
                    app: this
                });

                if (this.busy) {
                    this.write("^C", "warning");
                }
            }
        }

        handleClick(event) {
            const action = event.target.closest("[data-terminal-action]");

            if (!action || !this.root.contains(action)) {
                return;
            }

            const name = action.dataset.terminalAction;

            switch (name) {
                case "clear":
                    this.clear();
                    break;
                case "help":
                    this.execute("help");
                    break;
                case "restart":
                    this.restart();
                    break;
                case "copy":
                    this.copyOutput();
                    break;
                case "fullscreen":
                    this.toggleFullscreen(action);
                    break;
                case "toggle-terminal":
                    this.toggleRegion("terminal");
                    break;
                case "toggle-splash":
                    this.toggleRegion("splash");
                    break;
                case "toggle-console":
                    this.toggleRegion("console");
                    break;
                default:
                    emit(this.root, "speciedex:terminal-action", {
                        app: this,
                        action: name
                    });
            }
        }

        async execute(input) {
            if (this.busy || this.destroyed) {
                return;
            }

            const raw = String(input || "").trim();
            this.elements.input.value = "";
            this.hideCompletion();

            if (!raw) {
                return;
            }

            this.addHistory(raw);
            this.writeCommand(raw);

            let parsed;

            try {
                parsed = parseCommand(raw);
            } catch (error) {
                this.write(error.message, "error");
                return;
            }

            this.setBusy(true);
            this.executionAbortController = new AbortController();
            this.context.executionSignal =
                this.executionAbortController.signal;
            this.metrics.commandsExecuted += 1;

            try {
                const result = await this.commandRegistry.execute(parsed);

                if (
                    result !== undefined &&
                    result !== null &&
                    result !== "" &&
                    !(
                        isNode(result) &&
                        result.parentNode ===
                        this.elements.output
                    )
                ) {
                    this.renderResult(result);
                }

                emit(this.root, "speciedex:terminal-command-complete", {
                    app: this,
                    parsed,
                    result
                });
            } catch (error) {
                this.metrics.commandErrors += 1;

                if (error?.name === "AbortError") {
                    this.write(
                        errorMessage(error),
                        "warning"
                    );
                } else {
                    console.error("[SpeciedexTerminal] Command failed:", error);
                    this.write(errorMessage(error), "error");
                }

                emit(this.root, "speciedex:terminal-command-error", {
                    app: this,
                    parsed,
                    error
                });
            } finally {
                delete this.context.executionSignal;
                this.executionAbortController = null;
                this.setBusy(false);
                this.focus();
            }
        }

        installBuiltinCommands() {
            const register = definition =>
                this.commandRegistry.register(definition);

            register({
                name: "help",
                aliases: ["?"],
                category: "core",
                description: "List commands or show help for one command.",
                usage: "help [command]",
                handler: ({ args }) => this.commandHelp(args)
            });

            register({
                name: "clear",
                aliases: ["cls"],
                category: "core",
                description: "Clear terminal output.",
                handler: () => this.clear()
            });

            register({
                name: "history",
                category: "core",
                description: "Display command history.",
                handler: () => this.commandHistory()
            });

            register({
                name: "status",
                category: "core",
                description: "Display application, module, worker, and network status.",
                handler: () => this.commandStatus()
            });

            register({
                name: "modules",
                category: "core",
                description: "List discovered and missing terminal modules.",
                usage: "modules [--missing]",
                handler: ({ flags, options }) =>
                    this.commandModules(Boolean(flags.missing || options.missing))
            });

            register({
                name: "workers",
                category: "core",
                description: "List available terminal workers.",
                handler: () =>
                    this.writeTable(
                        ["Worker", "Available", "Running"],
                        Object.keys(
                            window.SpeciedexTerminalLoader?.WORKERS || {}
                        ).map(name => [
                            name,
                            this.workers.has(name) ? "yes" : "no",
                            this.workers.workers.has(name) ? "yes" : "no"
                        ])
                    )
            });

            register({
                name: "version",
                aliases: ["-v", "--version"],
                category: "core",
                description: "Display version information.",
                handler: () => this.write(
                    `SpeciedexTerminal Application ${VERSION}`,
                    "success"
                )
            });

            register({
                name: "about",
                category: "core",
                description: "Describe the SpeciedexTerminal application.",
                handler: () => this.write([
                    "SpeciedexTerminal",
                    "Interactive interface for Speciedex biodiversity data,",
                    "archives, taxonomy, providers, search, statistics,",
                    "visualizations, imports, exports, and distributed services.",
                    "",
                    "https://speciedex.org/"
                ].join("\n"), "output", { preformatted: true })
            });

            register({
                name: "printf",
                category: "core",
                description: "Print text.",
                usage: "printf <text>",
                handler: ({ args }) => this.write(args.join(" "))
            });

            register({
                name: "reload",
                category: "core",
                description: "Reload all terminal modules and restart the interface.",
                handler: async () => {
                    await window.SpeciedexTerminalLoader?.load({ reload: true });
                    this.restart();
                }
            });

            register({
                name: "diagnostics",
                aliases: ["diag"],
                category: "core",
                description: "Display detailed runtime diagnostics.",
                handler: () => this.writeJSON(this.diagnostics())
            });

            register({
                name: "services",
                category: "core",
                description: "List registered application services.",
                handler: () => this.writeTable(
                    ["Service", "Type"],
                    [...this.context.services.entries()].map(
                        ([name, service]) => [
                            name,
                            typeof service
                        ]
                    )
                )
            });

            register({
                name: "renderers",
                category: "core",
                description: "List registered renderers and visualizations.",
                handler: () => this.writeJSON({
                    renderers: [...this.context.renderers.keys()].sort(),
                    visualizations: [
                        ...this.context.visualizations.keys()
                    ].sort()
                })
            });

            register({
                name: "cancel",
                category: "core",
                description: "Cancel the currently executing command.",
                handler: () => {
                    if (!this.executionAbortController) {
                        return this.write(
                            "No command is currently running.",
                            "warning"
                        );
                    }

                    this.executionAbortController.abort(
                        new DOMException(
                            "Command cancelled.",
                            "AbortError"
                        )
                    );

                    return this.write(
                        "Cancellation requested.",
                        "success"
                    );
                }
            });
        }

        verifyRuntime() {
            const requiredCommands = [
                "search",
                "search-help",
                "search-fields",
                "search-explain",
                "splash"
            ];

            const missingCommands = requiredCommands.filter(
                name => !this.commandRegistry.get(name)
            );

            const splashModule =
                this.moduleInstances.get("Splash") ||
                this.context.terminalSplash ||
                null;

            const report = {
                modules: this.modules.size,
                mountedModules: this.moduleInstances.size,
                missingModules: this.missingModules.length,
                commands: this.commandRegistry.list({
                    includeHidden: true
                }).length,
                missingCommands,
                splashModule: Boolean(splashModule),
                splashElement: Boolean(this.elements.splash),
                workers: this.workers.status()
            };

            if (missingCommands.length) {
                this.write(
                    `Runtime warning: missing commands: ${missingCommands.join(", ")}`,
                    "warning"
                );
            }

            if (this.elements.splash && !splashModule) {
                this.write(
                    "Runtime warning: terminal splash markup exists, but the Splash module did not mount.",
                    "warning"
                );
            }

            emit(
                this.root,
                "speciedex:terminal-runtime-verification",
                {
                    app: this,
                    report
                }
            );

            return report;
        }

        diagnostics() {
            return {
                application: APP_NAME,
                version: VERSION,
                startedAt: this.startedAt,
                mounted: this.mounted,
                mounting: this.mounting,
                destroyed: this.destroyed,
                busy: this.busy,
                online: navigator.onLine,
                rootConnected: this.root.isConnected,
                modules: {
                    discovered: this.modules.size,
                    mounted: this.moduleInstances.size,
                    missing: this.missingModules.slice(),
                    names: [...this.modules.keys()].sort()
                },
                registries: {
                    commands: this.commandRegistry.list({
                        includeHidden: true
                    }).length,
                    services: [...this.context.services.keys()].sort(),
                    renderers: [...this.context.renderers.keys()].sort(),
                    routes: [...this.context.routes.keys()].sort(),
                    providers: [...this.context.providers.keys()].sort(),
                    taxa: [...this.context.taxa.keys()].sort(),
                    visualizations: [
                        ...this.context.visualizations.keys()
                    ].sort(),
                    archive: [...this.context.archive.keys()].sort()
                },
                workers: this.workers.status(),
                history: {
                    entries: this.history.length,
                    limit: this.options.historyLimit,
                    persistent: this.options.persistHistory,
                    storageAvailable: Boolean(this.storage)
                },
                output: {
                    entries: this.elements.output.children.length,
                    limit: this.options.maxOutputEntries
                },
                metrics: { ...this.metrics },
                loader:
                    window.SpeciedexTerminalLoader?.state ||
                    "unavailable"
            };
        }

        getRegionElement(name) {
            switch (String(name || "").toLowerCase()) {
                case "terminal":
                    return this.elements.regions;
                case "splash":
                    return this.elements.splash;
                case "console":
                    return this.elements.consoleRegion;
                default:
                    return null;
            }
        }

        getRegionButton(name) {
            switch (String(name || "").toLowerCase()) {
                case "terminal":
                    return this.elements.toggleTerminal;
                case "splash":
                    return this.elements.toggleSplash;
                case "console":
                    return this.elements.toggleConsole;
                default:
                    return null;
            }
        }

        getRegionVisibility(name) {
            const region = this.getRegionElement(name);
            return region ? !region.hidden : false;
        }

        setRegionVisibility(name, visible) {
            const normalized = String(name || "").toLowerCase();
            const region = this.getRegionElement(normalized);

            if (!region) {
                return false;
            }

            const nextVisible = Boolean(visible);
            region.hidden = !nextVisible;
            region.dataset.collapsed = nextVisible ? "false" : "true";
            region.setAttribute("aria-hidden", String(!nextVisible));

            const button = this.getRegionButton(normalized);

            if (button) {
                button.setAttribute("aria-expanded", String(nextVisible));
                button.classList.toggle("is-collapsed", !nextVisible);
            }

            this.root.classList.toggle(
                `terminal-${normalized}-collapsed`,
                !nextVisible
            );

            emit(
                this.root,
                "speciedex:terminal-region-visibility",
                {
                    app: this,
                    region: normalized,
                    visible: nextVisible
                }
            );

            return true;
        }

        toggleRegion(name) {
            return this.setRegionVisibility(
                name,
                !this.getRegionVisibility(name)
            );
        }

        commandHelp(args) {
            if (args.length) {
                const command = this.commandRegistry.get(args[0]);

                if (!command) {
                    this.write(`No help found for "${args[0]}".`, "error");
                    return;
                }

                this.write([
                    command.name,
                    command.description,
                    `Usage: ${command.usage}`,
                    command.aliases.length
                        ? `Aliases: ${command.aliases.join(", ")}`
                        : "",
                    `Category: ${command.category}`,
                    `Source: ${command.source}`
                ].filter(Boolean).join("\n"), "output", {
                    preformatted: true
                });

                return;
            }

            const grouped = new Map();

            for (const command of this.commandRegistry.list()) {
                if (!grouped.has(command.category)) {
                    grouped.set(command.category, []);
                }
                grouped.get(command.category).push(command);
            }

            const lines = [];

            for (const [category, commands] of grouped.entries()) {
                lines.push(`[${category}]`);

                for (const command of commands) {
                    lines.push(
                        `  ${command.name.padEnd(22)} ${command.description}`
                    );
                }

                lines.push("");
            }

            this.write(lines.join("\n").trim(), "output", {
                preformatted: true
            });
        }

        commandHistory() {
            if (!this.history.length) {
                this.write("No command history is available.");
                return;
            }

            this.write(
                this.history.map((command, index) =>
                    `${String(index + 1).padStart(4)}  ${command}`
                ).join("\n"),
                "output",
                { preformatted: true }
            );
        }

        commandStatus() {
            const status = {
                application:
                    this.destroyed
                        ? "destroyed"
                        : this.busy
                            ? "busy"
                            : this.mounted
                                ? "ready"
                                : "loading",
                version: VERSION,
                startedAt: this.startedAt,
                online: navigator.onLine,
                modulesDiscovered: this.modules.size,
                modulesMissing: this.missingModules.length,
                moduleInstances: this.moduleInstances.size,
                commands: this.commandRegistry.list({
                    includeHidden: true
                }).length,
                services: this.context.services.size,
                renderers: this.context.renderers.size,
                visualizations:
                    this.context.visualizations.size,
                activeWorkers: this.workers.workers.size,
                pendingWorkerRequests:
                    this.workers.pending.size,
                historyEntries: this.history.length,
                outputEntries:
                    this.elements.output.children.length,
                metrics: { ...this.metrics },
                loaderState:
                    window.SpeciedexTerminalLoader?.state ||
                    "unavailable"
            };

            this.writeJSON(status);
        }

        commandModules(missingOnly = false) {
            if (missingOnly) {
                this.writeTable(
                    ["Group", "Missing module"],
                    this.missingModules.map(item => [
                        item.group,
                        item.name
                    ])
                );
                return;
            }

            this.writeTable(
                ["Group", "Module", "Export", "Mounted"],
                [...this.modules.values()].map(record => [
                    record.group,
                    record.name,
                    record.globalName,
                    this.moduleInstances.has(record.name) ? "yes" : "no"
                ])
            );
        }

        writeCommand(command) {
            const line = document.createElement("div");
            line.className = "terminal-entry terminal-entry-command";

            const prompt = document.createElement("span");
            prompt.className = "terminal-entry-prompt";
            prompt.textContent = this.promptText();

            const text = document.createElement("span");
            text.className = "terminal-entry-command-text";
            text.textContent = command;

            line.append(prompt, document.createTextNode(" "), text);
            this.append(line);
        }

        write(content, type = "output", options = {}) {
            const element = document.createElement(
                options.preformatted ? "pre" : "div"
            );

            element.className =
                `terminal-entry terminal-entry-${kebab(type)}`;

            if (options.html === true) {
                element.innerHTML = String(content ?? "");
            } else {
                element.textContent = String(content ?? "");
            }

            this.append(element);
            return element;
        }

        writeJSON(value) {
            const pre = document.createElement("pre");
            pre.className = "terminal-entry terminal-entry-json";
            const code = document.createElement("code");
            const seen = new WeakSet();

            code.textContent = JSON.stringify(
                value,
                (key, item) => {
                    if (typeof item === "bigint") {
                        return item.toString();
                    }

                    if (typeof item === "function") {
                        return `[Function ${item.name || "anonymous"}]`;
                    }

                    if (
                        item &&
                        typeof item === "object"
                    ) {
                        if (seen.has(item)) {
                            return "[Circular]";
                        }

                        seen.add(item);
                    }

                    return item;
                },
                2
            );

            pre.appendChild(code);
            this.append(pre);
            return pre;
        }

        writeTable(headers, rows) {
            const wrapper = document.createElement("div");
            wrapper.className = "terminal-table-wrapper";

            const table = document.createElement("table");
            table.className = "terminal-table";

            const thead = document.createElement("thead");
            const headerRow = document.createElement("tr");

            for (const header of headers) {
                const cell = document.createElement("th");
                cell.scope = "col";
                cell.textContent = String(header);
                headerRow.appendChild(cell);
            }

            thead.appendChild(headerRow);

            const tbody = document.createElement("tbody");

            for (const row of rows) {
                const tableRow = document.createElement("tr");

                for (const value of row) {
                    const cell = document.createElement("td");
                    cell.textContent = value === null || value === undefined
                        ? ""
                        : String(value);
                    tableRow.appendChild(cell);
                }

                tbody.appendChild(tableRow);
            }

            table.append(thead, tbody);
            wrapper.appendChild(table);
            this.append(wrapper);
            return wrapper;
        }

        renderResult(result) {
            if (isPromiseLike(result)) {
                return Promise.resolve(result).then(
                    value => this.renderResult(value)
                );
            }

            if (isNode(result)) {
                this.append(result);
                return result;
            }

            if (result instanceof Map) {
                this.writeJSON(
                    Object.fromEntries(result)
                );
                return result;
            }

            if (result instanceof Set) {
                this.writeJSON(
                    [...result]
                );
                return result;
            }

            if (result instanceof Error) {
                this.write(
                    errorMessage(result),
                    "error"
                );
                return result;
            }

            if (
                Array.isArray(result) ||
                isObject(result)
            ) {
                this.writeJSON(result);
                return result;
            }

            this.write(result);
            return result;
        }

        append(element) {
            this.elements.output.appendChild(element);
            this.metrics.outputsWritten += 1;

            while (
                this.elements.output.children.length >
                this.options.maxOutputEntries
            ) {
                this.elements.output.firstElementChild?.remove();
                this.metrics.outputsTrimmed += 1;
            }

            window.requestAnimationFrame(() => {
                this.elements.output.scrollTop =
                    this.elements.output.scrollHeight;
                this.elements.screen.scrollTop =
                    this.elements.screen.scrollHeight;
            });

            emit(this.root, "speciedex:terminal-output", {
                app: this,
                element
            });

            return element;
        }

        clear() {
            this.elements.output.replaceChildren();
            emit(this.root, "speciedex:terminal-cleared", {
                app: this
            });
        }

        printWelcome() {
            this.write([
                `SpeciedexTerminal Application ${VERSION}`,
                "Open biodiversity research and data infrastructure.",
                `${this.modules.size} modules discovered; ` +
                `${this.commandRegistry.list({ includeHidden: true }).length} commands registered; ` +
                `${this.missingModules.length} modules unavailable.`,
                'Enter "help" to list available commands.'
            ].join("\n"), "system", { preformatted: true });
        }

        removeBootstrapMessage() {
            this.root
                .querySelector("[data-terminal-bootstrap-message]")
                ?.remove();
        }

        promptText() {
            return (
                `${this.options.promptUser}@${this.options.promptHost}:` +
                `${this.options.promptPath}${this.options.promptSymbol}`
            );
        }

        setStatus(message, state = "ready") {
            if (this.elements.status) {
                this.elements.status.textContent = String(message);
                this.elements.status.dataset.state = state;
            }

            if (this.elements.statusIndicator) {
                this.elements.statusIndicator.dataset.state = state;
            }

            this.root.dataset.terminalState = state;
        }

        setBusy(value) {
            this.busy = Boolean(value);
            this.elements.input.disabled = this.busy;
            this.setStatus(
                this.busy ? "Working" : "Ready",
                this.busy ? "busy" : "ready"
            );
        }

        updateMetadata() {
            if (this.elements.version) {
                this.elements.version.textContent = `Version: ${VERSION}`;
            }

            if (this.elements.provider) {
                this.elements.provider.textContent =
                    `Modules: ${this.modules.size}`;
            }

            if (this.elements.networkStatus) {
                this.elements.networkStatus.textContent =
                    `Network: ${navigator.onLine ? "online" : "offline"}`;
            }

            const statisticsModule =
                this.moduleInstances.get("Stats") ||
                this.moduleInstances.get("ProviderStatistics");

            if (
                this.elements.recordCount &&
                statisticsModule &&
                typeof statisticsModule.getRecordCount === "function"
            ) {
                Promise.resolve(
                    statisticsModule.getRecordCount(this.context)
                ).then(value => {
                    this.elements.recordCount.textContent =
                        `Records: ${Number(value).toLocaleString()}`;
                }).catch(() => {
                    this.elements.recordCount.textContent =
                        "Records: unavailable";
                });
            }
        }

        addHistory(command) {
            if (
                this.history[this.history.length - 1] !== command
            ) {
                this.history.push(command);
            }

            this.history = this.history.slice(-this.options.historyLimit);
            this.historyIndex = this.history.length;
            this.persistHistory();
        }

        restoreHistory() {
            if (!this.options.persistHistory) {
                return;
            }

            try {
                if (!this.storage) {
                    return;
                }

                const parsed = JSON.parse(
                    this.storage.getItem(this.storageKey) || "[]"
                );

                if (Array.isArray(parsed)) {
                    this.history = parsed
                        .filter(value => typeof value === "string")
                        .slice(-this.options.historyLimit);
                }
            } catch (error) {
                this.history = [];
            }

            this.historyIndex = this.history.length;
        }

        persistHistory() {
            if (!this.options.persistHistory) {
                return;
            }

            try {
                if (!this.storage) {
                    return;
                }

                this.storage.setItem(
                    this.storageKey,
                    JSON.stringify(this.history)
                );
            } catch (error) {
                // Storage is optional.
            }
        }

        navigateHistory(direction) {
            if (!this.history.length) {
                return;
            }

            this.historyIndex = Math.max(
                0,
                Math.min(
                    this.history.length,
                    this.historyIndex + direction
                )
            );

            this.elements.input.value =
                this.historyIndex === this.history.length
                    ? ""
                    : this.history[this.historyIndex];

            const length = this.elements.input.value.length;
            this.elements.input.setSelectionRange(length, length);
        }

        complete() {
            const value = this.elements.input.value;
            const parsed = value.trim().split(/\s+/);
            const first = parsed[0] || "";

            if (parsed.length === 1) {
                const candidates = this.commandRegistry.complete(first);

                if (candidates.length === 1) {
                    this.elements.input.value = `${candidates[0]} `;
                    this.hideCompletion();
                    return;
                }

                this.showCompletion(candidates);
                return;
            }

            const command = this.commandRegistry.get(first);

            if (command?.completer) {
                Promise.resolve(
                    command.completer({
                        app: this,
                        value,
                        tokens: parsed,
                        context: this.context
                    })
                ).then(candidates =>
                    this.showCompletion(candidates || [])
                );
            }
        }

        showCompletion(candidates) {
            if (!this.elements.completion) {
                if (candidates.length) {
                    this.write(
                        candidates.join("    "),
                        "completion",
                        { preformatted: true }
                    );
                }
                return;
            }

            this.elements.completion.replaceChildren();

            for (const candidate of candidates) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "terminal-completion-item";
                button.setAttribute("role", "option");
                button.textContent = String(candidate);
                button.addEventListener("click", () => {
                    this.elements.input.value = `${candidate} `;
                    this.hideCompletion();
                    this.focus();
                }, { once: true });
                this.elements.completion.appendChild(button);
            }

            this.elements.completion.hidden = candidates.length === 0;
        }

        hideCompletion() {
            if (!this.elements.completion) {
                return;
            }

            this.elements.completion.hidden = true;
            this.elements.completion.replaceChildren();
        }

        focus() {
            if (!this.destroyed && !this.elements.input.disabled) {
                this.elements.input.focus({ preventScroll: true });
            }
        }

        async copyOutput() {
            try {
                await navigator.clipboard.writeText(
                    this.elements.output.innerText
                );
                this.setStatus("Copied", "success");
            } catch (error) {
                this.write("Unable to copy terminal output.", "error");
            }

            window.setTimeout(() =>
                this.setStatus("Ready", "ready"),
                1200
            );
        }

        async toggleFullscreen(button) {
            try {
                if (document.fullscreenElement === this.elements.shell) {
                    await document.exitFullscreen();
                    button.setAttribute("aria-pressed", "false");
                } else {
                    await this.elements.shell.requestFullscreen();
                    button.setAttribute("aria-pressed", "true");
                }
            } catch (error) {
                const enabled =
                    this.elements.shell.classList.toggle(
                        "terminal-fullscreen-fallback"
                    );

                button.setAttribute(
                    "aria-pressed",
                    String(enabled)
                );
            }
        }

        async restart() {
            if (this.destroyed) {
                throw new Error(
                    "Cannot restart a destroyed terminal application."
                );
            }

            this.metrics.restarts += 1;
            this.executionAbortController?.abort(
                new DOMException(
                    "Terminal restarting.",
                    "AbortError"
                )
            );
            this.clear();
            this.setStatus("Restarting", "loading");

            for (const instance of this.moduleInstances.values()) {
                await invokeCompatible(
                    instance,
                    ["restart", "reset", "refresh"],
                    this.context
                );
            }

            this.printWelcome();
            this.setStatus("Ready", "ready");
        }

        async destroy() {
            if (this.destroyed) {
                return;
            }

            this.executionAbortController?.abort(
                new DOMException(
                    "Terminal application destroyed.",
                    "AbortError"
                )
            );
            this.abortController.abort();

            for (const instance of [...this.moduleInstances.values()].reverse()) {
                try {
                    await invokeCompatible(
                        instance,
                        ["destroy", "dispose", "unmount", "stop"],
                        this.context
                    );
                } catch (error) {
                    console.warn(
                        "[SpeciedexTerminal] Module cleanup failed:",
                        error
                    );
                }
            }

            this.workers.destroy();
            this.moduleInstances.clear();
            this.destroyed = true;
            this.mounted = false;
            this.mounting = false;
            this.root.dataset.terminalReady = "false";
            this.root.dataset.terminalState = "destroyed";
            delete this.root[INSTANCE_SYMBOL];
            instances.delete(this);

            emit(this.root, "speciedex:terminal-application-destroyed", {
                app: this
            });
        }
    }

    async function create(root, options = {}) {
        const app = new SpeciedexTerminalApplication(root, options);
        await app.mount();
        return app;
    }

    async function mount(root, options = {}) {
        return create(root, options);
    }

    async function initialize(root, options = {}) {
        return create(root, options);
    }

    async function initializeAll(context = document, options = {}) {
        const roots = [];

        if (isElement(context) && context.matches(ROOT_SELECTOR)) {
            roots.push(context);
        }

        if (context.querySelectorAll) {
            roots.push(...context.querySelectorAll(ROOT_SELECTOR));
        }

        const results = [];

        for (const root of [...new Set(roots)]) {
            results.push(await create(root, options));
        }

        return results;
    }

    function use(plugin) {
        if (!plugin) {
            throw new TypeError("A terminal plugin is required.");
        }

        plugins.add(plugin);

        for (const app of instances) {
            invokeCompatible(
                plugin,
                ["mount", "install", "initialize", "init", "use"],
                app.context
            ).catch(error => {
                console.error(
                    "[SpeciedexTerminal] Plugin installation failed:",
                    error
                );
            });
        }

        return () => plugins.delete(plugin);
    }

    function getInstance(root) {
        if (!isElement(root)) {
            return null;
        }

        return root[INSTANCE_SYMBOL] || null;
    }

    function registerCommand(definition) {
        const registered = [];

        for (const app of instances) {
            registered.push(
                app.commandRegistry.register(definition)
            );
        }

        return registered[0] || null;
    }

    function unregisterCommand(name) {
        let removed = false;

        for (const app of instances) {
            removed =
                app.commandRegistry.unregister(name) ||
                removed;
        }

        return removed;
    }

    function status() {
        return {
            version: VERSION,
            instances: instances.size,
            plugins: plugins.size,
            applications: [...instances].map(app => ({
                mounted: app.mounted,
                destroyed: app.destroyed,
                modules: app.modules.size,
                mountedModules: app.moduleInstances.size,
                commands: app.commandRegistry.list({
                    includeHidden: true
                }).length,
                workers: app.workers.status()
            }))
        };
    }

    window[APP_NAME] = Object.freeze({
        VERSION,
        Application: SpeciedexTerminalApplication,
        CommandRegistry,
        WorkerPool,
        MODULE_GROUPS,
        COMMAND_ALIASES,
        ROOT_SELECTOR,
        create,
        mount,
        initialize,
        initializeAll,
        use,
        registerCommand,
        unregisterCommand,
        getInstance,
        status,
        parseCommand,
        tokenize,
        discoverModule,
        discoverAllModules,
        getInstances() {
            return [...instances];
        }
    });

    document.dispatchEvent(
        new CustomEvent("speciedex:terminal-application-available", {
            detail: {
                application: window[APP_NAME],
                version: VERSION
            }
        })
    );
})(window, document);
