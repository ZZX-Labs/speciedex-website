/*
========================================================================
Speciedex.org
Terminal Phylogeny Visualization
========================================================================

Interactive phylogenetic and taxonomic tree renderer for Speciedex records.
Supports explicit parent/child structures, lineage inference, rectangular and
radial layouts, branch lengths, collapsed clades, searching, selection, zoom,
pan, responsive rendering, exports, diagnostics, and lifecycle control.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Phylogeny";
    const DEFAULT_WIDTH = 960;
    const DEFAULT_HEIGHT = 540;
    const DEFAULT_BACKGROUND = "#020a05";
    const DEFAULT_FOREGROUND = "#c0d674";
    const DEFAULT_HIGHLIGHT = "#eef7c8";
    const DEFAULT_BRANCH = "#35503a";
    const DEFAULT_NODE_RADIUS = 4;
    const DEFAULT_PADDING = 48;
    const DEFAULT_MAX_NODES = 10000;

    const RANK_ORDER = Object.freeze([
        "domain",
        "kingdom",
        "phylum",
        "class",
        "order",
        "family",
        "genus",
        "species",
        "subspecies",
        "variety",
        "form",
        "unranked"
    ]);

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

        if (value === undefined || value === null || typeof value !== "object") {
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
            /* Visualization events must never interrupt rendering. */
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
            "Phylogeny visualization requires a canvas or container element."
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
            return `taxon-${index + 1}`;
        }

        return String(firstValue(record, [
            "speciedex_id",
            "speciedexId",
            "taxon_id",
            "taxonId",
            "canonical_id",
            "canonicalId",
            "id",
            "key",
            "uuid"
        ], `taxon-${index + 1}`));
    }

    function nodeLabel(record, index) {
        if (!isObject(record)) {
            return String(record ?? `Taxon ${index + 1}`);
        }

        return String(firstValue(record, [
            "scientific_name",
            "scientificName",
            "canonical_name",
            "canonicalName",
            "accepted_name",
            "acceptedName",
            "common_name",
            "commonName",
            "name",
            "label"
        ], `Taxon ${index + 1}`));
    }

    function nodeRank(record) {
        if (!isObject(record)) {
            return "unranked";
        }

        return String(firstValue(record, [
            "rank",
            "taxon_rank",
            "taxonRank"
        ], "unranked")).toLowerCase();
    }

    function nodeParentId(record) {
        if (!isObject(record)) {
            return null;
        }

        const value = firstValue(record, [
            "parent_id",
            "parentId",
            "parent_taxon_id",
            "parentTaxonId",
            "accepted_parent_id",
            "acceptedParentId"
        ], null);

        return value === null ? null : String(value);
    }

    function nodeBranchLength(record) {
        if (!isObject(record)) {
            return 1;
        }

        for (const key of [
            "branch_length",
            "branchLength",
            "distance",
            "evolutionary_distance",
            "evolutionaryDistance"
        ]) {
            const value = Number(record[key]);

            if (Number.isFinite(value)) {
                return Math.max(0, value);
            }
        }

        return 1;
    }

    function nodeWeight(record) {
        if (!isObject(record)) {
            return 1;
        }

        for (const key of [
            "weight",
            "count",
            "value",
            "abundance",
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

    function rankIndex(rank) {
        const index = RANK_ORDER.indexOf(String(rank || "").toLowerCase());
        return index === -1 ? RANK_ORDER.length : index;
    }

    function colorHash(value) {
        let hash = 0;

        for (const character of String(value || "")) {
            hash = ((hash << 5) - hash) + character.charCodeAt(0);
            hash |= 0;
        }

        return `hsl(${Math.abs(hash) % 360} 52% 60%)`;
    }

    function inferLineageParent(record, labelMap) {
        if (!isObject(record)) {
            return null;
        }

        const currentRank = nodeRank(record);
        const currentIndex = rankIndex(currentRank);

        for (
            let index = currentIndex - 1;
            index >= 0;
            index -= 1
        ) {
            const rank = RANK_ORDER[index];
            const value = firstValue(record, [
                rank,
                `${rank}_name`,
                `${rank}Name`
            ], null);

            if (!value) {
                continue;
            }

            const id = labelMap.get(String(value));

            if (id) {
                return id;
            }
        }

        return null;
    }

    function normalizeTree(data, options = {}) {
        const records = normalizeRecords(data).slice(
            0,
            parseNumber(options.maxNodes, DEFAULT_MAX_NODES, 1, 100000)
        );
        const nodes = [];
        const byId = new Map();
        const labelMap = new Map();

        records.forEach((record, index) => {
            const id = nodeId(record, index);

            if (byId.has(id)) {
                return;
            }

            const node = {
                id,
                label: nodeLabel(record, index),
                rank: nodeRank(record),
                parentId: nodeParentId(record),
                branchLength: nodeBranchLength(record),
                weight: nodeWeight(record),
                children: [],
                parent: null,
                depth: 0,
                leafCount: 1,
                collapsed: false,
                visible: true,
                matched: true,
                x: 0,
                y: 0,
                angle: 0,
                radius: 0,
                raw: clone(record)
            };

            nodes.push(node);
            byId.set(id, node);
            labelMap.set(node.label, id);
        });

        if (options.inferLineage !== false) {
            for (const node of nodes) {
                if (node.parentId && byId.has(node.parentId)) {
                    continue;
                }

                const inferred = inferLineageParent(node.raw, labelMap);

                if (inferred && inferred !== node.id) {
                    node.parentId = inferred;
                }
            }
        }

        for (const node of nodes) {
            if (!node.parentId || !byId.has(node.parentId)) {
                continue;
            }

            const parent = byId.get(node.parentId);

            if (parent === node) {
                continue;
            }

            node.parent = parent;
            parent.children.push(node);
        }

        const roots = nodes.filter((node) => !node.parent);

        const visit = (node, depth, stack = new Set()) => {
            if (stack.has(node.id)) {
                node.parent = null;
                node.parentId = null;
                return 1;
            }

            stack.add(node.id);
            node.depth = depth;

            if (!node.children.length) {
                node.leafCount = 1;
                stack.delete(node.id);
                return 1;
            }

            node.children.sort((left, right) => {
                return (
                    rankIndex(left.rank) - rankIndex(right.rank) ||
                    left.label.localeCompare(right.label)
                );
            });

            node.leafCount = node.children.reduce(
                (total, child) =>
                    total + visit(child, depth + 1, new Set(stack)),
                0
            );

            stack.delete(node.id);
            return node.leafCount;
        };

        roots.sort((left, right) =>
            left.label.localeCompare(right.label)
        );

        roots.forEach((root) => visit(root, 0));

        return {
            nodes,
            roots,
            byId
        };
    }

    class PhylogenyController extends EventTarget {
        constructor(target, data = [], options = {}) {
            super();

            this.canvas = resolveCanvas(target);
            this.context = this.canvas.getContext("2d", {
                alpha: true,
                desynchronized: true
            });

            if (!this.context) {
                throw new Error(
                    "Unable to acquire Phylogeny 2D canvas context."
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
                branchColor:
                    options.branchColor ||
                    DEFAULT_BRANCH,
                nodeRadius: parseNumber(
                    options.nodeRadius,
                    DEFAULT_NODE_RADIUS,
                    1,
                    24
                ),
                padding: parseNumber(
                    options.padding,
                    DEFAULT_PADDING,
                    16,
                    240
                ),
                layout:
                    options.layout === "radial"
                        ? "radial"
                        : "rectangular",
                branchLengthMode:
                    options.branchLengthMode === "scaled"
                        ? "scaled"
                        : "depth",
                orientation:
                    options.orientation === "vertical"
                        ? "vertical"
                        : "horizontal",
                showLabels:
                    options.showLabels !== false,
                showRanks:
                    options.showRanks === true,
                showBranchLengths:
                    options.showBranchLengths === true,
                showInternalNodes:
                    options.showInternalNodes !== false,
                showLeaves:
                    options.showLeaves !== false,
                showGrid:
                    options.showGrid === true,
                inferLineage:
                    options.inferLineage !== false,
                maxNodes: parseNumber(
                    options.maxNodes,
                    DEFAULT_MAX_NODES,
                    1,
                    100000
                ),
                interactive:
                    options.interactive !== false,
                zoomable:
                    options.zoomable !== false,
                pannable:
                    options.pannable !== false,
                label:
                    options.label ||
                    "Phylogeny visualization"
            };

            this.tree = {
                nodes: [],
                roots: [],
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
            this.visibleNodes = [];
            this.visibleEdges = [];
            this.hovered = null;
            this.selected = null;
            this.drag = null;
            this.query = "";
            this.rankFilter = null;
            this.destroyed = false;
            this.lastError = null;
            this.metrics = {
                inputRecords: 0,
                nodes: 0,
                roots: 0,
                visibleNodes: 0,
                visibleEdges: 0,
                collapsedNodes: 0,
                draws: 0,
                resizes: 0,
                zooms: 0,
                pans: 0,
                selections: 0,
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
            this._boundDoubleClick =
                this._handleDoubleClick.bind(this);
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
                    "dblclick",
                    this._boundDoubleClick
                );
                this.canvas.addEventListener(
                    "keydown",
                    this._boundKeydown
                );
            }

            this.resize();
            this.setData(data);
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
            this.layout();
            this.draw();

            this._emit("resize", clone(this.bounds));
        }

        setData(data) {
            try {
                this.tree = normalizeTree(
                    data,
                    this.options
                );
                this.metrics.inputRecords =
                    normalizeRecords(data).length;
                this.metrics.nodes =
                    this.tree.nodes.length;
                this.metrics.roots =
                    this.tree.roots.length;
                this.hovered = null;
                this.selected = null;
                this._applyFilters();
                this.layout();
                this.draw();

                this._emit("data", {
                    nodes: this.tree.nodes.length,
                    roots: this.tree.roots.length
                });
            } catch (error) {
                this._recordError(error);
            }

            return this;
        }

        append(data) {
            const combined = [
                ...this.tree.nodes.map((node) => node.raw),
                ...normalizeRecords(data)
            ];

            this.setData(combined);

            this._emit("append", {
                added: normalizeRecords(data).length
            });

            return this;
        }

        _applyFilters() {
            const query = this.query.toLowerCase();

            for (const node of this.tree.nodes) {
                node.matched =
                    (
                        !query ||
                        node.label.toLowerCase().includes(query) ||
                        node.id.toLowerCase().includes(query) ||
                        node.rank.toLowerCase().includes(query)
                    ) &&
                    (
                        !this.rankFilter ||
                        node.rank === this.rankFilter
                    );
            }

            const propagate = (node) => {
                const childMatch = node.children.some(propagate);
                node.visible = node.matched || childMatch;
                return node.visible;
            };

            this.tree.roots.forEach(propagate);
        }

        _collectVisible() {
            const nodes = [];
            const edges = [];

            const visit = (node) => {
                if (!node.visible) {
                    return;
                }

                nodes.push(node);

                if (node.collapsed) {
                    return;
                }

                for (const child of node.children) {
                    if (!child.visible) {
                        continue;
                    }

                    edges.push({
                        source: node,
                        target: child,
                        branchLength: child.branchLength
                    });
                    visit(child);
                }
            };

            this.tree.roots.forEach(visit);

            this.visibleNodes = nodes;
            this.visibleEdges = edges;
            this.metrics.visibleNodes = nodes.length;
            this.metrics.visibleEdges = edges.length;
            this.metrics.collapsedNodes =
                this.tree.nodes.filter(
                    (node) => node.collapsed
                ).length;
        }

        layout() {
            this._collectVisible();

            if (!this.visibleNodes.length) {
                return;
            }

            if (this.options.layout === "radial") {
                this._layoutRadial();
            } else {
                this._layoutRectangular();
            }
        }

        _layoutRectangular() {
            const padding = this.options.padding;
            const width = Math.max(
                1,
                this.bounds.width - padding * 2
            );
            const height = Math.max(
                1,
                this.bounds.height - padding * 2
            );
            const leaves = this.visibleNodes.filter(
                (node) =>
                    node.collapsed ||
                    !node.children.some(
                        (child) => child.visible
                    )
            );
            const maxDepth = Math.max(
                ...this.visibleNodes.map((node) => node.depth),
                1
            );
            const maxDistance = Math.max(
                ...this.visibleNodes.map(
                    (node) => this._cumulativeBranchLength(node)
                ),
                1
            );

            leaves.forEach((leaf, index) => {
                leaf.y =
                    padding +
                    (
                        leaves.length === 1
                            ? 0.5
                            : index / (leaves.length - 1)
                    ) *
                    height;
            });

            const placeInternal = (node) => {
                const visibleChildren = node.children.filter(
                    (child) =>
                        child.visible &&
                        !node.collapsed
                );

                visibleChildren.forEach(placeInternal);

                if (visibleChildren.length) {
                    node.y =
                        visibleChildren.reduce(
                            (total, child) => total + child.y,
                            0
                        ) /
                        visibleChildren.length;
                }

                const depthRatio =
                    this.options.branchLengthMode === "scaled"
                        ? this._cumulativeBranchLength(node) / maxDistance
                        : node.depth / maxDepth;

                node.x =
                    padding +
                    depthRatio *
                    width;
            };

            this.tree.roots
                .filter((root) => root.visible)
                .forEach(placeInternal);

            if (this.options.orientation === "vertical") {
                for (const node of this.visibleNodes) {
                    const x = node.x;
                    node.x = node.y;
                    node.y = x;
                }
            }
        }

        _layoutRadial() {
            const centerX = this.bounds.width / 2;
            const centerY = this.bounds.height / 2;
            const radius = Math.max(
                1,
                Math.min(
                    this.bounds.width,
                    this.bounds.height
                ) /
                2 -
                this.options.padding
            );
            const leaves = this.visibleNodes.filter(
                (node) =>
                    node.collapsed ||
                    !node.children.some(
                        (child) => child.visible
                    )
            );
            const maxDepth = Math.max(
                ...this.visibleNodes.map((node) => node.depth),
                1
            );
            const maxDistance = Math.max(
                ...this.visibleNodes.map(
                    (node) => this._cumulativeBranchLength(node)
                ),
                1
            );

            leaves.forEach((leaf, index) => {
                leaf.angle =
                    (
                        index /
                        Math.max(1, leaves.length)
                    ) *
                    Math.PI *
                    2 -
                    Math.PI / 2;
            });

            const placeInternal = (node) => {
                const visibleChildren = node.children.filter(
                    (child) =>
                        child.visible &&
                        !node.collapsed
                );

                visibleChildren.forEach(placeInternal);

                if (visibleChildren.length) {
                    const x =
                        visibleChildren.reduce(
                            (total, child) =>
                                total + Math.cos(child.angle),
                            0
                        );
                    const y =
                        visibleChildren.reduce(
                            (total, child) =>
                                total + Math.sin(child.angle),
                            0
                        );
                    node.angle = Math.atan2(y, x);
                }

                const depthRatio =
                    this.options.branchLengthMode === "scaled"
                        ? this._cumulativeBranchLength(node) / maxDistance
                        : node.depth / maxDepth;

                node.radius = depthRatio * radius;
                node.x =
                    centerX +
                    Math.cos(node.angle) *
                    node.radius;
                node.y =
                    centerY +
                    Math.sin(node.angle) *
                    node.radius;
            };

            this.tree.roots
                .filter((root) => root.visible)
                .forEach(placeInternal);
        }

        _cumulativeBranchLength(node) {
            let total = 0;
            let current = node;

            while (current && current.parent) {
                total += current.branchLength;
                current = current.parent;
            }

            return total;
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

            if (!this.visibleNodes.length) {
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
                    "No phylogenetic nodes.",
                    this.bounds.width / 2,
                    this.bounds.height / 2
                );
                this.context.globalAlpha = 1;
                this.metrics.draws += 1;
                return;
            }

            if (this.options.showGrid) {
                this._drawGrid();
            }

            this._drawBranches();
            this._drawNodes();

            if (this.options.showLabels) {
                this._drawLabels();
            }

            this.metrics.draws += 1;
        }

        _drawGrid() {
            this.context.save();
            this.context.strokeStyle =
                this.options.branchColor;
            this.context.globalAlpha = 0.15;
            this.context.lineWidth = 1;

            if (this.options.layout === "radial") {
                const center =
                    this._screenPoint(
                        this.bounds.width / 2,
                        this.bounds.height / 2
                    );
                const maxDepth = Math.max(
                    ...this.visibleNodes.map(
                        (node) => node.depth
                    ),
                    1
                );
                const radius =
                    Math.min(
                        this.bounds.width,
                        this.bounds.height
                    ) /
                    2 -
                    this.options.padding;

                for (
                    let depth = 1;
                    depth <= maxDepth;
                    depth += 1
                ) {
                    this.context.beginPath();
                    this.context.arc(
                        center.x,
                        center.y,
                        radius *
                        (
                            depth /
                            maxDepth
                        ) *
                        this.transform.zoom,
                        0,
                        Math.PI * 2
                    );
                    this.context.stroke();
                }
            } else {
                const maxDepth = Math.max(
                    ...this.visibleNodes.map(
                        (node) => node.depth
                    ),
                    1
                );

                for (
                    let depth = 0;
                    depth <= maxDepth;
                    depth += 1
                ) {
                    const x =
                        this.options.padding +
                        (
                            depth /
                            maxDepth
                        ) *
                        (
                            this.bounds.width -
                            this.options.padding *
                            2
                        );
                    const screen =
                        this._screenPoint(
                            x,
                            0
                        );

                    this.context.beginPath();
                    this.context.moveTo(
                        screen.x,
                        0
                    );
                    this.context.lineTo(
                        screen.x,
                        this.bounds.height
                    );
                    this.context.stroke();
                }
            }

            this.context.restore();
        }

        _drawBranches() {
            this.context.save();
            this.context.strokeStyle =
                this.options.branchColor;
            this.context.globalAlpha = 0.78;
            this.context.lineWidth = 1.2;

            for (const edge of this.visibleEdges) {
                const source =
                    this._screenPoint(
                        edge.source.x,
                        edge.source.y
                    );
                const target =
                    this._screenPoint(
                        edge.target.x,
                        edge.target.y
                    );

                this.context.beginPath();

                if (
                    this.options.layout === "rectangular" &&
                    this.options.orientation === "horizontal"
                ) {
                    this.context.moveTo(
                        source.x,
                        source.y
                    );
                    this.context.lineTo(
                        target.x,
                        source.y
                    );
                    this.context.lineTo(
                        target.x,
                        target.y
                    );
                } else if (
                    this.options.layout === "rectangular"
                ) {
                    this.context.moveTo(
                        source.x,
                        source.y
                    );
                    this.context.lineTo(
                        source.x,
                        target.y
                    );
                    this.context.lineTo(
                        target.x,
                        target.y
                    );
                } else {
                    this.context.moveTo(
                        source.x,
                        source.y
                    );
                    this.context.lineTo(
                        target.x,
                        target.y
                    );
                }

                this.context.stroke();

                if (
                    this.options.showBranchLengths
                ) {
                    this.context.fillStyle =
                        this.options.foreground;
                    this.context.globalAlpha =
                        0.62;
                    this.context.font =
                        '9px "IBM Plex Mono", monospace';
                    this.context.textAlign =
                        "center";
                    this.context.textBaseline =
                        "bottom";
                    this.context.fillText(
                        Number(
                            edge.branchLength.toPrecision(4)
                        ).toString(),
                        (
                            source.x +
                            target.x
                        ) /
                        2,
                        (
                            source.y +
                            target.y
                        ) /
                        2 -
                        2
                    );
                    this.context.globalAlpha =
                        0.78;
                }
            }

            this.context.restore();
        }

        _drawNodes() {
            this.context.save();

            for (const node of this.visibleNodes) {
                const isLeaf =
                    node.collapsed ||
                    !node.children.some(
                        (child) => child.visible
                    );

                if (
                    (isLeaf && !this.options.showLeaves) ||
                    (!isLeaf && !this.options.showInternalNodes)
                ) {
                    continue;
                }

                const point =
                    this._screenPoint(
                        node.x,
                        node.y
                    );
                const emphasized =
                    node.id === this.selected?.id ||
                    node.id === this.hovered?.id;
                const radius =
                    this.options.nodeRadius *
                    (
                        0.8 +
                        Math.min(
                            2,
                            Math.sqrt(
                                node.weight
                            ) *
                            0.12
                        )
                    ) *
                    Math.sqrt(
                        this.transform.zoom
                    );

                this.context.beginPath();

                if (node.collapsed) {
                    this.context.moveTo(
                        point.x - radius,
                        point.y - radius
                    );
                    this.context.lineTo(
                        point.x + radius,
                        point.y
                    );
                    this.context.lineTo(
                        point.x - radius,
                        point.y + radius
                    );
                    this.context.closePath();
                } else {
                    this.context.arc(
                        point.x,
                        point.y,
                        radius,
                        0,
                        Math.PI * 2
                    );
                }

                this.context.fillStyle =
                    emphasized
                        ? this.options.highlight
                        : colorHash(node.rank);
                this.context.globalAlpha =
                    emphasized ? 1 : 0.88;

                if (emphasized) {
                    this.context.shadowColor =
                        this.options.highlight;
                    this.context.shadowBlur = 12;
                } else {
                    this.context.shadowBlur = 0;
                }

                this.context.fill();

                node.screenX = point.x;
                node.screenY = point.y;
                node.screenRadius = radius;
            }

            this.context.restore();
        }

        _drawLabels() {
            this.context.save();
            this.context.font =
                '11px "IBM Plex Mono", monospace';
            this.context.textBaseline =
                "middle";

            for (const node of this.visibleNodes) {
                const isLeaf =
                    node.collapsed ||
                    !node.children.some(
                        (child) => child.visible
                    );

                if (
                    !isLeaf &&
                    node.id !== this.selected?.id &&
                    node.id !== this.hovered?.id
                ) {
                    continue;
                }

                if (
                    this.transform.zoom < 0.65 &&
                    node.id !== this.selected?.id &&
                    node.id !== this.hovered?.id
                ) {
                    continue;
                }

                const point =
                    this._screenPoint(
                        node.x,
                        node.y
                    );
                const emphasized =
                    node.id === this.selected?.id ||
                    node.id === this.hovered?.id;
                const label =
                    this.options.showRanks
                        ? `${node.label} [${node.rank}]`
                        : node.label;

                this.context.fillStyle =
                    emphasized
                        ? this.options.highlight
                        : this.options.foreground;
                this.context.globalAlpha =
                    emphasized ? 1 : 0.72;

                if (
                    this.options.layout === "radial"
                ) {
                    const rightSide =
                        Math.cos(node.angle) >= 0;
                    this.context.textAlign =
                        rightSide
                            ? "left"
                            : "right";
                    this.context.fillText(
                        label,
                        point.x +
                        (
                            rightSide
                                ? 1
                                : -1
                        ) *
                        (
                            node.screenRadius +
                            5
                        ),
                        point.y
                    );
                } else {
                    this.context.textAlign =
                        "left";
                    this.context.fillText(
                        label,
                        point.x +
                        node.screenRadius +
                        5,
                        point.y
                    );
                }
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
                    this.visibleNodes.length -
                    1;
                index >= 0;
                index -= 1
            ) {
                const node =
                    this.visibleNodes[index];
                const dx =
                    x - node.screenX;
                const dy =
                    y - node.screenY;
                const radius =
                    node.screenRadius + 5;

                if (
                    dx * dx + dy * dy <=
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

            if (this.drag) {
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

            this.hovered = hovered;
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
            if (
                !this.options.pannable ||
                event.button !== 0
            ) {
                return;
            }

            const point =
                this._pointFromEvent(event);

            this.drag = {
                startX: point.x,
                startY: point.y,
                originX:
                    this.transform.x,
                originY:
                    this.transform.y
            };

            this.canvas.setPointerCapture?.(
                event.pointerId
            );
        }

        _handlePointerUp(event) {
            if (!this.drag) {
                return;
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
                node?.id === this.selected?.id
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

        _handleDoubleClick(event) {
            const point =
                this._pointFromEvent(event);
            const node =
                this.hitTest(
                    point.x,
                    point.y
                );

            if (
                node &&
                node.children.length
            ) {
                this.toggleNode(node.id);
            }
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
            } else if (
                event.key === "Enter" &&
                this.selected?.children.length
            ) {
                event.preventDefault();
                this.toggleNode(
                    this.selected.id
                );
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

        toggleNode(id) {
            const node =
                this.tree.byId.get(
                    String(id)
                );

            if (!node || !node.children.length) {
                return null;
            }

            node.collapsed =
                !node.collapsed;
            this.layout();
            this.draw();

            this._emit("toggle", {
                node:
                    this.describeNode(node)
            });

            return node.collapsed;
        }

        collapseAll() {
            for (const node of this.tree.nodes) {
                if (node.children.length) {
                    node.collapsed = true;
                }
            }

            this.layout();
            this.draw();
            return this.metrics.collapsedNodes;
        }

        expandAll() {
            for (const node of this.tree.nodes) {
                node.collapsed = false;
            }

            this.layout();
            this.draw();
            return 0;
        }

        setFilter(query = "") {
            this.query =
                String(query || "");
            this._applyFilters();
            this.layout();
            this.draw();

            this._emit("filter", {
                query:
                    this.query,
                visibleNodes:
                    this.visibleNodes.length
            });

            return this.query;
        }

        setRank(rank = null) {
            this.rankFilter =
                rank
                    ? String(rank).toLowerCase()
                    : null;
            this._applyFilters();
            this.layout();
            this.draw();
            return this.rankFilter;
        }

        setLayout(layout) {
            if (
                ![
                    "rectangular",
                    "radial"
                ].includes(layout)
            ) {
                throw new Error(
                    `Unknown phylogeny layout: ${layout}`
                );
            }

            this.options.layout = layout;
            this.layout();
            this.draw();
            return layout;
        }

        selectNode(id) {
            const node =
                this.tree.byId.get(
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

            return {
                id: node.id,
                label: node.label,
                rank: node.rank,
                parentId:
                    node.parent?.id ||
                    null,
                branchLength:
                    node.branchLength,
                depth:
                    node.depth,
                leafCount:
                    node.leafCount,
                children:
                    node.children.map(
                        (child) => ({
                            id: child.id,
                            label: child.label,
                            rank: child.rank
                        })
                    ),
                collapsed:
                    node.collapsed,
                visible:
                    node.visible,
                raw:
                    clone(node.raw)
            };
        }

        update(options = {}) {
            if (!isObject(options)) {
                throw new TypeError(
                    "Phylogeny options must be an object."
                );
            }

            const rebuildRequired = [
                "inferLineage",
                "maxNodes"
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
                    branchColor:
                        options.branchColor ||
                        this.options.branchColor,
                    nodeRadius:
                        options.nodeRadius !== undefined
                            ? parseNumber(
                                options.nodeRadius,
                                this.options.nodeRadius,
                                1,
                                24
                            )
                            : this.options.nodeRadius,
                    padding:
                        options.padding !== undefined
                            ? parseNumber(
                                options.padding,
                                this.options.padding,
                                16,
                                240
                            )
                            : this.options.padding,
                    layout:
                        options.layout ||
                        this.options.layout,
                    branchLengthMode:
                        options.branchLengthMode ||
                        this.options.branchLengthMode,
                    orientation:
                        options.orientation ||
                        this.options.orientation,
                    showLabels:
                        options.showLabels !== undefined
                            ? Boolean(
                                options.showLabels
                            )
                            : this.options.showLabels,
                    showRanks:
                        options.showRanks !== undefined
                            ? Boolean(
                                options.showRanks
                            )
                            : this.options.showRanks,
                    showBranchLengths:
                        options.showBranchLengths !== undefined
                            ? Boolean(
                                options.showBranchLengths
                            )
                            : this.options.showBranchLengths,
                    showInternalNodes:
                        options.showInternalNodes !== undefined
                            ? Boolean(
                                options.showInternalNodes
                            )
                            : this.options.showInternalNodes,
                    showLeaves:
                        options.showLeaves !== undefined
                            ? Boolean(
                                options.showLeaves
                            )
                            : this.options.showLeaves,
                    showGrid:
                        options.showGrid !== undefined
                            ? Boolean(
                                options.showGrid
                            )
                            : this.options.showGrid,
                    inferLineage:
                        options.inferLineage !== undefined
                            ? Boolean(
                                options.inferLineage
                            )
                            : this.options.inferLineage,
                    maxNodes:
                        options.maxNodes !== undefined
                            ? parseNumber(
                                options.maxNodes,
                                this.options.maxNodes,
                                1,
                                100000
                            )
                            : this.options.maxNodes
                }
            );

            if (rebuildRequired) {
                this.setData(
                    this.tree.nodes.map(
                        (node) => node.raw
                    )
                );
            } else {
                this.layout();
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

            if (normalized === "newick") {
                const serialize = (node) => {
                    const children =
                        node.collapsed
                            ? []
                            : node.children;

                    const prefix =
                        children.length
                            ? `(${children.map(serialize).join(",")})`
                            : "";

                    const safeLabel =
                        node.label.replace(
                            /[\s,:;()]/g,
                            "_"
                        );

                    return (
                        prefix +
                        safeLabel +
                        ":" +
                        Number(
                            node.branchLength.toPrecision(8)
                        )
                    );
                };

                return (
                    this.tree.roots
                        .map(serialize)
                        .join(",") +
                    ";"
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
                        roots:
                            this.tree.roots.map(
                                (root) => root.id
                            ),
                        nodes:
                            this.tree.nodes.map(
                                (node) =>
                                    this.describeNode(
                                        node
                                    )
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
                    "rank",
                    "parentId",
                    "branchLength",
                    "depth",
                    "leafCount",
                    "collapsed",
                    "visible"
                ]];

                for (const node of this.tree.nodes) {
                    rows.push([
                        node.id,
                        node.label,
                        node.rank,
                        node.parent?.id || "",
                        node.branchLength,
                        node.depth,
                        node.leafCount,
                        node.collapsed,
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

                            return /[",\n\r]/.test(text)
                                ? `"${text.replace(/"/g, '""')}"`
                                : text;
                        }).join(",")
                    )
                    .join("\r\n");
            }

            throw new Error(
                `Unsupported Phylogeny export format: ${format}`
            );
        }

        status() {
            return {
                name:
                    "phylogeny",
                module:
                    MODULE_NAME,
                nodes:
                    this.tree.nodes.length,
                roots:
                    this.tree.roots.length,
                visibleNodes:
                    this.visibleNodes.length,
                visibleEdges:
                    this.visibleEdges.length,
                collapsedNodes:
                    this.tree.nodes.filter(
                        (node) => node.collapsed
                    ).length,
                query:
                    this.query,
                rankFilter:
                    this.rankFilter,
                layout:
                    this.options.layout,
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
                    "dblclick",
                    this._boundDoubleClick
                );
                this.canvas.removeEventListener(
                    "keydown",
                    this._boundKeydown
                );
            }

            this.tree = {
                nodes: [],
                roots: [],
                byId: new Map()
            };
            this.visibleNodes = [];
            this.visibleEdges = [];
            this.destroyed = true;
            this._emit("destroy", {});
            return true;
        }
    }

    function mount(target, data = [], options = {}) {
        return new PhylogenyController(
            target,
            data,
            options
        );
    }

    function render(data = [], options = {}) {
        const container = createElement(
            "section",
            "terminal-visualization terminal-visualization-phylogeny"
        );
        container.dataset.visualization =
            "phylogeny";
        container.setAttribute(
            "role",
            "region"
        );
        container.setAttribute(
            "aria-label",
            options.label ||
            "Phylogeny visualization"
        );

        const canvas =
            document.createElement("canvas");
        canvas.className =
            "terminal-phylogeny-canvas";
        canvas.width =
            Number(options.width) ||
            DEFAULT_WIDTH;
        canvas.height =
            Number(options.height) ||
            DEFAULT_HEIGHT;
        canvas.setAttribute(
            "aria-label",
            options.label ||
            "Phylogeny visualization"
        );

        const status = createElement(
            "div",
            "terminal-phylogeny-status"
        );
        status.setAttribute(
            "aria-live",
            "polite"
        );

        const tooltip = createElement(
            "div",
            "terminal-phylogeny-tooltip"
        );
        tooltip.hidden = true;

        container.append(
            canvas,
            status,
            tooltip
        );

        const controller =
            new PhylogenyController(
                canvas,
                data,
                options
            );

        const updateStatus = () => {
            const snapshot =
                controller.status();

            status.textContent =
                `${snapshot.visibleNodes} of ${snapshot.nodes} taxon` +
                `${snapshot.nodes === 1 ? "" : "a"} · ` +
                `${snapshot.roots} root` +
                `${snapshot.roots === 1 ? "" : "s"} · ` +
                `${snapshot.collapsedNodes} collapsed · ` +
                `${snapshot.layout}`;
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
                    `${node.label} · ${node.rank} · ` +
                    `${node.children.length} child` +
                    `${node.children.length === 1 ? "" : "ren"} · ` +
                    `${node.leafCount} leaf` +
                    `${node.leafCount === 1 ? "" : "s"}`;
            }
        );

        for (const eventName of [
            "data",
            "append",
            "resize",
            "zoom",
            "filter",
            "toggle",
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
            controller.tree.nodes;
        container.destroy = () =>
            controller.destroy();

        return container;
    }

    function initialize(context = {}) {
        const dataset =
            context.root?.dataset || {};
        const config =
            context.config?.phylogeny || {};

        const defaults = {
            background:
                dataset.terminalPhylogenyBackground ||
                config.background ||
                DEFAULT_BACKGROUND,

            foreground:
                dataset.terminalPhylogenyForeground ||
                config.foreground ||
                DEFAULT_FOREGROUND,

            highlight:
                dataset.terminalPhylogenyHighlight ||
                config.highlight ||
                DEFAULT_HIGHLIGHT,

            branchColor:
                dataset.terminalPhylogenyBranchColor ||
                config.branchColor ||
                DEFAULT_BRANCH,

            nodeRadius:
                dataset.terminalPhylogenyNodeRadius ||
                config.nodeRadius ||
                DEFAULT_NODE_RADIUS,

            layout:
                dataset.terminalPhylogenyLayout ||
                config.layout ||
                "rectangular",

            branchLengthMode:
                dataset.terminalPhylogenyBranchLengthMode ||
                config.branchLengthMode ||
                "depth",

            orientation:
                dataset.terminalPhylogenyOrientation ||
                config.orientation ||
                "horizontal",

            showLabels: parseBoolean(
                dataset.terminalPhylogenyShowLabels,
                config.showLabels !== false
            ),

            showRanks: parseBoolean(
                dataset.terminalPhylogenyShowRanks,
                config.showRanks === true
            ),

            showBranchLengths: parseBoolean(
                dataset.terminalPhylogenyShowBranchLengths,
                config.showBranchLengths === true
            ),

            inferLineage: parseBoolean(
                dataset.terminalPhylogenyInferLineage,
                config.inferLineage !== false
            ),

            interactive: parseBoolean(
                dataset.terminalPhylogenyInteractive,
                config.interactive !== false
            )
        };

        const visualization = {
            mount(target, data = [], options = {}) {
                return new PhylogenyController(
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
                PhylogenyController,

            normalizeTree,

            normalizeRecords,

            rankIndex
        };

        context.registerVisualization?.(
            "phylogeny",
            visualization
        );
        context.registerRenderer?.(
            "phylogeny",
            visualization
        );
        context.phylogeny =
            visualization;

        safeDispatch(
            document,
            "speciedex:terminal-phylogeny-ready",
            {
                visualization
            }
        );

        return visualization;
    }

    const commands = [{
        name: "phylogeny",
        category: "visualization",
        description:
            "Render and control an interactive phylogenetic or taxonomic tree.",
        usage:
            "phylogeny [collection|status|layout|filter|rank|collapse|" +
            "expand|toggle|zoom|pan|reset|export] [arguments]",
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
                context.phylogenyController ||
                context.terminalPhylogenyController;

            try {
                if (controller) {
                    switch (lower) {
                        case "status":
                        case "show":
                        case "info":
                            return writeJSON(
                                controller.status()
                            );

                        case "layout":
                            if (!args[1]) {
                                return writeJSON({
                                    layout:
                                        controller.options.layout
                                });
                            }

                            return writeJSON({
                                layout:
                                    controller.setLayout(
                                        args[1]
                                    )
                            });

                        case "filter":
                            return writeJSON({
                                query:
                                    controller.setFilter(
                                        args.slice(1).join(" ")
                                    ),
                                status:
                                    controller.status()
                            });

                        case "rank":
                            return writeJSON({
                                rank:
                                    controller.setRank(
                                        args.slice(1).join(" ") ||
                                        null
                                    ),
                                status:
                                    controller.status()
                            });

                        case "collapse":
                            return writeJSON({
                                collapsed:
                                    controller.collapseAll(),
                                status:
                                    controller.status()
                            });

                        case "expand":
                            controller.expandAll();
                            return writeJSON(
                                controller.status()
                            );

                        case "toggle":
                            return writeJSON({
                                collapsed:
                                    controller.toggleNode(
                                        args[1]
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
                        ...context.config?.phylogeny,
                        label:
                            `Phylogeny for ${collection}`
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
        PhylogenyController,
        normalizeTree,
        normalizeRecords,
        rankIndex,
        mount,
        render,
        initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalPhylogeny =
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
