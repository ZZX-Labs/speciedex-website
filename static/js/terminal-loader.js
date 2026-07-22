/*
========================================================================
Speciedex.org
SpeciedexTerminal Loader
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D

Licensed under the MIT License.

========================================================================
*/
(function (window, document) {
    "use strict";

    const GLOBAL_NAME = "SpeciedexTerminalLoader";
    const BASE_PATH = "/static/js/terminal/";
    const MANIFEST_URL = `${BASE_PATH}manifest.json`;

    const DEFAULT_MANIFEST = Object.freeze({
        version: 1,
        basePath: BASE_PATH,
        styles: [],
        modules: []
    });

    const loadedURLs = new Set();
    const pendingURLs = new Map();
    const loadedModules = [];
    const failedModules = [];

    let state = "idle";
    let manifest = null;
    let loadPromise = null;

    function emit(name, detail = {}) {
        document.dispatchEvent(new CustomEvent(name, { detail }));
    }

    function normalizeURL(path, basePath = BASE_PATH) {
        if (!path) {
            throw new Error("Terminal resource path cannot be empty.");
        }

        if (/^(?:https?:)?\/\//i.test(path) || path.startsWith("/")) {
            return new URL(path, window.location.origin).href;
        }

        return new URL(path, new URL(basePath, window.location.origin)).href;
    }

    function normalizeManifest(value) {
        const source = value && typeof value === "object" ? value : {};

        return {
            version: Number(source.version) || 1,
            basePath: source.basePath || BASE_PATH,
            styles: Array.isArray(source.styles) ? source.styles : [],
            modules: Array.isArray(source.modules) ? source.modules : []
        };
    }

    async function fetchManifest(url = MANIFEST_URL) {
        try {
            const response = await fetch(url, {
                method: "GET",
                cache: "no-store",
                credentials: "same-origin",
                headers: {
                    Accept: "application/json"
                }
            });

            if (!response.ok) {
                if (response.status === 404) {
                    return normalizeManifest(DEFAULT_MANIFEST);
                }

                throw new Error(
                    `Terminal manifest request failed with HTTP ${response.status}.`
                );
            }

            return normalizeManifest(await response.json());
        } catch (error) {
            console.warn(
                "[SpeciedexTerminalLoader] Using the empty fallback manifest:",
                error
            );
            return normalizeManifest(DEFAULT_MANIFEST);
        }
    }

    function loadScript(url, attributes = {}) {
        const normalized = normalizeURL(url);

        if (loadedURLs.has(normalized)) {
            return Promise.resolve(normalized);
        }

        if (pendingURLs.has(normalized)) {
            return pendingURLs.get(normalized);
        }

        const promise = new Promise((resolve, reject) => {
            const existing = [...document.scripts].find(
                (script) => script.src === normalized
            );

            if (existing?.dataset.loaded === "true") {
                loadedURLs.add(normalized);
                resolve(normalized);
                return;
            }

            const script = existing || document.createElement("script");

            script.src = normalized;
            script.async = false;
            script.defer = false;
            script.dataset.speciedexTerminalResource = "script";

            for (const [name, value] of Object.entries(attributes)) {
                if (value !== undefined && value !== null) {
                    script.setAttribute(name, String(value));
                }
            }

            const cleanup = () => {
                script.removeEventListener("load", onLoad);
                script.removeEventListener("error", onError);
                pendingURLs.delete(normalized);
            };

            const onLoad = () => {
                script.dataset.loaded = "true";
                loadedURLs.add(normalized);
                cleanup();
                resolve(normalized);
            };

            const onError = () => {
                cleanup();
                reject(new Error(`Unable to load terminal script: ${normalized}`));
            };

            script.addEventListener("load", onLoad, { once: true });
            script.addEventListener("error", onError, { once: true });

            if (!existing) {
                document.head.appendChild(script);
            }
        });

        pendingURLs.set(normalized, promise);
        return promise;
    }

    function loadStyle(url, attributes = {}) {
        const normalized = normalizeURL(url);

        if (loadedURLs.has(normalized)) {
            return Promise.resolve(normalized);
        }

        if (pendingURLs.has(normalized)) {
            return pendingURLs.get(normalized);
        }

        const promise = new Promise((resolve, reject) => {
            const existing = [...document.styleSheets]
                .map((sheet) => sheet.ownerNode)
                .find((node) => node?.href === normalized);

            if (existing) {
                loadedURLs.add(normalized);
                resolve(normalized);
                return;
            }

            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = normalized;
            link.dataset.speciedexTerminalResource = "style";

            for (const [name, value] of Object.entries(attributes)) {
                if (value !== undefined && value !== null) {
                    link.setAttribute(name, String(value));
                }
            }

            const cleanup = () => {
                link.removeEventListener("load", onLoad);
                link.removeEventListener("error", onError);
                pendingURLs.delete(normalized);
            };

            const onLoad = () => {
                loadedURLs.add(normalized);
                cleanup();
                resolve(normalized);
            };

            const onError = () => {
                cleanup();
                reject(new Error(`Unable to load terminal stylesheet: ${normalized}`));
            };

            link.addEventListener("load", onLoad, { once: true });
            link.addEventListener("error", onError, { once: true });
            document.head.appendChild(link);
        });

        pendingURLs.set(normalized, promise);
        return promise;
    }

    function normalizeModule(entry, index) {
        if (typeof entry === "string") {
            return {
                name: entry.replace(/^.*\//, "").replace(/\.js$/i, ""),
                path: entry,
                enabled: true,
                optional: false,
                dependencies: [],
                attributes: {}
            };
        }

        if (!entry || typeof entry !== "object") {
            throw new TypeError(`Invalid terminal module at index ${index}.`);
        }

        const path = entry.path || entry.src || entry.url;

        if (!path) {
            throw new Error(`Terminal module at index ${index} has no path.`);
        }

        return {
            name: String(
                entry.name ||
                path.replace(/^.*\//, "").replace(/\.js$/i, "")
            ),
            path,
            enabled: entry.enabled !== false,
            optional: entry.optional === true,
            dependencies: Array.isArray(entry.dependencies)
                ? entry.dependencies.map(String)
                : [],
            attributes: entry.attributes && typeof entry.attributes === "object"
                ? entry.attributes
                : {}
        };
    }

    function orderModules(entries) {
        const modules = entries.map(normalizeModule).filter((item) => item.enabled);
        const byName = new Map(modules.map((module) => [module.name, module]));
        const ordered = [];
        const permanent = new Set();
        const temporary = new Set();

        function visit(module) {
            if (permanent.has(module.name)) {
                return;
            }

            if (temporary.has(module.name)) {
                throw new Error(
                    `Circular terminal module dependency involving "${module.name}".`
                );
            }

            temporary.add(module.name);

            for (const dependencyName of module.dependencies) {
                const dependency = byName.get(dependencyName);

                if (!dependency) {
                    throw new Error(
                        `Terminal module "${module.name}" requires missing ` +
                        `dependency "${dependencyName}".`
                    );
                }

                visit(dependency);
            }

            temporary.delete(module.name);
            permanent.add(module.name);
            ordered.push(module);
        }

        for (const module of modules) {
            visit(module);
        }

        return ordered;
    }

    async function loadStyles(entries, basePath) {
        for (const entry of entries) {
            const definition = typeof entry === "string"
                ? { path: entry, optional: false, attributes: {} }
                : {
                    path: entry.path || entry.href || entry.url,
                    optional: entry.optional === true,
                    attributes: entry.attributes || {}
                };

            try {
                await loadStyle(
                    normalizeURL(definition.path, basePath),
                    definition.attributes
                );
            } catch (error) {
                if (!definition.optional) {
                    throw error;
                }

                console.warn("[SpeciedexTerminalLoader] Optional style failed:", error);
            }
        }
    }

    async function loadModules(entries, basePath) {
        const ordered = orderModules(entries);

        for (const module of ordered) {
            const url = normalizeURL(module.path, basePath);

            try {
                await loadScript(url, module.attributes);
                loadedModules.push({
                    name: module.name,
                    url
                });

                emit("speciedex:terminal-module-loaded", {
                    module: module.name,
                    url
                });
            } catch (error) {
                failedModules.push({
                    name: module.name,
                    url,
                    error
                });

                emit("speciedex:terminal-module-error", {
                    module: module.name,
                    url,
                    error
                });

                if (!module.optional) {
                    throw error;
                }

                console.warn(
                    `[SpeciedexTerminalLoader] Optional module "${module.name}" failed:`,
                    error
                );
            }
        }
    }

    async function performLoad(options = {}) {
        state = "loading";
        emit("speciedex:terminal-loader-start", { options });

        manifest = normalizeManifest(
            options.manifest ||
            await fetchManifest(options.manifestURL || MANIFEST_URL)
        );

        const basePath = options.basePath || manifest.basePath || BASE_PATH;
        const styles = options.styles || manifest.styles;
        const modules = options.modules || manifest.modules;

        await loadStyles(styles, basePath);
        await loadModules(modules, basePath);

        state = "ready";

        const result = {
            state,
            manifest,
            loadedModules: [...loadedModules],
            failedModules: [...failedModules]
        };

        emit("speciedex:terminal-loader-ready", result);
        return result;
    }

    function load(options = {}) {
        if (state === "ready" && !options.reload) {
            return Promise.resolve({
                state,
                manifest,
                loadedModules: [...loadedModules],
                failedModules: [...failedModules]
            });
        }

        if (loadPromise && !options.reload) {
            return loadPromise;
        }

        if (options.reload) {
            loadedModules.length = 0;
            failedModules.length = 0;
            state = "idle";
            loadPromise = null;
        }

        loadPromise = performLoad(options).catch((error) => {
            state = "error";
            emit("speciedex:terminal-loader-error", { error });
            loadPromise = null;
            throw error;
        });

        return loadPromise;
    }

    function registerModule(definition) {
        const current = manifest || normalizeManifest(DEFAULT_MANIFEST);
        current.modules.push(definition);
        manifest = current;
        return definition;
    }

    window[GLOBAL_NAME] = Object.freeze({
        VERSION: "1.0.0",
        BASE_PATH,
        MANIFEST_URL,
        load,
        loadScript,
        loadStyle,
        fetchManifest,
        registerModule,
        normalizeURL,
        get state() {
            return state;
        },
        get manifest() {
            return manifest;
        },
        get loadedModules() {
            return [...loadedModules];
        },
        get failedModules() {
            return [...failedModules];
        }
    });

    emit("speciedex:terminal-loader-available", {
        loader: window[GLOBAL_NAME]
    });
})(window, document);
