/*
========================================================================
Speciedex.org
SpeciedexTerminal Core
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D

Licensed under the MIT License.

========================================================================
*/
(function (window, document) {
    "use strict";

    const GLOBAL_NAME = "SpeciedexTerminal";
    const VERSION = "1.0.0";
    const DEFAULT_SELECTOR = "[data-speciedex-terminal], [data-terminal]";
    const INSTANCE_KEY = "__speciedexTerminalInstance";

    const registry = new Map();
    const instances = new Set();

    function isElement(value) {
        return value instanceof Element;
    }

    function toArray(value) {
        if (Array.isArray(value)) {
            return value;
        }

        if (value === null || value === undefined) {
            return [];
        }

        return [value];
    }

    function normalizeText(value) {
        return String(value ?? "").replace(/\r\n?/g, "\n");
    }

    function escapeHTML(value) {
        return normalizeText(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function tokenize(input) {
        const tokens = [];
        let token = "";
        let quote = null;
        let escaped = false;

        for (const character of normalizeText(input).trim()) {
            if (escaped) {
                token += character;
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
                    token += character;
                }
                continue;
            }

            if (character === '"' || character === "'") {
                quote = character;
                continue;
            }

            if (/\s/.test(character)) {
                if (token.length) {
                    tokens.push(token);
                    token = "";
                }
                continue;
            }

            token += character;
        }

        if (escaped) {
            token += "\\";
        }

        if (quote) {
            throw new Error("Unterminated quoted string.");
        }

        if (token.length) {
            tokens.push(token);
        }

        return tokens;
    }

    function parseBoolean(value, fallback = false) {
        if (value === undefined || value === null || value === "") {
            return fallback;
        }

        return !["false", "0", "no", "off"].includes(String(value).toLowerCase());
    }

    function clampInteger(value, fallback, minimum, maximum) {
        const parsed = Number.parseInt(value, 10);

        if (!Number.isFinite(parsed)) {
            return fallback;
        }

        return Math.min(maximum, Math.max(minimum, parsed));
    }

    function emit(target, name, detail = {}) {
        target.dispatchEvent(new CustomEvent(name, {
            bubbles: true,
            detail
        }));
    }

    function safeStorage() {
        try {
            const probe = "__speciedex_terminal_probe__";
            window.localStorage.setItem(probe, probe);
            window.localStorage.removeItem(probe);
            return window.localStorage;
        } catch (error) {
            return null;
        }
    }

    class Terminal {
        constructor(root, options = {}) {
            if (!isElement(root)) {
                throw new TypeError("SpeciedexTerminal requires a valid root element.");
            }

            if (root[INSTANCE_KEY]) {
                return root[INSTANCE_KEY];
            }

            this.root = root;
            this.options = this.resolveOptions(options);
            this.storage = safeStorage();
            this.history = [];
            this.historyIndex = 0;
            this.commandCounter = 0;
            this.destroyed = false;
            this.busy = false;
            this.bound = {};
            this.elements = {};
            this.commandRegistry = new Map();

            this.captureElements();
            this.installCommands();
            this.restoreHistory();
            this.bindEvents();
            this.setStatus("Ready", "ready");
            this.updateFooter();
            this.removeBootstrapMessage();
            this.printWelcome();

            root[INSTANCE_KEY] = this;
            root.dataset.terminalReady = "true";
            instances.add(this);

            emit(root, "speciedex:terminal-ready", {
                terminal: this,
                version: VERSION
            });
        }

        resolveOptions(options) {
            const dataset = this.root.dataset;
            const instanceName = dataset.terminalInstance || "default";

            return {
                instanceName,
                promptUser: options.promptUser || dataset.terminalPromptUser || "public",
                promptHost: options.promptHost || dataset.terminalPromptHost || "speciedex",
                promptPath: options.promptPath || dataset.terminalPromptPath || "~",
                promptSymbol: options.promptSymbol || dataset.terminalPromptSymbol || "$",
                historyEnabled: parseBoolean(dataset.terminalHistory, true),
                completionEnabled: parseBoolean(dataset.terminalCompletion, true),
                persistHistory: parseBoolean(dataset.terminalPersistHistory, true),
                autofocus: parseBoolean(dataset.terminalAutofocus, false),
                maxLines: clampInteger(dataset.terminalMaxLines, 500, 25, 5000),
                maxHistory: clampInteger(dataset.terminalMaxHistory, 100, 10, 1000),
                storageKey: `speciedex-terminal:history:${instanceName}`,
                welcome: options.welcome !== false
            };
        }

        captureElements() {
            const query = (selector) => this.root.querySelector(selector);

            this.elements.shell = query("[data-terminal-shell]") || this.root;
            this.elements.screen = query("[data-terminal-screen]");
            this.elements.output = query("[data-terminal-output]");
            this.elements.form = query("[data-terminal-form]");
            this.elements.input = query("[data-terminal-input]");
            this.elements.prompt = query("[data-terminal-prompt]");
            this.elements.status = query("[data-terminal-status]");
            this.elements.statusIndicator = query("[data-terminal-status-indicator]");
            this.elements.completion = query("[data-terminal-completion]");
            this.elements.hint = query("[data-terminal-hint]");
            this.elements.provider = query("[data-terminal-provider]");
            this.elements.recordCount = query("[data-terminal-record-count]");
            this.elements.networkStatus = query("[data-terminal-network-status]");
            this.elements.version = query("[data-terminal-version]");

            if (!this.elements.output || !this.elements.form || !this.elements.input) {
                throw new Error(
                    "SpeciedexTerminal markup must provide data-terminal-output, " +
                    "data-terminal-form, and data-terminal-input."
                );
            }

            this.updatePrompt();
        }

        installCommands() {
            for (const [name, definition] of registry.entries()) {
                this.commandRegistry.set(name, definition);
            }

            const builtins = [
                {
                    name: "help",
                    aliases: ["?"],
                    description: "List commands or show help for one command.",
                    usage: "help [command]",
                    handler: ({ args }) => this.commandHelp(args)
                },
                {
                    name: "clear",
                    aliases: ["cls"],
                    description: "Clear the terminal display.",
                    usage: "clear",
                    handler: () => this.clear()
                },
                {
                    name: "history",
                    description: "Display command history.",
                    usage: "history",
                    handler: () => this.commandHistory()
                },
                {
                    name: "about",
                    description: "Describe SpeciedexTerminal.",
                    usage: "about",
                    handler: () => this.commandAbout()
                },
                {
                    name: "status",
                    description: "Display terminal and loader status.",
                    usage: "status",
                    handler: () => this.commandStatus()
                },
                {
                    name: "version",
                    aliases: ["--version", "-v"],
                    description: "Display terminal version information.",
                    usage: "version",
                    handler: () => this.write(`SpeciedexTerminal ${VERSION}`, "success")
                },
                {
                    name: "printf",
                    description: "Print text to the terminal.",
                    usage: "printf <text>",
                    handler: ({ args }) => this.write(args.join(" "))
                },
                {
                    name: "commands",
                    description: "Display registered command names.",
                    usage: "commands",
                    handler: () => this.write(
                        [...this.commandRegistry.keys()].sort().join("\n"),
                        "output",
                        { preformatted: true }
                    )
                }
            ];

            for (const definition of builtins) {
                this.registerCommand(definition);
            }
        }

        registerCommand(definition) {
            const normalized = Terminal.normalizeCommand(definition);
            this.commandRegistry.set(normalized.name, normalized);

            for (const alias of normalized.aliases) {
                this.commandRegistry.set(alias, {
                    ...normalized,
                    aliasFor: normalized.name
                });
            }

            return this;
        }

        unregisterCommand(name) {
            const definition = this.commandRegistry.get(name);

            if (!definition) {
                return false;
            }

            this.commandRegistry.delete(definition.name);

            for (const alias of definition.aliases || []) {
                this.commandRegistry.delete(alias);
            }

            return true;
        }

        restoreHistory() {
            if (!this.options.persistHistory || !this.storage) {
                return;
            }

            try {
                const stored = JSON.parse(
                    this.storage.getItem(this.options.storageKey) || "[]"
                );

                if (Array.isArray(stored)) {
                    this.history = stored
                        .filter((item) => typeof item === "string")
                        .slice(-this.options.maxHistory);
                }
            } catch (error) {
                this.history = [];
            }

            this.historyIndex = this.history.length;
        }

        persistHistory() {
            if (!this.options.persistHistory || !this.storage) {
                return;
            }

            try {
                this.storage.setItem(
                    this.options.storageKey,
                    JSON.stringify(this.history.slice(-this.options.maxHistory))
                );
            } catch (error) {
                // Storage failure must not interrupt terminal use.
            }
        }

        bindEvents() {
            this.bound.submit = (event) => {
                event.preventDefault();
                this.execute(this.elements.input.value);
            };

            this.bound.keydown = (event) => this.handleKeydown(event);
            this.bound.screenClick = () => this.focus();
            this.bound.toolbar = (event) => this.handleToolbarAction(event);

            this.elements.form.addEventListener("submit", this.bound.submit);
            this.elements.input.addEventListener("keydown", this.bound.keydown);

            if (this.elements.screen) {
                this.elements.screen.addEventListener("click", this.bound.screenClick);
            }

            this.root.addEventListener("click", this.bound.toolbar);

            if (this.options.autofocus) {
                window.requestAnimationFrame(() => this.focus());
            }
        }

        handleToolbarAction(event) {
            const button = event.target.closest("[data-terminal-action]");

            if (!button || !this.root.contains(button)) {
                return;
            }

            const action = button.dataset.terminalAction;

            switch (action) {
                case "help":
                    this.execute("help");
                    break;
                case "clear":
                    this.clear();
                    break;
                case "restart":
                    this.restart();
                    break;
                case "copy":
                    this.copyOutput();
                    break;
                case "fullscreen":
                    this.toggleFullscreen(button);
                    break;
                default:
                    emit(this.root, "speciedex:terminal-action", {
                        terminal: this,
                        action
                    });
            }
        }

        handleKeydown(event) {
            if (event.key === "ArrowUp" && this.options.historyEnabled) {
                event.preventDefault();
                this.navigateHistory(-1);
                return;
            }

            if (event.key === "ArrowDown" && this.options.historyEnabled) {
                event.preventDefault();
                this.navigateHistory(1);
                return;
            }

            if (event.key === "Tab" && this.options.completionEnabled) {
                event.preventDefault();
                this.completeInput();
                return;
            }

            if (event.key === "Escape") {
                this.hideCompletion();
                this.elements.input.value = "";
                return;
            }

            if (event.key.toLowerCase() === "l" && event.ctrlKey) {
                event.preventDefault();
                this.clear();
            }
        }

        navigateHistory(direction) {
            if (!this.history.length) {
                return;
            }

            this.historyIndex = Math.max(
                0,
                Math.min(this.history.length, this.historyIndex + direction)
            );

            this.elements.input.value =
                this.historyIndex === this.history.length
                    ? ""
                    : this.history[this.historyIndex];

            window.requestAnimationFrame(() => {
                const length = this.elements.input.value.length;
                this.elements.input.setSelectionRange(length, length);
            });
        }

        completeInput() {
            const value = this.elements.input.value.trim();
            const firstToken = value.split(/\s+/, 1)[0].toLowerCase();
            const candidates = [...new Set(
                [...this.commandRegistry.values()]
                    .map((definition) => definition.name)
                    .filter((name) => name.startsWith(firstToken))
            )].sort();

            if (candidates.length === 1) {
                const remainder = value.slice(firstToken.length);
                this.elements.input.value = `${candidates[0]}${remainder || " "}`;
                this.hideCompletion();
                return;
            }

            if (candidates.length > 1) {
                this.showCompletion(candidates);
                this.write(candidates.join("    "), "output", {
                    preformatted: true,
                    transient: true
                });
            }
        }

        showCompletion(candidates) {
            const container = this.elements.completion;

            if (!container) {
                return;
            }

            container.replaceChildren();

            for (const command of candidates) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "terminal-completion-item";
                button.setAttribute("role", "option");
                button.dataset.terminalCompletionItem = "";
                button.textContent = command;
                button.addEventListener("click", () => {
                    this.elements.input.value = `${command} `;
                    this.hideCompletion();
                    this.focus();
                });
                container.appendChild(button);
            }

            container.hidden = false;
        }

        hideCompletion() {
            if (this.elements.completion) {
                this.elements.completion.hidden = true;
                this.elements.completion.replaceChildren();
            }
        }

        addHistory(command) {
            if (!this.options.historyEnabled || !command) {
                return;
            }

            if (this.history[this.history.length - 1] !== command) {
                this.history.push(command);
            }

            this.history = this.history.slice(-this.options.maxHistory);
            this.historyIndex = this.history.length;
            this.persistHistory();
        }

        async execute(rawCommand) {
            if (this.busy || this.destroyed) {
                return;
            }

            const commandText = normalizeText(rawCommand).trim();
            this.elements.input.value = "";
            this.hideCompletion();

            if (!commandText) {
                return;
            }

            this.addHistory(commandText);
            this.writeCommand(commandText);

            let tokens;

            try {
                tokens = tokenize(commandText);
            } catch (error) {
                this.write(error.message, "error");
                return;
            }

            const name = (tokens.shift() || "").toLowerCase();
            const definition = this.commandRegistry.get(name);

            if (!definition) {
                this.write(
                    `Command not found: ${name}\nEnter "help" to list available commands.`,
                    "error",
                    { preformatted: true }
                );
                return;
            }

            this.setBusy(true);

            try {
                const result = await definition.handler({
                    terminal: this,
                    command: definition.aliasFor || definition.name,
                    invokedAs: name,
                    args: tokens,
                    raw: commandText,
                    write: this.write.bind(this),
                    clear: this.clear.bind(this),
                    setStatus: this.setStatus.bind(this)
                });

                if (result !== undefined && result !== null && result !== "") {
                    this.renderResult(result);
                }

                emit(this.root, "speciedex:terminal-command", {
                    terminal: this,
                    command: name,
                    args: tokens,
                    result
                });
            } catch (error) {
                console.error("[SpeciedexTerminal] Command failed:", error);
                this.write(
                    error instanceof Error ? error.message : String(error),
                    "error"
                );
            } finally {
                this.setBusy(false);
                this.focus();
            }
        }

        renderResult(result) {
            if (isElement(result)) {
                this.appendEntry(result);
                return;
            }

            if (typeof result === "object") {
                this.write(JSON.stringify(result, null, 2), "output", {
                    preformatted: true
                });
                return;
            }

            this.write(String(result));
        }

        writeCommand(commandText) {
            const entry = document.createElement("div");
            entry.className = "terminal-entry terminal-entry-command";

            const prompt = document.createElement("span");
            prompt.className = "terminal-entry-prompt";
            prompt.textContent = this.getPromptText();

            const command = document.createElement("span");
            command.className = "terminal-entry-command";
            command.textContent = commandText;

            entry.append(prompt, document.createTextNode(" "), command);
            this.appendEntry(entry);
        }

        write(content, type = "output", options = {}) {
            const entry = document.createElement(
                options.preformatted ? "pre" : "div"
            );

            entry.className = [
                "terminal-entry",
                `terminal-entry-${type}`,
                options.transient ? "terminal-entry-transient" : ""
            ].filter(Boolean).join(" ");

            if (options.html === true) {
                entry.innerHTML = normalizeText(content);
            } else {
                entry.textContent = normalizeText(content);
            }

            this.appendEntry(entry);
            return entry;
        }

        appendEntry(entry) {
            this.elements.output.appendChild(entry);
            this.commandCounter += 1;
            this.trimOutput();
            this.scrollToBottom();
            return entry;
        }

        trimOutput() {
            while (this.elements.output.children.length > this.options.maxLines) {
                this.elements.output.firstElementChild?.remove();
            }
        }

        scrollToBottom() {
            window.requestAnimationFrame(() => {
                this.elements.output.scrollTop = this.elements.output.scrollHeight;

                if (this.elements.screen) {
                    this.elements.screen.scrollTop = this.elements.screen.scrollHeight;
                }
            });
        }

        clear() {
            this.elements.output.replaceChildren();
            this.commandCounter = 0;
            emit(this.root, "speciedex:terminal-cleared", { terminal: this });
        }

        restart() {
            this.clear();
            this.setStatus("Ready", "ready");
            this.printWelcome();
            emit(this.root, "speciedex:terminal-restarted", { terminal: this });
        }

        printWelcome() {
            if (!this.options.welcome) {
                return;
            }

            this.write(
                [
                    `SpeciedexTerminal ${VERSION}`,
                    "Open biodiversity research, discovery, and archival infrastructure.",
                    'Enter "help" to list available commands.'
                ].join("\n"),
                "system",
                { preformatted: true }
            );
        }

        removeBootstrapMessage() {
            this.root.querySelector("[data-terminal-bootstrap-message]")?.remove();
        }

        updatePrompt() {
            const user = this.root.querySelector("[data-terminal-prompt-user]");
            const host = this.root.querySelector("[data-terminal-prompt-host]");
            const path = this.root.querySelector("[data-terminal-prompt-path]");
            const symbol = this.root.querySelector("[data-terminal-prompt-symbol]");

            if (user) user.textContent = this.options.promptUser;
            if (host) host.textContent = this.options.promptHost;
            if (path) path.textContent = `:${this.options.promptPath}`;
            if (symbol) symbol.textContent = this.options.promptSymbol;
        }

        getPromptText() {
            return `${this.options.promptUser}@${this.options.promptHost}:${this.options.promptPath}${this.options.promptSymbol}`;
        }

        updateFooter() {
            if (this.elements.version) {
                this.elements.version.textContent = `Version: ${VERSION}`;
            }

            if (this.elements.provider) {
                this.elements.provider.textContent = "Provider: local";
            }

            if (this.elements.networkStatus) {
                this.elements.networkStatus.textContent =
                    navigator.onLine ? "Network: online" : "Network: offline";
            }
        }

        setStatus(message, state = "ready") {
            if (this.elements.status) {
                this.elements.status.textContent = message;
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
            this.setStatus(this.busy ? "Working" : "Ready", this.busy ? "busy" : "ready");
        }

        focus() {
            if (!this.destroyed && !this.elements.input.disabled) {
                this.elements.input.focus({ preventScroll: true });
            }
        }

        async copyOutput() {
            const text = this.elements.output.innerText;

            try {
                await navigator.clipboard.writeText(text);
                this.setStatus("Copied", "success");
            } catch (error) {
                this.write("Unable to copy terminal output.", "error");
            } finally {
                window.setTimeout(() => this.setStatus("Ready", "ready"), 1200);
            }
        }

        async toggleFullscreen(button) {
            const shell = this.elements.shell;

            try {
                if (document.fullscreenElement === shell) {
                    await document.exitFullscreen();
                    button.setAttribute("aria-pressed", "false");
                } else {
                    await shell.requestFullscreen();
                    button.setAttribute("aria-pressed", "true");
                }
            } catch (error) {
                shell.classList.toggle("terminal-fullscreen-fallback");
                button.setAttribute(
                    "aria-pressed",
                    String(shell.classList.contains("terminal-fullscreen-fallback"))
                );
            }
        }

        commandHelp(args) {
            if (args.length) {
                const name = args[0].toLowerCase();
                const definition = this.commandRegistry.get(name);

                if (!definition) {
                    this.write(`No help is available for "${name}".`, "error");
                    return;
                }

                const lines = [
                    definition.name,
                    definition.description,
                    `Usage: ${definition.usage || definition.name}`
                ];

                if (definition.aliases.length) {
                    lines.push(`Aliases: ${definition.aliases.join(", ")}`);
                }

                this.write(lines.join("\n"), "output", { preformatted: true });
                return;
            }

            const unique = new Map();

            for (const definition of this.commandRegistry.values()) {
                unique.set(definition.name, definition);
            }

            const lines = [...unique.values()]
                .sort((a, b) => a.name.localeCompare(b.name))
                .map((definition) => {
                    return `${definition.name.padEnd(18)} ${definition.description}`;
                });

            this.write(lines.join("\n"), "output", { preformatted: true });
        }

        commandHistory() {
            if (!this.history.length) {
                this.write("No command history is available.");
                return;
            }

            const lines = this.history.map((command, index) => {
                return `${String(index + 1).padStart(4)}  ${command}`;
            });

            this.write(lines.join("\n"), "output", { preformatted: true });
        }

        commandAbout() {
            this.write(
                [
                    "SpeciedexTerminal",
                    "A browser-based command interface for the Speciedex",
                    "biodiversity index, archives, providers, and public APIs.",
                    "",
                    "Project: https://speciedex.org/",
                    "License: MIT"
                ].join("\n"),
                "output",
                { preformatted: true }
            );
        }

        commandStatus() {
            const loader = window.SpeciedexTerminalLoader;
            const status = {
                terminal: "ready",
                version: VERSION,
                network: navigator.onLine ? "online" : "offline",
                commands: new Set(
                    [...this.commandRegistry.values()].map((item) => item.name)
                ).size,
                history: this.history.length,
                loader: loader?.state || "unavailable",
                modules: loader?.loadedModules?.length || 0
            };

            this.write(JSON.stringify(status, null, 2), "output", {
                preformatted: true
            });
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.elements.form.removeEventListener("submit", this.bound.submit);
            this.elements.input.removeEventListener("keydown", this.bound.keydown);
            this.elements.screen?.removeEventListener("click", this.bound.screenClick);
            this.root.removeEventListener("click", this.bound.toolbar);

            this.destroyed = true;
            this.root.dataset.terminalReady = "false";
            delete this.root[INSTANCE_KEY];
            instances.delete(this);

            emit(this.root, "speciedex:terminal-destroyed", {
                terminal: this
            });
        }

        static normalizeCommand(definition) {
            if (!definition || typeof definition !== "object") {
                throw new TypeError("Command definition must be an object.");
            }

            const name = String(definition.name || "").trim().toLowerCase();

            if (!/^[a-z0-9][a-z0-9:_-]*$/.test(name)) {
                throw new Error(`Invalid terminal command name: ${name || "(empty)"}`);
            }

            if (typeof definition.handler !== "function") {
                throw new TypeError(`Command "${name}" requires a handler function.`);
            }

            return {
                name,
                aliases: toArray(definition.aliases)
                    .map((alias) => String(alias).trim().toLowerCase())
                    .filter(Boolean),
                description: String(definition.description || "No description."),
                usage: String(definition.usage || name),
                handler: definition.handler,
                source: definition.source || "core"
            };
        }
    }

    function registerCommand(definition) {
        const normalized = Terminal.normalizeCommand(definition);
        registry.set(normalized.name, normalized);

        for (const instance of instances) {
            instance.registerCommand(normalized);
        }

        document.dispatchEvent(new CustomEvent("speciedex:terminal-command-registered", {
            detail: { command: normalized }
        }));

        return normalized;
    }

    function unregisterCommand(name) {
        const normalizedName = String(name || "").trim().toLowerCase();
        const definition = registry.get(normalizedName);

        if (!definition) {
            return false;
        }

        registry.delete(normalizedName);

        for (const instance of instances) {
            instance.unregisterCommand(normalizedName);
        }

        return true;
    }

    function create(root, options = {}) {
        return new Terminal(root, options);
    }

    function initializeAll(context = document, options = {}) {
        const roots = [];

        if (isElement(context) && context.matches(DEFAULT_SELECTOR)) {
            roots.push(context);
        }

        if (context.querySelectorAll) {
            roots.push(...context.querySelectorAll(DEFAULT_SELECTOR));
        }

        return [...new Set(roots)].map((root) => {
            try {
                return create(root, options);
            } catch (error) {
                console.error("[SpeciedexTerminal] Initialization failed:", error);
                root.dataset.terminalReady = "error";
                root.dataset.terminalError = error.message;
                return null;
            }
        }).filter(Boolean);
    }

    window[GLOBAL_NAME] = Object.freeze({
        VERSION,
        Terminal,
        create,
        initializeAll,
        registerCommand,
        unregisterCommand,
        getInstances: () => [...instances],
        getCommands: () => [...registry.values()],
        tokenize,
        escapeHTML
    });

    document.dispatchEvent(new CustomEvent("speciedex:terminal-core-ready", {
        detail: {
            version: VERSION,
            api: window[GLOBAL_NAME]
        }
    }));
})(window, document);
