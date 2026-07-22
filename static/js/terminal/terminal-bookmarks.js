/*
========================================================================
Speciedex.org
Terminal Bookmarks
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Bookmarks";
    const SERVICE_NAME = "bookmarks";
    const STORAGE_KEY = "bookmarks";
    const STORAGE_VERSION = 1;
    const DEFAULT_LIMIT = 1000;

    function iso(value = Date.now()) {
        const date = value instanceof Date ? value : new Date(value);
        return Number.isNaN(date.getTime())
            ? new Date().toISOString()
            : date.toISOString();
    }

    function text(value) {
        return String(value ?? "").trim();
    }

    function normalizeTags(value) {
        const source = Array.isArray(value)
            ? value
            : text(value)
                .split(",");

        return Array.from(new Set(
            source
                .map((item) => text(item).toLowerCase())
                .filter(Boolean)
        ));
    }

    function makeID() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }

        if (window.crypto && typeof window.crypto.getRandomValues === "function") {
            const bytes = new Uint8Array(16);
            window.crypto.getRandomValues(bytes);
            bytes[6] = (bytes[6] & 0x0f) | 0x40;
            bytes[8] = (bytes[8] & 0x3f) | 0x80;

            const hex = Array.from(bytes, (byte) =>
                byte.toString(16).padStart(2, "0")
            ).join("");

            return [
                hex.slice(0, 8),
                hex.slice(8, 12),
                hex.slice(12, 16),
                hex.slice(16, 20),
                hex.slice(20)
            ].join("-");
        }

        return `bookmark-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function clone(value) {
        if (typeof structuredClone === "function") {
            try {
                return structuredClone(value);
            } catch (_error) {
                // Fall through to JSON cloning for plain data.
            }
        }

        return JSON.parse(JSON.stringify(value));
    }

    class Bookmarks {
        constructor(context, options = {}) {
            if (!context || typeof context !== "object") {
                throw new TypeError("A terminal context is required.");
            }

            this.context = context;
            this.storage = options.storage || context.storage || null;
            this.storageKey = text(options.storageKey) || STORAGE_KEY;
            this.limit = Number.isFinite(Number(options.limit))
                ? Math.max(1, Math.trunc(Number(options.limit)))
                : DEFAULT_LIMIT;
            this.items = [];

            this.load();
        }

        normalizeRecord(record = {}) {
            const label = text(record.label);
            const value = text(record.value);

            if (!label || !value) {
                return null;
            }

            const createdAt = iso(record.createdAt);
            const updatedAt = iso(record.updatedAt || createdAt);

            return {
                id: text(record.id) || makeID(),
                label,
                value,
                tags: normalizeTags(record.tags),
                note: text(record.note),
                createdAt,
                updatedAt,
                metadata:
                    record.metadata && typeof record.metadata === "object"
                        ? clone(record.metadata)
                        : {}
            };
        }

        load() {
            let payload = [];

            try {
                payload = this.storage?.get?.(this.storageKey, []) ?? [];
            } catch (error) {
                this.report("load", error);
                payload = [];
            }

            if (payload && !Array.isArray(payload) && Array.isArray(payload.items)) {
                payload = payload.items;
            }

            if (!Array.isArray(payload)) {
                payload = [];
            }

            const seen = new Set();
            this.items = payload
                .map((record) => this.normalizeRecord(record))
                .filter((record) => {
                    if (!record || seen.has(record.id)) {
                        return false;
                    }
                    seen.add(record.id);
                    return true;
                })
                .slice(0, this.limit);

            return this.list();
        }

        save() {
            const payload = {
                version: STORAGE_VERSION,
                updatedAt: iso(),
                items: this.items.map((item) => clone(item))
            };

            try {
                this.storage?.set?.(this.storageKey, payload);
            } catch (error) {
                this.report("save", error);
                throw error;
            }

            this.emit("saved", {
                count: this.items.length
            });

            return payload;
        }

        list(options = {}) {
            let result = this.items.slice();
            const query = text(options.query).toLowerCase();
            const tag = text(options.tag).toLowerCase();

            if (query) {
                result = result.filter((item) =>
                    item.label.toLowerCase().includes(query) ||
                    item.value.toLowerCase().includes(query) ||
                    item.note.toLowerCase().includes(query) ||
                    item.tags.some((itemTag) => itemTag.includes(query))
                );
            }

            if (tag) {
                result = result.filter((item) => item.tags.includes(tag));
            }

            const sort = text(options.sort || "newest").toLowerCase();
            result.sort((left, right) => {
                if (sort === "oldest") {
                    return left.createdAt.localeCompare(right.createdAt);
                }
                if (sort === "label") {
                    return left.label.localeCompare(right.label);
                }
                return right.createdAt.localeCompare(left.createdAt);
            });

            const limit = Number(options.limit);
            if (Number.isFinite(limit) && limit >= 0) {
                result = result.slice(0, Math.trunc(limit));
            }

            return result.map((item) => clone(item));
        }

        get(idOrLabel) {
            const needle = text(idOrLabel).toLowerCase();
            if (!needle) {
                return null;
            }

            const item = this.items.find((record) =>
                record.id.toLowerCase() === needle ||
                record.label.toLowerCase() === needle
            );

            return item ? clone(item) : null;
        }

        add(label, value, options = {}) {
            const normalizedLabel = text(label);
            const normalizedValue = text(value);

            if (!normalizedLabel) {
                throw new TypeError("A bookmark label is required.");
            }
            if (!normalizedValue) {
                throw new TypeError("A bookmark value is required.");
            }

            const duplicate = this.items.find((item) =>
                item.label.toLowerCase() === normalizedLabel.toLowerCase() &&
                item.value === normalizedValue
            );

            if (duplicate && options.allowDuplicate !== true) {
                return clone(duplicate);
            }

            if (this.items.length >= this.limit) {
                throw new RangeError(`Bookmark limit reached (${this.limit}).`);
            }

            const now = iso();
            const record = this.normalizeRecord({
                id: options.id,
                label: normalizedLabel,
                value: normalizedValue,
                tags: options.tags,
                note: options.note,
                metadata: options.metadata,
                createdAt: options.createdAt || now,
                updatedAt: options.updatedAt || now
            });

            this.items.push(record);
            this.save();
            this.emit("added", { bookmark: clone(record) });

            return clone(record);
        }

        update(idOrLabel, changes = {}) {
            const needle = text(idOrLabel).toLowerCase();
            const index = this.items.findIndex((item) =>
                item.id.toLowerCase() === needle ||
                item.label.toLowerCase() === needle
            );

            if (index < 0) {
                return null;
            }

            const current = this.items[index];
            const next = this.normalizeRecord({
                ...current,
                ...changes,
                id: current.id,
                createdAt: current.createdAt,
                updatedAt: iso()
            });

            if (!next) {
                throw new TypeError("Updated bookmark must retain a label and value.");
            }

            this.items[index] = next;
            this.save();
            this.emit("updated", { bookmark: clone(next) });

            return clone(next);
        }

        remove(idOrLabel) {
            const needle = text(idOrLabel).toLowerCase();
            if (!needle) {
                return null;
            }

            const index = this.items.findIndex((item) =>
                item.id.toLowerCase() === needle ||
                item.label.toLowerCase() === needle
            );

            if (index < 0) {
                return null;
            }

            const [removed] = this.items.splice(index, 1);
            this.save();
            this.emit("removed", { bookmark: clone(removed) });

            return clone(removed);
        }

        clear() {
            const count = this.items.length;
            this.items = [];
            this.save();
            this.emit("cleared", { count });
            return count;
        }

        export() {
            return {
                version: STORAGE_VERSION,
                exportedAt: iso(),
                count: this.items.length,
                items: this.list({ sort: "oldest" })
            };
        }

        import(payload, options = {}) {
            const records = Array.isArray(payload)
                ? payload
                : Array.isArray(payload?.items)
                    ? payload.items
                    : [];

            if (!records.length) {
                return { added: 0, skipped: 0 };
            }

            let added = 0;
            let skipped = 0;

            if (options.replace === true) {
                this.items = [];
            }

            for (const record of records) {
                const normalized = this.normalizeRecord(record);
                if (!normalized) {
                    skipped += 1;
                    continue;
                }

                const duplicate = this.items.some((item) =>
                    item.id === normalized.id ||
                    (
                        item.label.toLowerCase() === normalized.label.toLowerCase() &&
                        item.value === normalized.value
                    )
                );

                if (duplicate || this.items.length >= this.limit) {
                    skipped += 1;
                    continue;
                }

                this.items.push(normalized);
                added += 1;
            }

            this.save();
            this.emit("imported", { added, skipped });

            return { added, skipped };
        }

        emit(action, detail = {}) {
            const payload = {
                action,
                service: this,
                ...detail
            };

            this.context.events?.emit?.(`bookmarks:${action}`, payload);
            document.dispatchEvent(new CustomEvent(
                `speciedex:terminal-bookmarks-${action}`,
                { detail: payload }
            ));
        }

        report(phase, error) {
            this.context.log?.error?.("Terminal bookmarks error", {
                phase,
                error
            });

            document.dispatchEvent(new CustomEvent("speciedex:error", {
                detail: {
                    phase: `terminal-bookmarks:${phase}`,
                    error
                }
            }));
        }
    }

    function initialize(context) {
        if (!context || typeof context !== "object") {
            throw new TypeError("A terminal context is required.");
        }

        if (context.bookmarks instanceof Bookmarks) {
            return context.bookmarks;
        }

        const bookmarks = new Bookmarks(context);
        context.bookmarks = bookmarks;
        context.registerService?.(SERVICE_NAME, bookmarks);
        return bookmarks;
    }

    function outputJSON(writeJSON, write, value) {
        if (typeof writeJSON === "function") {
            return writeJSON(value);
        }
        if (typeof write === "function") {
            return write(JSON.stringify(value, null, 2));
        }
        return value;
    }

    function parseOptions(args) {
        const options = {};
        const positional = [];

        for (let index = 0; index < args.length; index += 1) {
            const item = args[index];

            if (item === "--tag") {
                options.tag = args[++index] || "";
            } else if (item === "--query" || item === "-q") {
                options.query = args[++index] || "";
            } else if (item === "--sort") {
                options.sort = args[++index] || "newest";
            } else if (item === "--limit") {
                options.limit = Number(args[++index]);
            } else if (item === "--tags") {
                options.tags = args[++index] || "";
            } else if (item === "--note") {
                options.note = args[++index] || "";
            } else if (item === "--replace") {
                options.replace = true;
            } else {
                positional.push(item);
            }
        }

        return { options, positional };
    }

    const commands = [{
        name: "bookmark",
        aliases: ["bookmarks", "bm"],
        category: "data",
        description: "Add, list, inspect, update, remove, clear, import, or export terminal bookmarks.",
        usage: [
            "bookmark add <label> <value> [--tags a,b] [--note text]",
            "bookmark list [--query text] [--tag tag] [--sort newest|oldest|label] [--limit n]",
            "bookmark show <id|label>",
            "bookmark update <id|label> [--label name] [--value value] [--tags a,b] [--note text]",
            "bookmark remove <id|label>",
            "bookmark clear",
            "bookmark export"
        ].join("\n"),
        handler: ({ args = [], context, writeJSON, write }) => {
            const bookmarks = context.bookmarks || initialize(context);
            const tokens = Array.from(args);
            const action = text(tokens.shift() || "list").toLowerCase();
            const parsed = parseOptions(tokens);
            const positional = parsed.positional;
            const options = parsed.options;

            if (action === "add") {
                const label = positional.shift();
                const value = positional.join(" ");
                const bookmark = bookmarks.add(label, value, options);
                write?.(`Bookmark added: ${bookmark.label}`, "success");
                return bookmark;
            }

            if (action === "list" || action === "ls") {
                return outputJSON(writeJSON, write, bookmarks.list(options));
            }

            if (action === "show" || action === "get") {
                const bookmark = bookmarks.get(positional.join(" "));
                if (!bookmark) {
                    throw new Error("Bookmark not found.");
                }
                return outputJSON(writeJSON, write, bookmark);
            }

            if (action === "remove" || action === "delete" || action === "rm") {
                const removed = bookmarks.remove(positional.join(" "));
                if (!removed) {
                    throw new Error("Bookmark not found.");
                }
                write?.(`Bookmark removed: ${removed.label}`, "success");
                return removed;
            }

            if (action === "clear") {
                const count = bookmarks.clear();
                write?.(`Removed ${count} bookmark${count === 1 ? "" : "s"}.`, "success");
                return count;
            }

            if (action === "export") {
                return outputJSON(writeJSON, write, bookmarks.export());
            }

            throw new Error(`Unknown bookmark action: ${action}`);
        }
    }];

    const api = Object.freeze({
        name: MODULE_NAME,
        service: SERVICE_NAME,
        Bookmarks,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalBookmarks = api;
    window.SpeciedexTerminalModules = window.SpeciedexTerminalModules || {};
    window.SpeciedexTerminalModules[MODULE_NAME] = api;

    document.dispatchEvent(new CustomEvent("speciedex:terminal-module-available", {
        detail: {
            name: MODULE_NAME,
            module: api
        }
    }));
})(window, document);
