/*
========================================================================
Speciedex.org
Terminal Network Visualization
========================================================================

Interactive network-topology renderer for Speciedex records. Supports explicit
nodes and links, inferred taxonomic/provider relationships, weighted traffic,
animated packets, clustering, filtering, selection, zoom, pan, responsive
high-DPI rendering, exports, diagnostics, and lifecycle control.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Network";
    const DEFAULT_WIDTH = 960;
    const DEFAULT_HEIGHT = 540;
    const DEFAULT_BACKGROUND = "#020a05";
    const DEFAULT_FOREGROUND = "#c0d674";
    const DEFAULT_HIGHLIGHT = "#eef7c8";
    const DEFAULT_EDGE = "#35503a";
    const DEFAULT_PACKET = "#e6a42b";
    const DEFAULT_NODE_RADIUS = 5;
    const DEFAULT_MAX_NODES = 2500;
    const DEFAULT_MAX_EDGES = 15000;
    const DEFAULT_CHARGE = 130;
    const DEFAULT_LINK_DISTANCE = 84;
    const DEFAULT_DAMPING = 0.88;
    const DEFAULT_ALPHA_DECAY = 0.018;
    const DEFAULT_MIN_ALPHA = 0.002;
    const MAX_LABELS = 180;

    function iso() {
        return new Date().toISOString();
    }

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function clone(value) {
        if (typeof structuredClone === "function") {
            try {
                return structuredClone(value);
            } catch (error) {
                /* Fall through. */
            }
        }

        if (value === null || value === undefined || typeof value !== "object") {
            return value;
        }

        try {
            return JSON.parse(JSON.stringify(value));
        } catch (error) {
            return value;
        }
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

    function parseNumber(value, fallback, minimum = -Infinity, maximum = Infinity) {
        const number = Number(value);

        if (!Number.isFinite(number)) {
            return fallback;
        }

        return Math.min(maximum, Math.max(minimum, number));
    }

    function safeDispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, { detail }));
        } catch (error) {
            /* Visualization events must not interrupt rendering. */
        }
    }

    function createElement(tagName, className, text) {
        const element = document.createElement(tagName);

        if (className) {
            element.className = className;
        }

        if (text !== undefined) {
            element.textContent = text;
        }

        return element;
    }

    function resolveCanvas(target) {
        if (target instanceof HTMLCanvasElement) {
            return target;
        }

        if (target instanceof Element) {
            const canvas =
                target.querySelector("canvas") ||
                document.createElement("canvas");

            if (!canvas.isConnected) {
                target.appendChild(canvas);
            }

            return canvas;
        }

        throw new TypeError(
            "Network visualization requires a canvas or container element."
        );
    }

    function createResizeObserver(element, callback) {
        if (typeof ResizeObserver === "function") {
            const observer = new ResizeObserver(callback);
            observer.observe(element);
            return () => observer.disconnect();
        }

        window.addEventListener("resize", callback);
        return () => window.removeEventListener("resize", callback);
    }

    function normalizeRecords(data) {
        if (data === null || data === undefined) {
            return [];
        }

        if (Array.isArray(data)) {
            return data;
        }

        if (isObject(data)) {
            for (const key of ["records", "results", "items", "nodes", "data"]) {
                if (Array.isArray(data[key])) {
                    return data[key];
                }
            }

            return [data];
        }

        return [data];
    }

    function seededRandom(seed) {
        let value = 2166136261;

        for (const character of String(seed || "speciedex-network")) {
            value ^= character.charCodeAt(0);
            value = Math.imul(value, 16777619);
        }

        return function random() {
            value += 0x6D2B79F5;
            let result = value;
            result = Math.imul(result ^ (result >>> 15), result | 1);
            result ^= result + Math.imul(result ^ (result >>> 7), result | 61);
            return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
        };
    }

    function firstValue(record, keys, fallback = null) {
        for (const key of keys) {
            const value = record?.[key];

            if (value !== undefined && value !== null && value !== "") {
                return value;
            }
        }

        return fallback;
    }

    function nodeId(record, index) {
        if (!isObject(record)) {
            return `node-${index + 1}`;
        }

        return String(firstValue(record, [
            "speciedex_id",
            "speciedexId",
            "canonical_id",
            "canonicalId",
            "taxon_id",
            "taxonId",
            "node_id",
            "nodeId",
            "id",
            "key",
            "uuid"
        ], `node-${index + 1}`));
    }

    function nodeLabel(record, index) {
        if (!isObject(record)) {
            return String(record ?? `Node ${index + 1}`);
        }

        return String(firstValue(record, [
            "scientific_name",
            "scientificName",
            "canonical_name",
            "canonicalName",
            "common_name",
            "commonName",
            "hostname",
            "provider",
            "name",
            "label",
            "id"
        ], `Node ${index + 1}`));
    }

    function nodeGroup(record, groupKey) {
        if (!isObject(record)) {
            return "ungrouped";
        }

        if (groupKey && record[groupKey] !== undefined) {
            return String(record[groupKey]);
        }

        return String(firstValue(record, [
            "network",
            "cluster",
            "provider",
            "source",
            "kingdom",
            "phylum",
            "family",
            "rank",
            "status"
        ], "ungrouped"));
    }

    function nodeWeight(record) {
        if (!isObject(record)) {
            return 1;
        }

        for (const key of [
            "weight",
            "value",
            "count",
            "traffic",
            "bandwidth",
            "throughput",
            "score",
            "occurrenceCount",
            "occurrence_count"
        ]) {
            const value = Number(record[key]);

            if (Number.isFinite(value)) {
                return Math.max(0.01, value);
            }
        }

        return 1;
    }

    function nodeStatus(record) {
        if (!isObject(record)) {
            return "unknown";
        }

        return String(firstValue(record, [
            "status",
            "state",
            "health",
            "availability",
            "online"
        ], "unknown"));
    }

    function colorHash(value) {
        let hash = 0;

        for (const character of String(value || "")) {
            hash = ((hash << 5) - hash) + character.charCodeAt(0);
            hash |= 0;
        }

        return `hsl(${Math.abs(hash) % 360} 55% 60%)`;
    }

    function extractExplicitEdges(data) {
        if (!isObject(data)) {
            return [];
        }

        for (const key of ["edges", "links", "connections", "relationships"]) {
            if (Array.isArray(data[key])) {
                return data[key];
            }
        }

        return [];
    }

    function extractReferences(record, options = {}) {
        if (!isObject(record)) {
            return [];
        }

        const keys = Array.isArray(options.edgeKeys) && options.edgeKeys.length
            ? options.edgeKeys
            : [
                "parent_id",
                "parentId",
                "accepted_id",
                "acceptedId",
                "peer_ids",
                "peerIds",
                "related_ids",
                "relatedIds",
                "connections",
                "links",
                "edges",
                "relationships"
            ];
        const references = [];

        const append = (item, type) => {
            if (isObject(item)) {
                const target = firstValue(item, [
                    "target",
                    "targetId",
                    "to",
                    "id",
                    "key",
                    "taxonId",
                    "nodeId"
                ], "");

                if (target !== "") {
                    references.push({
                        target: String(target),
                        type: String(firstValue(item, [
                            "type",
                            "relationship",
                            "kind",
                            "protocol"
                        ], type)),
                        weight: parseNumber(
                            item.weight ?? item.value ?? item.traffic,
                            1,
                            0.01,
                            1000000
                        ),
                        directed: item.directed !== false
                    });
                }
            } else if (item !== "") {
                references.push({
                    target: String(item),
                    type,
                    weight: 1,
                    directed: true
                });
            }
        };

        for (const key of keys) {
            const value = record[key];

            if (value === undefined || value === null) {
                continue;
            }

            if (Array.isArray(value)) {
                value.forEach((item) => append(item, key));
            } else {
                append(value, key);
            }
        }

        return references;
    }

    function normalizeNetwork(data, options = {}) {
        const maxNodes = parseNumber(
            options.maxNodes,
            DEFAULT_MAX_NODES,
            1,
            100000
        );
        const maxEdges = parseNumber(
            options.maxEdges,
            DEFAULT_MAX_EDGES,
            0,
            1000000
        );
        const records = normalizeRecords(data).slice(0, maxNodes);
        const random = seededRandom(options.seed || "speciedex-network");
        const nodes = [];
        const byId = new Map();

        records.forEach((record, index) => {
            const id = nodeId(record, index);

            if (byId.has(id)) {
                return;
            }

            const node = {
                id,
                label: nodeLabel(record, index),
                group: nodeGroup(record, options.groupKey),
                status: nodeStatus(record),
                weight: nodeWeight(record),
                degree: 0,
                radius: 0,
                x: random(),
                y: random(),
                vx: 0,
                vy: 0,
                fx: null,
                fy: null,
                visible: true,
                raw: clone(record)
            };

            nodes.push(node);
            byId.set(id, node);
        });

        const edges = [];
        const seen = new Set();

        const addEdge = (
            source,
            target,
            type = "connected",
            weight = 1,
            directed = true
        ) => {
            if (edges.length >= maxEdges) {
                return false;
            }

            source = String(source);
            target = String(target);

            if (
                source === target ||
                !byId.has(source) ||
                !byId.has(target)
            ) {
                return false;
            }

            const key = `${source}|${target}|${type}`;

            if (seen.has(key)) {
                return false;
            }

            seen.add(key);
            edges.push({
                id: key,
                source,
                target,
                type: String(type),
                weight: parseNumber(weight, 1, 0.01, 1000000),
                directed: directed !== false,
                visible: true
            });

            return true;
        };

        for (const edge of extractExplicitEdges(data)) {
            if (!isObject(edge)) {
                continue;
            }

            addEdge(
                firstValue(edge, ["source", "sourceId", "from"], ""),
                firstValue(edge, ["target", "targetId", "to"], ""),
                firstValue(edge, ["type", "relationship", "kind", "protocol"], "connected"),
                edge.weight ?? edge.value ?? edge.traffic ?? 1,
                edge.directed !== false
            );
        }

        for (const node of nodes) {
            for (const reference of extractReferences(node.raw, options)) {
                addEdge(
                    node.id,
                    reference.target,
                    reference.type,
                    reference.weight,
                    reference.directed
                );
            }
        }

        if (options.inferRelationships !== false && edges.length < maxEdges) {
            const buckets = new Map();

            for (const node of nodes) {
                if (!buckets.has(node.group)) {
                    buckets.set(node.group, []);
                }

                buckets.get(node.group).push(node);
            }

            for (const members of buckets.values()) {
                for (let index = 1; index < members.length; index += 1) {
                    addEdge(
                        members[index - 1].id,
                        members[index].id,
                        "cluster",
                        0.5,
                        false
                    );

                    if (edges.length >= maxEdges) {
                        break;
                    }
                }

                if (edges.length >= maxEdges) {
                    break;
                }
            }
        }

        for (const edge of edges) {
            byId.get(edge.source).degree += 1;
            byId.get(edge.target).degree += 1;
        }

        return {
            nodes,
            edges,
            byId
        };
    }

    class NetworkController extends EventTarget {
        constructor(target, data = [], options = {}) {
            super();

            this.canvas = resolveCanvas(target);
            this.context = this.canvas.getContext("2d", {
                alpha: true,
                desynchronized: true
            });

            if (!this.context) {
                throw new Error(
                    "Unable to acquire Network 2D canvas context."
                );
            }

            this.options = {
                background:
                    options.background ||
                    DEFAULT_BACKGROUND,
                foreground:
                    options.foreground ||
                    DEFAULT_FOREGROUND,
                highlight:
                    options.highlight ||
                    DEFAULT_HIGHLIGHT,
                edgeColor:
                    options.edgeColor ||
                    DEFAULT_EDGE,
                packetColor:
                    options.packetColor ||
                    DEFAULT_PACKET,
                nodeRadius: parseNumber(
                    options.nodeRadius,
                    DEFAULT_NODE_RADIUS,
                    1,
                    40
                ),
                charge: parseNumber(
                    options.charge,
                    DEFAULT_CHARGE,
                    0,
                    5000
                ),
                linkDistance: parseNumber(
                    options.linkDistance,
                    DEFAULT_LINK_DISTANCE,
                    5,
                    1000
                ),
                linkStrength: parseNumber(
                    options.linkStrength,
                    0.08,
                    0,
                    2
                ),
                centering: parseNumber(
                    options.centering,
                    0.013,
                    0,
                    1
                ),
                clustering: parseNumber(
                    options.clustering,
                    0.02,
                    0,
                    1
                ),
                damping: parseNumber(
                    options.damping,
                    DEFAULT_DAMPING,
                    0,
                    0.999
                ),
                alphaDecay: parseNumber(
                    options.alphaDecay,
                    DEFAULT_ALPHA_DECAY,
                    0.0001,
                    1
                ),
                minAlpha: parseNumber(
                    options.minAlpha,
                    DEFAULT_MIN_ALPHA,
                    0.00001,
                    1
                ),
                collisionPadding: parseNumber(
                    options.collisionPadding,
                    2,
                    0,
                    100
                ),
                maxNodes: parseNumber(
                    options.maxNodes,
                    DEFAULT_MAX_NODES,
                    1,
                    100000
                ),
                maxEdges: parseNumber(
                    options.maxEdges,
                    DEFAULT_MAX_EDGES,
                    0,
                    1000000
                ),
                groupKey:
                    options.groupKey || null,
                inferRelationships:
                    options.inferRelationships !== false,
                showLabels:
                    options.showLabels !== false,
                showEdges:
                    options.showEdges !== false,
                showGroups:
                    options.showGroups !== false,
                showArrows:
                    options.showArrows === true,
                showTraffic:
                    options.showTraffic !== false,
                trafficSpeed: parseNumber(
                    options.trafficSpeed,
                    0.00025,
                    0.00001,
                    0.02
                ),
                trafficDensity: parseNumber(
                    options.trafficDensity,
                    0.35,
                    0,
                    1
                ),
                animated:
                    options.animated !== false,
                interactive:
                    options.interactive !== false,
                zoomable:
                    options.zoomable !== false,
                pannable:
                    options.pannable !== false,
                seed:
                    options.seed ||
                    "speciedex-network",
                label:
                    options.label ||
                    "Network visualization"
            };

            this.network = {
                nodes: [],
                edges: [],
                byId: new Map()
            };
            this.bounds = {
                width: 1,
                height: 1
            };
            this.transform = {
                zoom: 1,
                x: 0,
                y: 0
            };
            this.alpha = 1;
            this.running = false;
            this.paused = false;
            this.destroyed = false;
            this.animationFrame = 0;
            this.lastFrameAt = 0;
            this.startedAt = null;
            this.hovered = null;
            this.selected = null;
            this.drag = null;
            this.query = "";
            this.groupFilter = null;
            this.statusFilter = null;
            this.trafficPhase = 0;
            this.lastError = null;
            this.metrics = {
                inputRecords: 0,
                nodes: 0,
                edges: 0,
                visibleNodes: 0,
                visibleEdges: 0,
                frames: 0,
                simulationSteps: 0,
                trafficPackets: 0,
                zooms: 0,
                pans: 0,
                selections: 0,
                resizes: 0,
                errors: 0
            };

            this._boundPointerMove =
                this._handlePointerMove.bind(this);
            this._boundPointerLeave =
                this._handlePointerLeave.bind(this);
            this._boundPointerDown =
                this._handlePointerDown.bind(this);
            this._boundPointerUp =
                this._handlePointerUp.bind(this);
            this._boundWheel =
                this._handleWheel.bind(this);
            this._boundClick =
                this._handleClick.bind(this);
            this._boundKeydown =
                this._handleKeydown.bind(this);

            this._cleanupResize = createResizeObserver(
                this.canvas,
                () => this.resize()
            );

            if (this.options.interactive) {
                this.canvas.tabIndex =
                    this.canvas.tabIndex >= 0
                        ? this.canvas.tabIndex
                        : 0;
                this.canvas.setAttribute(
                    "aria-label",
                    this.options.label
                );
                this.canvas.addEventListener(
                    "pointermove",
                    this._boundPointerMove
                );
                this.canvas.addEventListener(
                    "pointerleave",
                    this._boundPointerLeave
                );
                this.canvas.addEventListener(
                    "pointerdown",
                    this._boundPointerDown
                );
                this.canvas.addEventListener(
                    "pointerup",
                    this._boundPointerUp
                );
                this.canvas.addEventListener(
                    "wheel",
                    this._boundWheel,
                    { passive: false }
                );
                this.canvas.addEventListener(
                    "click",
                    this._boundClick
                );
                this.canvas.addEventListener(
                    "keydown",
                    this._boundKeydown
                );
            }

            this.resize();
            this.setData(data);

            if (this.options.animated) {
                this.start();
            } else {
                this.simulate(160);
                this.draw();
            }
        }

        _emit(type, detail = {}) {
            safeDispatch(this, type, {
                type,
                timestamp: iso(),
                ...detail
            });
        }

        _recordError(error) {
            this.lastError = error instanceof Error
                ? error
                : new Error(String(error));
            this.metrics.errors += 1;

            this._emit("error", {
                error: {
                    name: this.lastError.name,
                    message: this.lastError.message,
                    stack: this.lastError.stack || ""
                }
            });
        }

        resize() {
            if (this.destroyed) {
                return;
            }

            const rectangle =
                this.canvas.getBoundingClientRect();
            const ratio = Math.min(
                window.devicePixelRatio || 1,
                2
            );
            const width = Math.max(
                1,
                Math.floor(rectangle.width * ratio)
            );
            const height = Math.max(
                1,
                Math.floor(rectangle.height * ratio)
            );

            if (
                this.canvas.width !== width ||
                this.canvas.height !== height
            ) {
                this.canvas.width = width;
                this.canvas.height = height;
            }

            this.context.setTransform(
                ratio,
                0,
                0,
                ratio,
                0,
                0
            );

            this.bounds.width =
                rectangle.width || DEFAULT_WIDTH;
            this.bounds.height =
                rectangle.height || DEFAULT_HEIGHT;
            this.metrics.resizes += 1;
            this._scaleNormalizedPositions();
            this.alpha = Math.max(this.alpha, 0.25);
            this.draw();

            this._emit("resize", clone(this.bounds));
        }

        _scaleNormalizedPositions() {
            for (const node of this.network.nodes) {
                if (node.x >= 0 && node.x <= 1) {
                    node.x *= this.bounds.width;
                }

                if (node.y >= 0 && node.y <= 1) {
                    node.y *= this.bounds.height;
                }

                node.x = Math.max(
                    0,
                    Math.min(this.bounds.width, node.x)
                );
                node.y = Math.max(
                    0,
                    Math.min(this.bounds.height, node.y)
                );
            }
        }

        setData(data) {
            try {
                this.network = normalizeNetwork(
                    data,
                    this.options
                );
                this.metrics.inputRecords =
                    normalizeRecords(data).length;
                this.metrics.nodes =
                    this.network.nodes.length;
                this.metrics.edges =
                    this.network.edges.length;
                this.alpha = 1;
                this.hovered = null;
                this.selected = null;
                this._scaleNormalizedPositions();
                this._updateRadii();
                this._applyFilters();
                this.draw();

                this._emit("data", {
                    nodes: this.network.nodes.length,
                    edges: this.network.edges.length
                });
            } catch (error) {
                this._recordError(error);
            }

            return this;
        }

        append(data) {
            const combined = [
                ...this.network.nodes.map((node) => node.raw),
                ...normalizeRecords(data)
            ];

            this.setData(combined);

            this._emit("append", {
                added: normalizeRecords(data).length
            });

            return this;
        }

        _updateRadii() {
            const scores = this.network.nodes.map(
                (node) => node.weight + node.degree
            );
            const minimum = Math.min(...scores, 1);
            const maximum = Math.max(...scores, 1);
            const range = Math.max(1e-9, maximum - minimum);

            for (const node of this.network.nodes) {
                const ratio =
                    (node.weight + node.degree - minimum) /
                    range;

                node.radius =
                    this.options.nodeRadius *
                    (0.72 + Math.sqrt(ratio) * 1.9);
            }
        }

        _applyFilters() {
            const query = this.query.toLowerCase();

            for (const node of this.network.nodes) {
                node.visible =
                    (
                        !query ||
                        node.id.toLowerCase().includes(query) ||
                        node.label.toLowerCase().includes(query) ||
                        node.group.toLowerCase().includes(query) ||
                        node.status.toLowerCase().includes(query)
                    ) &&
                    (
                        !this.groupFilter ||
                        node.group === this.groupFilter
                    ) &&
                    (
                        !this.statusFilter ||
                        node.status === this.statusFilter
                    );
            }

            for (const edge of this.network.edges) {
                edge.visible = Boolean(
                    this.network.byId.get(edge.source)?.visible &&
                    this.network.byId.get(edge.target)?.visible
                );
            }

            this.metrics.visibleNodes =
                this.network.nodes.filter(
                    (node) => node.visible
                ).length;
            this.metrics.visibleEdges =
                this.network.edges.filter(
                    (edge) => edge.visible
                ).length;
        }

        start() {
            if (this.destroyed) {
                throw new Error(
                    "Network controller has been destroyed."
                );
            }

            if (this.running && !this.paused) {
                return this;
            }

            this.running = true;
            this.paused = false;
            this.startedAt =
                this.startedAt || iso();
            this.lastFrameAt = 0;
            this.animationFrame =
                window.requestAnimationFrame(
                    (timestamp) =>
                        this._frame(timestamp)
                );

            this._emit("start", {});
            return this;
        }

        stop() {
            const active =
                this.running ||
                this.paused;

            this.running = false;
            this.paused = false;

            if (this.animationFrame) {
                window.cancelAnimationFrame(
                    this.animationFrame
                );
                this.animationFrame = 0;
            }

            if (active) {
                this._emit("stop", {});
            }

            return this;
        }

        pause() {
            if (!this.running || this.paused) {
                return false;
            }

            this.paused = true;

            if (this.animationFrame) {
                window.cancelAnimationFrame(
                    this.animationFrame
                );
                this.animationFrame = 0;
            }

            this._emit("pause", {});
            return true;
        }

        resume() {
            if (!this.running) {
                this.start();
                return true;
            }

            if (!this.paused) {
                return false;
            }

            this.paused = false;
            this.lastFrameAt = 0;
            this.animationFrame =
                window.requestAnimationFrame(
                    (timestamp) =>
                        this._frame(timestamp)
                );
            this._emit("resume", {});
            return true;
        }

        _frame(timestamp) {
            if (
                !this.running ||
                this.paused ||
                this.destroyed
            ) {
                return;
            }

            const delta = this.lastFrameAt
                ? Math.min(
                    50,
                    timestamp - this.lastFrameAt
                ) / 1000
                : 0.016667;
            this.lastFrameAt = timestamp;

            if (this.alpha > this.options.minAlpha) {
                this._simulateStep(delta);
            }

            this.trafficPhase +=
                delta *
                this.options.trafficSpeed *
                1000;

            this.draw();
            this.metrics.frames += 1;

            this.animationFrame =
                window.requestAnimationFrame(
                    (nextTimestamp) =>
                        this._frame(nextTimestamp)
                );
        }

        simulate(iterations = 1) {
            const count = parseNumber(
                iterations,
                1,
                1,
                100000
            );

            for (let index = 0; index < count; index += 1) {
                this._simulateStep(0.016667);
            }

            this.draw();
            return this;
        }

        _simulateStep(delta) {
            const nodes =
                this.network.nodes.filter(
                    (node) => node.visible
                );
            const edges =
                this.network.edges.filter(
                    (edge) => edge.visible
                );

            if (!nodes.length) {
                return;
            }

            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;

            for (
                let leftIndex = 0;
                leftIndex < nodes.length;
                leftIndex += 1
            ) {
                const left = nodes[leftIndex];

                for (
                    let rightIndex = leftIndex + 1;
                    rightIndex < nodes.length;
                    rightIndex += 1
                ) {
                    const right = nodes[rightIndex];
                    let dx = right.x - left.x;
                    let dy = right.y - left.y;
                    let distanceSquared =
                        dx * dx + dy * dy;

                    if (distanceSquared < 0.01) {
                        dx = 0.1;
                        dy = 0.1;
                        distanceSquared = 0.02;
                    }

                    const distance =
                        Math.sqrt(distanceSquared);
                    const force =
                        this.options.charge *
                        this.alpha /
                        distanceSquared;
                    const fx =
                        force * dx / distance;
                    const fy =
                        force * dy / distance;

                    if (left.fx === null) {
                        left.vx -= fx;
                        left.vy -= fy;
                    }

                    if (right.fx === null) {
                        right.vx += fx;
                        right.vy += fy;
                    }

                    const minimumDistance =
                        left.radius +
                        right.radius +
                        this.options.collisionPadding;

                    if (distance < minimumDistance) {
                        const overlap =
                            minimumDistance -
                            distance;
                        const correction =
                            overlap * 0.16;

                        if (left.fx === null) {
                            left.vx -=
                                correction *
                                dx /
                                distance;
                            left.vy -=
                                correction *
                                dy /
                                distance;
                        }

                        if (right.fx === null) {
                            right.vx +=
                                correction *
                                dx /
                                distance;
                            right.vy +=
                                correction *
                                dy /
                                distance;
                        }
                    }
                }
            }

            for (const edge of edges) {
                const source =
                    this.network.byId.get(
                        edge.source
                    );
                const target =
                    this.network.byId.get(
                        edge.target
                    );

                if (!source || !target) {
                    continue;
                }

                let dx =
                    target.x - source.x;
                let dy =
                    target.y - source.y;
                let distance =
                    Math.sqrt(
                        dx * dx + dy * dy
                    );

                if (distance < 0.01) {
                    distance = 0.01;
                }

                const desired =
                    this.options.linkDistance /
                    Math.max(
                        0.45,
                        Math.sqrt(edge.weight)
                    );
                const force =
                    (
                        distance - desired
                    ) *
                    this.options.linkStrength *
                    this.alpha;
                const fx =
                    force * dx / distance;
                const fy =
                    force * dy / distance;

                if (source.fx === null) {
                    source.vx += fx;
                    source.vy += fy;
                }

                if (target.fx === null) {
                    target.vx -= fx;
                    target.vy -= fy;
                }
            }

            const groups = Array.from(
                new Set(
                    nodes.map(
                        (node) => node.group
                    )
                )
            );
            const groupCenters = new Map();

            groups.forEach((group, index) => {
                const angle =
                    index /
                    Math.max(
                        1,
                        groups.length
                    ) *
                    Math.PI *
                    2;
                const radius =
                    Math.min(
                        this.bounds.width,
                        this.bounds.height
                    ) *
                    0.24;

                groupCenters.set(group, {
                    x:
                        centerX +
                        Math.cos(angle) *
                        radius,
                    y:
                        centerY +
                        Math.sin(angle) *
                        radius
                });
            });

            for (const node of nodes) {
                const cluster =
                    groupCenters.get(
                        node.group
                    ) || {
                        x: centerX,
                        y: centerY
                    };

                if (node.fx === null) {
                    node.vx +=
                        (
                            centerX - node.x
                        ) *
                        this.options.centering *
                        this.alpha;
                    node.vy +=
                        (
                            centerY - node.y
                        ) *
                        this.options.centering *
                        this.alpha;
                    node.vx +=
                        (
                            cluster.x - node.x
                        ) *
                        this.options.clustering *
                        this.alpha;
                    node.vy +=
                        (
                            cluster.y - node.y
                        ) *
                        this.options.clustering *
                        this.alpha;

                    node.vx *=
                        this.options.damping;
                    node.vy *=
                        this.options.damping;
                    node.x +=
                        node.vx *
                        delta *
                        60;
                    node.y +=
                        node.vy *
                        delta *
                        60;
                } else {
                    node.x = node.fx;
                    node.y = node.fy;
                    node.vx = 0;
                    node.vy = 0;
                }

                const margin =
                    node.radius + 4;
                node.x = Math.max(
                    margin,
                    Math.min(
                        this.bounds.width -
                        margin,
                        node.x
                    )
                );
                node.y = Math.max(
                    margin,
                    Math.min(
                        this.bounds.height -
                        margin,
                        node.y
                    )
                );
            }

            this.alpha *=
                1 -
                this.options.alphaDecay;
            this.metrics.simulationSteps += 1;
        }

        _screenPoint(x, y) {
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;

            return {
                x:
                    centerX +
                    (
                        x - centerX
                    ) *
                    this.transform.zoom +
                    this.transform.x,
                y:
                    centerY +
                    (
                        y - centerY
                    ) *
                    this.transform.zoom +
                    this.transform.y
            };
        }

        _inverseScreenPoint(x, y) {
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;

            return {
                x:
                    centerX +
                    (
                        x -
                        centerX -
                        this.transform.x
                    ) /
                    this.transform.zoom,
                y:
                    centerY +
                    (
                        y -
                        centerY -
                        this.transform.y
                    ) /
                    this.transform.zoom
            };
        }

        draw() {
            if (this.destroyed) {
                return;
            }

            this.context.clearRect(
                0,
                0,
                this.bounds.width,
                this.bounds.height
            );
            this.context.fillStyle =
                this.options.background;
            this.context.fillRect(
                0,
                0,
                this.bounds.width,
                this.bounds.height
            );

            if (!this.network.nodes.length) {
                this.context.fillStyle =
                    this.options.foreground;
                this.context.globalAlpha =
                    0.72;
                this.context.font =
                    '14px "IBM Plex Mono", monospace';
                this.context.textAlign =
                    "center";
                this.context.textBaseline =
                    "middle";
                this.context.fillText(
                    "No network nodes.",
                    this.bounds.width / 2,
                    this.bounds.height / 2
                );
                this.context.globalAlpha = 1;
                return;
            }

            if (this.options.showEdges) {
                this._drawEdges();
            }

            if (this.options.showTraffic) {
                this._drawTraffic();
            }

            this._drawNodes();

            if (this.options.showLabels) {
                this._drawLabels();
            }
        }

        _drawEdges() {
            this.context.save();

            for (const edge of this.network.edges) {
                if (!edge.visible) {
                    continue;
                }

                const source =
                    this.network.byId.get(
                        edge.source
                    );
                const target =
                    this.network.byId.get(
                        edge.target
                    );

                if (!source || !target) {
                    continue;
                }

                const start =
                    this._screenPoint(
                        source.x,
                        source.y
                    );
                const end =
                    this._screenPoint(
                        target.x,
                        target.y
                    );
                const emphasized =
                    this.selected &&
                    (
                        source.id ===
                        this.selected.id ||
                        target.id ===
                        this.selected.id
                    );

                this.context.strokeStyle =
                    emphasized
                        ? this.options.highlight
                        : this.options.edgeColor;
                this.context.globalAlpha =
                    emphasized
                        ? 0.9
                        : 0.30;
                this.context.lineWidth =
                    emphasized
                        ? 2
                        : Math.min(
                            3,
                            0.6 +
                            Math.sqrt(
                                edge.weight
                            ) *
                            0.34
                        );
                this.context.beginPath();
                this.context.moveTo(
                    start.x,
                    start.y
                );
                this.context.lineTo(
                    end.x,
                    end.y
                );
                this.context.stroke();

                if (
                    this.options.showArrows &&
                    edge.directed
                ) {
                    this._drawArrowHead(
                        start,
                        end,
                        target.radius *
                        Math.sqrt(
                            this.transform.zoom
                        )
                    );
                }
            }

            this.context.restore();
        }

        _drawArrowHead(start, end, targetRadius) {
            const angle =
                Math.atan2(
                    end.y - start.y,
                    end.x - start.x
                );
            const size = 6;
            const x =
                end.x -
                Math.cos(angle) *
                (
                    targetRadius + 2
                );
            const y =
                end.y -
                Math.sin(angle) *
                (
                    targetRadius + 2
                );

            this.context.beginPath();
            this.context.moveTo(x, y);
            this.context.lineTo(
                x -
                Math.cos(
                    angle -
                    Math.PI / 6
                ) *
                size,
                y -
                Math.sin(
                    angle -
                    Math.PI / 6
                ) *
                size
            );
            this.context.lineTo(
                x -
                Math.cos(
                    angle +
                    Math.PI / 6
                ) *
                size,
                y -
                Math.sin(
                    angle +
                    Math.PI / 6
                ) *
                size
            );
            this.context.closePath();
            this.context.fillStyle =
                this.context.strokeStyle;
            this.context.fill();
        }

        _drawTraffic() {
            this.context.save();
            this.context.fillStyle =
                this.options.packetColor;
            this.context.shadowColor =
                this.options.packetColor;
            this.context.shadowBlur = 8;
            this.context.globalAlpha =
                0.88;

            let packets = 0;

            for (const edge of this.network.edges) {
                if (
                    !edge.visible ||
                    Math.random() >
                    this.options.trafficDensity
                ) {
                    continue;
                }

                const source =
                    this.network.byId.get(
                        edge.source
                    );
                const target =
                    this.network.byId.get(
                        edge.target
                    );

                if (!source || !target) {
                    continue;
                }

                const start =
                    this._screenPoint(
                        source.x,
                        source.y
                    );
                const end =
                    this._screenPoint(
                        target.x,
                        target.y
                    );
                const phase =
                    (
                        this.trafficPhase +
                        (
                            edge.id.length %
                            97
                        ) /
                        97
                    ) %
                    1;
                const x =
                    start.x +
                    (
                        end.x -
                        start.x
                    ) *
                    phase;
                const y =
                    start.y +
                    (
                        end.y -
                        start.y
                    ) *
                    phase;

                this.context.beginPath();
                this.context.arc(
                    x,
                    y,
                    Math.min(
                        4,
                        1.5 +
                        Math.sqrt(
                            edge.weight
                        ) *
                        0.2
                    ),
                    0,
                    Math.PI * 2
                );
                this.context.fill();
                packets += 1;
            }

            this.metrics.trafficPackets =
                packets;
            this.context.restore();
        }

        _drawNodes() {
            this.context.save();

            for (const node of this.network.nodes) {
                if (!node.visible) {
                    continue;
                }

                const point =
                    this._screenPoint(
                        node.x,
                        node.y
                    );
                const radius =
                    node.radius *
                    Math.sqrt(
                        this.transform.zoom
                    );
                const emphasized =
                    node.id ===
                    this.selected?.id ||
                    node.id ===
                    this.hovered?.id;

                this.context.beginPath();
                this.context.arc(
                    point.x,
                    point.y,
                    radius,
                    0,
                    Math.PI * 2
                );
                this.context.fillStyle =
                    emphasized
                        ? this.options.highlight
                        : this.options.showGroups
                            ? colorHash(
                                node.group
                            )
                            : this.options.foreground;
                this.context.globalAlpha =
                    emphasized
                        ? 1
                        : 0.84;

                if (emphasized) {
                    this.context.shadowColor =
                        this.options.highlight;
                    this.context.shadowBlur =
                        12;
                } else {
                    this.context.shadowBlur =
                        0;
                }

                this.context.fill();

                this.context.strokeStyle =
                    this.options.background;
                this.context.globalAlpha =
                    0.8;
                this.context.lineWidth = 1;
                this.context.stroke();

                node.screenX = point.x;
                node.screenY = point.y;
                node.screenRadius = radius;
            }

            this.context.restore();
        }

        _drawLabels() {
            const visible =
                this.network.nodes
                    .filter(
                        (node) =>
                            node.visible
                    )
                    .sort(
                        (left, right) =>
                            (
                                right.degree +
                                right.weight
                            ) -
                            (
                                left.degree +
                                left.weight
                            )
                    )
                    .slice(
                        0,
                        MAX_LABELS
                    );

            this.context.save();
            this.context.font =
                '11px "IBM Plex Mono", monospace';
            this.context.textAlign =
                "left";
            this.context.textBaseline =
                "middle";

            for (const node of visible) {
                if (
                    this.transform.zoom <
                        0.75 &&
                    node.id !==
                        this.selected?.id &&
                    node.id !==
                        this.hovered?.id
                ) {
                    continue;
                }

                const point =
                    this._screenPoint(
                        node.x,
                        node.y
                    );
                const emphasized =
                    node.id ===
                    this.selected?.id ||
                    node.id ===
                    this.hovered?.id;

                this.context.fillStyle =
                    emphasized
                        ? this.options.highlight
                        : this.options.foreground;
                this.context.globalAlpha =
                    emphasized
                        ? 1
                        : 0.68;
                this.context.fillText(
                    node.label,
                    point.x +
                    node.radius *
                    Math.sqrt(
                        this.transform.zoom
                    ) +
                    4,
                    point.y
                );
            }

            this.context.restore();
        }

        _pointFromEvent(event) {
            const rectangle =
                this.canvas.getBoundingClientRect();

            return {
                x:
                    event.clientX -
                    rectangle.left,
                y:
                    event.clientY -
                    rectangle.top
            };
        }

        hitTest(x, y) {
            for (
                let index =
                    this.network.nodes.length -
                    1;
                index >= 0;
                index -= 1
            ) {
                const node =
                    this.network.nodes[index];

                if (!node.visible) {
                    continue;
                }

                const point =
                    this._screenPoint(
                        node.x,
                        node.y
                    );
                const radius =
                    node.radius *
                    Math.sqrt(
                        this.transform.zoom
                    ) +
                    4;
                const dx =
                    x - point.x;
                const dy =
                    y - point.y;

                if (
                    dx * dx +
                    dy * dy <=
                    radius * radius
                ) {
                    return node;
                }
            }

            return null;
        }

        _handlePointerMove(event) {
            const point =
                this._pointFromEvent(event);

            if (this.drag?.node) {
                const world =
                    this._inverseScreenPoint(
                        point.x,
                        point.y
                    );
                this.drag.node.fx =
                    world.x;
                this.drag.node.fy =
                    world.y;
                this.drag.node.x =
                    world.x;
                this.drag.node.y =
                    world.y;
                this.alpha =
                    Math.max(
                        this.alpha,
                        0.12
                    );
                this.draw();
                return;
            }

            if (this.drag?.pan) {
                this.transform.x =
                    this.drag.originX +
                    point.x -
                    this.drag.startX;
                this.transform.y =
                    this.drag.originY +
                    point.y -
                    this.drag.startY;
                this.metrics.pans += 1;
                this.draw();
                return;
            }

            const hovered =
                this.hitTest(
                    point.x,
                    point.y
                );
            const changed =
                hovered?.id !==
                this.hovered?.id;

            this.hovered =
                hovered;
            this.canvas.style.cursor =
                hovered
                    ? "pointer"
                    : this.options.pannable
                        ? "grab"
                        : "default";

            if (changed) {
                this.draw();

                this._emit("hover", {
                    node:
                        hovered
                            ? this.describeNode(
                                hovered
                            )
                            : null
                });
            }
        }

        _handlePointerLeave() {
            this.drag = null;

            if (this.hovered) {
                this.hovered = null;
                this.draw();
                this._emit("hover", {
                    node: null
                });
            }
        }

        _handlePointerDown(event) {
            if (event.button !== 0) {
                return;
            }

            const point =
                this._pointFromEvent(event);
            const node =
                this.hitTest(
                    point.x,
                    point.y
                );

            if (node) {
                const world =
                    this._inverseScreenPoint(
                        point.x,
                        point.y
                    );
                node.fx = world.x;
                node.fy = world.y;
                this.drag = { node };
            } else if (this.options.pannable) {
                this.drag = {
                    pan: true,
                    startX: point.x,
                    startY: point.y,
                    originX:
                        this.transform.x,
                    originY:
                        this.transform.y
                };
            }

            this.canvas.setPointerCapture?.(
                event.pointerId
            );
        }

        _handlePointerUp(event) {
            if (!this.drag) {
                return;
            }

            if (this.drag.node) {
                this.drag.node.fx = null;
                this.drag.node.fy = null;
                this.alpha =
                    Math.max(
                        this.alpha,
                        0.18
                    );
            }

            this.canvas.releasePointerCapture?.(
                event.pointerId
            );
            this.drag = null;
        }

        _handleWheel(event) {
            if (!this.options.zoomable) {
                return;
            }

            event.preventDefault();

            const point =
                this._pointFromEvent(event);
            const before =
                this._inverseScreenPoint(
                    point.x,
                    point.y
                );
            const factor =
                event.deltaY < 0
                    ? 1.12
                    : 1 / 1.12;
            const zoom = Math.max(
                0.2,
                Math.min(
                    12,
                    this.transform.zoom *
                    factor
                )
            );
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;

            this.transform.zoom = zoom;
            this.transform.x =
                point.x -
                centerX -
                (
                    before.x -
                    centerX
                ) *
                zoom;
            this.transform.y =
                point.y -
                centerY -
                (
                    before.y -
                    centerY
                ) *
                zoom;
            this.metrics.zooms += 1;
            this.draw();

            this._emit("zoom", {
                zoom,
                transform:
                    clone(this.transform)
            });
        }

        _handleClick(event) {
            if (this.drag) {
                return;
            }

            const point =
                this._pointFromEvent(event);
            const node =
                this.hitTest(
                    point.x,
                    point.y
                );

            this.selected =
                node?.id ===
                this.selected?.id
                    ? null
                    : node;
            this.metrics.selections += 1;
            this.draw();

            this._emit("select", {
                node:
                    this.selected
                        ? this.describeNode(
                            this.selected
                        )
                        : null
            });
        }

        _handleKeydown(event) {
            if (
                event.key === "+" ||
                event.key === "="
            ) {
                event.preventDefault();
                this.setZoom(
                    this.transform.zoom *
                    1.2
                );
            } else if (event.key === "-") {
                event.preventDefault();
                this.setZoom(
                    this.transform.zoom /
                    1.2
                );
            } else if (event.key === "0") {
                event.preventDefault();
                this.resetView();
            } else if (
                event.key === "Escape"
            ) {
                this.selected = null;
                this.draw();
            } else if (
                event.key === "ArrowLeft"
            ) {
                event.preventDefault();
                this.panBy(24, 0);
            } else if (
                event.key === "ArrowRight"
            ) {
                event.preventDefault();
                this.panBy(-24, 0);
            } else if (
                event.key === "ArrowUp"
            ) {
                event.preventDefault();
                this.panBy(0, 24);
            } else if (
                event.key === "ArrowDown"
            ) {
                event.preventDefault();
                this.panBy(0, -24);
            }
        }

        setZoom(value) {
            this.transform.zoom =
                Math.max(
                    0.2,
                    Math.min(
                        12,
                        parseNumber(
                            value,
                            this.transform.zoom
                        )
                    )
                );
            this.draw();
            return this.transform.zoom;
        }

        panBy(x, y) {
            this.transform.x +=
                Number(x) || 0;
            this.transform.y +=
                Number(y) || 0;
            this.metrics.pans += 1;
            this.draw();
            return clone(
                this.transform
            );
        }

        resetView() {
            this.transform = {
                zoom: 1,
                x: 0,
                y: 0
            };
            this.selected = null;
            this.draw();
            return clone(
                this.transform
            );
        }

        setFilter(query = "") {
            this.query =
                String(query || "");
            this._applyFilters();
            this.alpha =
                Math.max(
                    this.alpha,
                    0.2
                );
            this.draw();

            this._emit("filter", {
                query:
                    this.query,
                visibleNodes:
                    this.metrics.visibleNodes,
                visibleEdges:
                    this.metrics.visibleEdges
            });

            return this.query;
        }

        setGroup(group = null) {
            this.groupFilter =
                group
                    ? String(group)
                    : null;
            this._applyFilters();
            this.alpha =
                Math.max(
                    this.alpha,
                    0.2
                );
            this.draw();
            return this.groupFilter;
        }

        setStatus(status = null) {
            this.statusFilter =
                status
                    ? String(status)
                    : null;
            this._applyFilters();
            this.alpha =
                Math.max(
                    this.alpha,
                    0.2
                );
            this.draw();
            return this.statusFilter;
        }

        selectNode(id) {
            const node =
                this.network.byId.get(
                    String(id)
                );

            if (!node) {
                return null;
            }

            this.selected = node;
            this.draw();
            return this.describeNode(node);
        }

        describeNode(node) {
            if (!node) {
                return null;
            }

            const edges =
                this.network.edges
                    .filter(
                        (edge) =>
                            edge.source ===
                                node.id ||
                            edge.target ===
                                node.id
                    )
                    .map((edge) => ({
                        ...clone(edge),
                        sourceLabel:
                            this.network.byId.get(
                                edge.source
                            )?.label ||
                            edge.source,
                        targetLabel:
                            this.network.byId.get(
                                edge.target
                            )?.label ||
                            edge.target
                    }));

            return {
                id: node.id,
                label: node.label,
                group: node.group,
                status: node.status,
                weight: node.weight,
                degree: node.degree,
                x: node.x,
                y: node.y,
                visible: node.visible,
                edges,
                raw: clone(node.raw)
            };
        }

        update(options = {}) {
            if (!isObject(options)) {
                throw new TypeError(
                    "Network options must be an object."
                );
            }

            const rebuildRequired = [
                "maxNodes",
                "maxEdges",
                "groupKey",
                "inferRelationships",
                "edgeKeys",
                "seed"
            ].some(
                (key) =>
                    options[key] !== undefined
            );

            Object.assign(
                this.options,
                {
                    background:
                        options.background ||
                        this.options.background,
                    foreground:
                        options.foreground ||
                        this.options.foreground,
                    highlight:
                        options.highlight ||
                        this.options.highlight,
                    edgeColor:
                        options.edgeColor ||
                        this.options.edgeColor,
                    packetColor:
                        options.packetColor ||
                        this.options.packetColor,
                    nodeRadius:
                        options.nodeRadius !== undefined
                            ? parseNumber(
                                options.nodeRadius,
                                this.options.nodeRadius,
                                1,
                                40
                            )
                            : this.options.nodeRadius,
                    charge:
                        options.charge !== undefined
                            ? parseNumber(
                                options.charge,
                                this.options.charge,
                                0,
                                5000
                            )
                            : this.options.charge,
                    linkDistance:
                        options.linkDistance !== undefined
                            ? parseNumber(
                                options.linkDistance,
                                this.options.linkDistance,
                                5,
                                1000
                            )
                            : this.options.linkDistance,
                    linkStrength:
                        options.linkStrength !== undefined
                            ? parseNumber(
                                options.linkStrength,
                                this.options.linkStrength,
                                0,
                                2
                            )
                            : this.options.linkStrength,
                    centering:
                        options.centering !== undefined
                            ? parseNumber(
                                options.centering,
                                this.options.centering,
                                0,
                                1
                            )
                            : this.options.centering,
                    clustering:
                        options.clustering !== undefined
                            ? parseNumber(
                                options.clustering,
                                this.options.clustering,
                                0,
                                1
                            )
                            : this.options.clustering,
                    damping:
                        options.damping !== undefined
                            ? parseNumber(
                                options.damping,
                                this.options.damping,
                                0,
                                0.999
                            )
                            : this.options.damping,
                    showLabels:
                        options.showLabels !== undefined
                            ? Boolean(
                                options.showLabels
                            )
                            : this.options.showLabels,
                    showEdges:
                        options.showEdges !== undefined
                            ? Boolean(
                                options.showEdges
                            )
                            : this.options.showEdges,
                    showGroups:
                        options.showGroups !== undefined
                            ? Boolean(
                                options.showGroups
                            )
                            : this.options.showGroups,
                    showArrows:
                        options.showArrows !== undefined
                            ? Boolean(
                                options.showArrows
                            )
                            : this.options.showArrows,
                    showTraffic:
                        options.showTraffic !== undefined
                            ? Boolean(
                                options.showTraffic
                            )
                            : this.options.showTraffic,
                    trafficSpeed:
                        options.trafficSpeed !== undefined
                            ? parseNumber(
                                options.trafficSpeed,
                                this.options.trafficSpeed,
                                0.00001,
                                0.02
                            )
                            : this.options.trafficSpeed,
                    trafficDensity:
                        options.trafficDensity !== undefined
                            ? parseNumber(
                                options.trafficDensity,
                                this.options.trafficDensity,
                                0,
                                1
                            )
                            : this.options.trafficDensity,
                    groupKey:
                        options.groupKey !== undefined
                            ? options.groupKey
                            : this.options.groupKey,
                    inferRelationships:
                        options.inferRelationships !== undefined
                            ? Boolean(
                                options.inferRelationships
                            )
                            : this.options.inferRelationships,
                    maxNodes:
                        options.maxNodes !== undefined
                            ? parseNumber(
                                options.maxNodes,
                                this.options.maxNodes,
                                1,
                                100000
                            )
                            : this.options.maxNodes,
                    maxEdges:
                        options.maxEdges !== undefined
                            ? parseNumber(
                                options.maxEdges,
                                this.options.maxEdges,
                                0,
                                1000000
                            )
                            : this.options.maxEdges,
                    seed:
                        options.seed ||
                        this.options.seed
                }
            );

            if (rebuildRequired) {
                this.setData(
                    this.network.nodes.map(
                        (node) => node.raw
                    )
                );
            } else {
                this._updateRadii();
                this.alpha =
                    Math.max(
                        this.alpha,
                        0.2
                    );
                this.draw();
            }

            this._emit("update", {
                options:
                    clone(this.options)
            });

            return this;
        }

        export(format = "json") {
            const normalized =
                String(format).toLowerCase();

            if (normalized === "png") {
                return this.canvas.toDataURL(
                    "image/png"
                );
            }

            if (normalized === "json") {
                return JSON.stringify(
                    {
                        generatedAt:
                            iso(),
                        options:
                            this.options,
                        transform:
                            this.transform,
                        nodes:
                            this.network.nodes.map(
                                (node) =>
                                    this.describeNode(
                                        node
                                    )
                            ),
                        edges:
                            this.network.edges.map(
                                clone
                            )
                    },
                    null,
                    2
                );
            }

            if (normalized === "csv") {
                const rows = [[
                    "id",
                    "label",
                    "group",
                    "status",
                    "weight",
                    "degree",
                    "x",
                    "y",
                    "visible"
                ]];

                for (
                    const node
                    of this.network.nodes
                ) {
                    rows.push([
                        node.id,
                        node.label,
                        node.group,
                        node.status,
                        node.weight,
                        node.degree,
                        node.x,
                        node.y,
                        node.visible
                    ]);
                }

                return rows
                    .map((row) =>
                        row.map((value) => {
                            const text =
                                String(
                                    value ?? ""
                                );

                            return /[",\n\r]/.test(
                                text
                            )
                                ? `"${text.replace(
                                    /"/g,
                                    '""'
                                )}"`
                                : text;
                        }).join(",")
                    )
                    .join("\r\n");
            }

            throw new Error(
                `Unsupported Network export format: ${format}`
            );
        }

        status() {
            return {
                name: "network",
                module: MODULE_NAME,
                running:
                    this.running,
                paused:
                    this.paused,
                startedAt:
                    this.startedAt,
                nodes:
                    this.network.nodes.length,
                visibleNodes:
                    this.metrics.visibleNodes,
                edges:
                    this.network.edges.length,
                visibleEdges:
                    this.metrics.visibleEdges,
                trafficPackets:
                    this.metrics.trafficPackets,
                alpha:
                    this.alpha,
                query:
                    this.query,
                groupFilter:
                    this.groupFilter,
                statusFilter:
                    this.statusFilter,
                transform:
                    clone(this.transform),
                selected:
                    this.selected
                        ? this.describeNode(
                            this.selected
                        )
                        : null,
                hovered:
                    this.hovered
                        ? this.describeNode(
                            this.hovered
                        )
                        : null,
                options:
                    clone(this.options),
                metrics:
                    { ...this.metrics },
                lastError:
                    this.lastError
                        ? {
                            name:
                                this.lastError.name,
                            message:
                                this.lastError.message
                        }
                        : null,
                destroyed:
                    this.destroyed
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.stop();
            this._cleanupResize?.();

            if (this.options.interactive) {
                this.canvas.removeEventListener(
                    "pointermove",
                    this._boundPointerMove
                );
                this.canvas.removeEventListener(
                    "pointerleave",
                    this._boundPointerLeave
                );
                this.canvas.removeEventListener(
                    "pointerdown",
                    this._boundPointerDown
                );
                this.canvas.removeEventListener(
                    "pointerup",
                    this._boundPointerUp
                );
                this.canvas.removeEventListener(
                    "wheel",
                    this._boundWheel
                );
                this.canvas.removeEventListener(
                    "click",
                    this._boundClick
                );
                this.canvas.removeEventListener(
                    "keydown",
                    this._boundKeydown
                );
            }

            this.network = {
                nodes: [],
                edges: [],
                byId: new Map()
            };
            this.destroyed = true;
            this._emit("destroy", {});
            return true;
        }
    }

    function mount(target, data = [], options = {}) {
        return new NetworkController(
            target,
            data,
            options
        );
    }

    function render(data = [], options = {}) {
        const container = createElement(
            "section",
            "terminal-visualization terminal-visualization-network"
        );
        container.dataset.visualization =
            "network";
        container.setAttribute(
            "role",
            "region"
        );
        container.setAttribute(
            "aria-label",
            options.label ||
            "Network visualization"
        );

        const canvas =
            document.createElement("canvas");
        canvas.className =
            "terminal-network-canvas";
        canvas.width =
            Number(options.width) ||
            DEFAULT_WIDTH;
        canvas.height =
            Number(options.height) ||
            DEFAULT_HEIGHT;
        canvas.setAttribute(
            "aria-label",
            options.label ||
            "Network visualization"
        );

        const status = createElement(
            "div",
            "terminal-network-status"
        );
        status.setAttribute(
            "aria-live",
            "polite"
        );

        const tooltip = createElement(
            "div",
            "terminal-network-tooltip"
        );
        tooltip.hidden = true;

        container.append(
            canvas,
            status,
            tooltip
        );

        const controller =
            new NetworkController(
                canvas,
                data,
                options
            );

        const updateStatus = () => {
            const snapshot =
                controller.status();

            status.textContent =
                `${snapshot.visibleNodes} of ${snapshot.nodes} node` +
                `${snapshot.nodes === 1 ? "" : "s"} · ` +
                `${snapshot.visibleEdges} link` +
                `${snapshot.visibleEdges === 1 ? "" : "s"} · ` +
                `${snapshot.trafficPackets} packet` +
                `${snapshot.trafficPackets === 1 ? "" : "s"} · ` +
                `${snapshot.transform.zoom.toFixed(2)}×`;
        };

        controller.addEventListener(
            "hover",
            (event) => {
                const node =
                    event.detail?.node;

                if (!node) {
                    tooltip.hidden = true;
                    return;
                }

                tooltip.hidden = false;
                tooltip.textContent =
                    `${node.label} · ${node.group} · ` +
                    `${node.status} · ${node.degree} connection` +
                    `${node.degree === 1 ? "" : "s"}`;
            }
        );

        for (const eventName of [
            "data",
            "append",
            "resize",
            "zoom",
            "filter",
            "select",
            "update"
        ]) {
            controller.addEventListener(
                eventName,
                updateStatus
            );
        }

        updateStatus();

        container.controller =
            controller;
        container.canvas =
            canvas;
        container.data =
            controller.network.nodes;
        container.destroy = () =>
            controller.destroy();

        return container;
    }

    function initialize(context = {}) {
        const dataset =
            context.root?.dataset || {};
        const config =
            context.config?.network || {};

        const defaults = {
            background:
                dataset.terminalNetworkBackground ||
                config.background ||
                DEFAULT_BACKGROUND,

            foreground:
                dataset.terminalNetworkForeground ||
                config.foreground ||
                DEFAULT_FOREGROUND,

            highlight:
                dataset.terminalNetworkHighlight ||
                config.highlight ||
                DEFAULT_HIGHLIGHT,

            edgeColor:
                dataset.terminalNetworkEdgeColor ||
                config.edgeColor ||
                DEFAULT_EDGE,

            packetColor:
                dataset.terminalNetworkPacketColor ||
                config.packetColor ||
                DEFAULT_PACKET,

            nodeRadius:
                dataset.terminalNetworkNodeRadius ||
                config.nodeRadius ||
                DEFAULT_NODE_RADIUS,

            charge:
                dataset.terminalNetworkCharge ||
                config.charge ||
                DEFAULT_CHARGE,

            linkDistance:
                dataset.terminalNetworkLinkDistance ||
                config.linkDistance ||
                DEFAULT_LINK_DISTANCE,

            groupKey:
                dataset.terminalNetworkGroupKey ||
                config.groupKey ||
                null,

            showLabels: parseBoolean(
                dataset.terminalNetworkShowLabels,
                config.showLabels !== false
            ),

            showEdges: parseBoolean(
                dataset.terminalNetworkShowEdges,
                config.showEdges !== false
            ),

            showGroups: parseBoolean(
                dataset.terminalNetworkShowGroups,
                config.showGroups !== false
            ),

            showArrows: parseBoolean(
                dataset.terminalNetworkShowArrows,
                config.showArrows === true
            ),

            showTraffic: parseBoolean(
                dataset.terminalNetworkShowTraffic,
                config.showTraffic !== false
            ),

            animated: parseBoolean(
                dataset.terminalNetworkAnimated,
                config.animated !== false
            ),

            inferRelationships: parseBoolean(
                dataset.terminalNetworkInferRelationships,
                config.inferRelationships !== false
            ),

            interactive: parseBoolean(
                dataset.terminalNetworkInteractive,
                config.interactive !== false
            )
        };

        const visualization = {
            mount(target, data = [], options = {}) {
                return new NetworkController(
                    target,
                    data,
                    {
                        ...defaults,
                        ...options
                    }
                );
            },

            render(data = [], options = {}) {
                return render(
                    data,
                    {
                        ...defaults,
                        ...options
                    }
                );
            },

            Controller:
                NetworkController,

            normalizeNetwork,

            normalizeRecords,

            extractReferences
        };

        context.registerVisualization?.(
            "network",
            visualization
        );
        context.registerRenderer?.(
            "network",
            visualization
        );
        context.network =
            visualization;

        safeDispatch(
            document,
            "speciedex:terminal-network-ready",
            {
                visualization
            }
        );

        return visualization;
    }

    const commands = [{
        name: "network",
        category: "visualization",
        description:
            "Render and control an interactive network-topology visualization.",
        usage:
            "network [collection|status|start|stop|pause|resume|simulate|" +
            "filter|group|state|zoom|pan|reset|export] [arguments]",
        handler: ({
            args = [],
            context,
            writeJSON,
            write,
            writeError
        }) => {
            const action =
                String(
                    args[0] ||
                    "records"
                );
            const lower =
                action.toLowerCase();
            const controller =
                context.networkController ||
                context.terminalNetworkController;

            try {
                if (controller) {
                    switch (lower) {
                        case "status":
                        case "show":
                        case "info":
                            return writeJSON(
                                controller.status()
                            );

                        case "start":
                            controller.start();
                            return write(
                                "Network visualization started.",
                                "success"
                            );

                        case "stop":
                            controller.stop();
                            return write(
                                "Network visualization stopped.",
                                "success"
                            );

                        case "pause":
                            controller.pause();
                            return write(
                                "Network visualization paused.",
                                "success"
                            );

                        case "resume":
                            controller.resume();
                            return write(
                                "Network visualization resumed.",
                                "success"
                            );

                        case "simulate":
                            controller.simulate(
                                args[1] ||
                                120
                            );
                            return writeJSON(
                                controller.status()
                            );

                        case "filter":
                            return writeJSON({
                                query:
                                    controller.setFilter(
                                        args.slice(1).join(" ")
                                    ),
                                status:
                                    controller.status()
                            });

                        case "group":
                            return writeJSON({
                                group:
                                    controller.setGroup(
                                        args.slice(1).join(" ") ||
                                        null
                                    ),
                                status:
                                    controller.status()
                            });

                        case "state":
                            return writeJSON({
                                state:
                                    controller.setStatus(
                                        args.slice(1).join(" ") ||
                                        null
                                    ),
                                status:
                                    controller.status()
                            });

                        case "zoom":
                            if (
                                args[1] ===
                                undefined
                            ) {
                                return writeJSON({
                                    zoom:
                                        controller.transform.zoom
                                });
                            }

                            return writeJSON({
                                zoom:
                                    controller.setZoom(
                                        args[1]
                                    )
                            });

                        case "pan":
                            return writeJSON({
                                transform:
                                    controller.panBy(
                                        args[1],
                                        args[2]
                                    )
                            });

                        case "reset":
                            return writeJSON({
                                transform:
                                    controller.resetView()
                            });

                        case "export":
                            return write(
                                controller.export(
                                    args[1] ||
                                    "json"
                                ),
                                "data"
                            );

                        default:
                            break;
                    }
                }

                const collection =
                    action;
                const data =
                    context.library?.get?.(
                        collection
                    ) ||
                    context.state?.get?.(
                        `library.${collection}`,
                        []
                    ) ||
                    [];

                return render(
                    data,
                    {
                        ...context.config?.network,
                        label:
                            `Network for ${collection}`
                    }
                );
            } catch (error) {
                if (
                    typeof writeError ===
                    "function"
                ) {
                    writeError(
                        error.message
                    );
                    return null;
                }

                throw error;
            }
        }
    }];

    const api = Object.freeze({
        name: MODULE_NAME,
        NetworkController,
        normalizeNetwork,
        normalizeRecords,
        extractReferences,
        mount,
        render,
        initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalNetwork =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

    document.dispatchEvent(
        new CustomEvent(
            "speciedex:terminal-module-available",
            {
                detail: {
                    name: MODULE_NAME,
                    module: api
                }
            }
        )
    );
})(window, document);
