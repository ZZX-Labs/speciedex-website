/*
========================================================================
Speciedex.org
Terminal HexMap Visualization
========================================================================

Canvas-based hexagonal spatial aggregation and exploration renderer for
Speciedex records. Supports geographic latitude/longitude data, projected
coordinates, arbitrary numeric x/y fields, density binning, weighted metrics,
selection, hover inspection, zoom, pan, responsive rendering, export, and
runtime updates.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "HexMap";
    const DEFAULT_WIDTH = 960;
    const DEFAULT_HEIGHT = 540;
    const DEFAULT_HEX_RADIUS = 14;
    const DEFAULT_PADDING = 24;
    const DEFAULT_FOREGROUND = "#c0d674";
    const DEFAULT_BACKGROUND = "#020a05";
    const DEFAULT_GRID = "#1f3a27";
    const DEFAULT_HIGHLIGHT = "#eef7c8";
    const DEFAULT_EMPTY_TEXT = "No mappable records.";
    const MAX_RECORDS = 250000;
    const SQRT3 = Math.sqrt(3);

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
            const existing = target.querySelector("canvas");

            if (existing) {
                return existing;
            }

            const canvas = document.createElement("canvas");
            target.appendChild(canvas);
            return canvas;
        }

        throw new TypeError(
            "HexMap requires a canvas or container element."
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
            return data.slice(0, MAX_RECORDS);
        }

        if (isObject(data)) {
            for (const key of [
                "records",
                "results",
                "items",
                "features",
                "data"
            ]) {
                if (Array.isArray(data[key])) {
                    return data[key].slice(0, MAX_RECORDS);
                }
            }

            return [data];
        }

        return [data];
    }

    function firstFinite(record, keys) {
        for (const key of keys) {
            const value = Number(record?.[key]);

            if (Number.isFinite(value)) {
                return value;
            }
        }

        return null;
    }

    function extractCoordinates(record, options = {}) {
        if (!isObject(record)) {
            return null;
        }

        const xKey = options.xKey || null;
        const yKey = options.yKey || null;

        if (xKey && yKey) {
            const x = Number(record[xKey]);
            const y = Number(record[yKey]);

            if (Number.isFinite(x) && Number.isFinite(y)) {
                return {
                    x,
                    y,
                    source: "explicit"
                };
            }
        }

        if (
            isObject(record.geometry) &&
            Array.isArray(record.geometry.coordinates) &&
            record.geometry.coordinates.length >= 2
        ) {
            const x = Number(record.geometry.coordinates[0]);
            const y = Number(record.geometry.coordinates[1]);

            if (Number.isFinite(x) && Number.isFinite(y)) {
                return {
                    x,
                    y,
                    source: "geometry"
                };
            }
        }

        const longitude = firstFinite(record, [
            "longitude",
            "lon",
            "lng",
            "decimalLongitude",
            "decimal_longitude",
            "x"
        ]);
        const latitude = firstFinite(record, [
            "latitude",
            "lat",
            "decimalLatitude",
            "decimal_latitude",
            "y"
        ]);

        if (
            longitude !== null &&
            latitude !== null
        ) {
            return {
                x: longitude,
                y: latitude,
                source: "geographic"
            };
        }

        return null;
    }

    function extractWeight(record, options = {}) {
        if (!isObject(record)) {
            return 1;
        }

        if (typeof options.weight === "function") {
            const value = Number(options.weight(record));

            return Number.isFinite(value) ? value : 1;
        }

        const key = options.weightKey;

        if (key) {
            const value = Number(record[key]);

            if (Number.isFinite(value)) {
                return value;
            }
        }

        for (const candidate of [
            "weight",
            "count",
            "value",
            "abundance",
            "occurrenceCount",
            "occurrence_count"
        ]) {
            const value = Number(record[candidate]);

            if (Number.isFinite(value)) {
                return value;
            }
        }

        return 1;
    }

    function labelForRecord(record, index) {
        if (!isObject(record)) {
            return String(record ?? `Record ${index + 1}`);
        }

        return String(
            record.scientific_name ??
            record.scientificName ??
            record.common_name ??
            record.commonName ??
            record.name ??
            record.label ??
            record.id ??
            `Record ${index + 1}`
        );
    }

    function projectGeographic(longitude, latitude) {
        const clampedLatitude = Math.max(
            -85.05112878,
            Math.min(85.05112878, latitude)
        );
        const x = (longitude + 180) / 360;
        const sinLatitude = Math.sin(
            clampedLatitude * Math.PI / 180
        );
        const y =
            0.5 -
            Math.log(
                (1 + sinLatitude) /
                (1 - sinLatitude)
            ) /
            (4 * Math.PI);

        return { x, y };
    }

    function axialToPixel(q, r, radius) {
        return {
            x: radius * SQRT3 * (q + r / 2),
            y: radius * 1.5 * r
        };
    }

    function pixelToAxial(x, y, radius) {
        return {
            q: (SQRT3 / 3 * x - 1 / 3 * y) / radius,
            r: (2 / 3 * y) / radius
        };
    }

    function cubeRound(q, r) {
        const x = q;
        const z = r;
        const y = -x - z;
        let rx = Math.round(x);
        let ry = Math.round(y);
        let rz = Math.round(z);

        const xDifference = Math.abs(rx - x);
        const yDifference = Math.abs(ry - y);
        const zDifference = Math.abs(rz - z);

        if (
            xDifference > yDifference &&
            xDifference > zDifference
        ) {
            rx = -ry - rz;
        } else if (yDifference > zDifference) {
            ry = -rx - rz;
        } else {
            rz = -rx - ry;
        }

        return {
            q: rx,
            r: rz
        };
    }

    function colorToRgb(color) {
        const value = String(color || "").trim();

        if (/^#[0-9a-f]{3}$/i.test(value)) {
            return {
                r: parseInt(value[1] + value[1], 16),
                g: parseInt(value[2] + value[2], 16),
                b: parseInt(value[3] + value[3], 16)
            };
        }

        if (/^#[0-9a-f]{6}$/i.test(value)) {
            return {
                r: parseInt(value.slice(1, 3), 16),
                g: parseInt(value.slice(3, 5), 16),
                b: parseInt(value.slice(5, 7), 16)
            };
        }

        return null;
    }

    function interpolateColor(start, end, ratio, alpha = 1) {
        const left = colorToRgb(start);
        const right = colorToRgb(end);

        if (!left || !right) {
            return end;
        }

        const amount = Math.max(0, Math.min(1, ratio));
        const r = Math.round(left.r + (right.r - left.r) * amount);
        const g = Math.round(left.g + (right.g - left.g) * amount);
        const b = Math.round(left.b + (right.b - left.b) * amount);

        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    class HexMapController extends EventTarget {
        constructor(target, data = [], options = {}) {
            super();

            this.canvas = resolveCanvas(target);
            this.context = this.canvas.getContext("2d", {
                alpha: true,
                desynchronized: true
            });

            if (!this.context) {
                throw new Error(
                    "Unable to acquire HexMap 2D canvas context."
                );
            }

            this.options = {
                xKey: options.xKey || null,
                yKey: options.yKey || null,
                weightKey: options.weightKey || null,
                weight: options.weight,
                geographic: options.geographic !== false,
                hexRadius: parseNumber(
                    options.hexRadius,
                    DEFAULT_HEX_RADIUS,
                    3,
                    100
                ),
                padding: parseNumber(
                    options.padding,
                    DEFAULT_PADDING,
                    0,
                    500
                ),
                foreground:
                    options.foreground ||
                    DEFAULT_FOREGROUND,
                background:
                    options.background ||
                    DEFAULT_BACKGROUND,
                gridColor:
                    options.gridColor ||
                    DEFAULT_GRID,
                highlight:
                    options.highlight ||
                    DEFAULT_HIGHLIGHT,
                minOpacity: parseNumber(
                    options.minOpacity,
                    0.08,
                    0,
                    1
                ),
                maxOpacity: parseNumber(
                    options.maxOpacity,
                    0.92,
                    0,
                    1
                ),
                showGrid:
                    options.showGrid !== false,
                showLabels:
                    options.showLabels === true,
                showLegend:
                    options.showLegend !== false,
                interactive:
                    options.interactive !== false,
                zoomable:
                    options.zoomable !== false,
                pannable:
                    options.pannable !== false,
                minZoom: parseNumber(
                    options.minZoom,
                    0.5,
                    0.05,
                    100
                ),
                maxZoom: parseNumber(
                    options.maxZoom,
                    12,
                    0.1,
                    100
                ),
                aggregation:
                    options.aggregation === "average"
                        ? "average"
                        : options.aggregation === "max"
                            ? "max"
                            : "sum",
                label:
                    options.label ||
                    "HexMap visualization"
            };

            this.records = [];
            this.points = [];
            this.bins = [];
            this.binIndex = new Map();
            this.hovered = null;
            this.selected = null;
            this.destroyed = false;
            this.lastError = null;
            this.transform = {
                zoom: 1,
                x: 0,
                y: 0
            };
            this.bounds = {
                minX: 0,
                maxX: 1,
                minY: 0,
                maxY: 1
            };
            this.layout = {
                width: 1,
                height: 1,
                plotX: 0,
                plotY: 0,
                plotWidth: 1,
                plotHeight: 1,
                scaleX: 1,
                scaleY: 1
            };
            this.drag = null;
            this.metrics = {
                inputRecords: 0,
                mappedRecords: 0,
                rejectedRecords: 0,
                bins: 0,
                draws: 0,
                resizes: 0,
                zooms: 0,
                pans: 0,
                selections: 0,
                errors: 0
            };

            this._boundPointerMove = this._handlePointerMove.bind(this);
            this._boundPointerLeave = this._handlePointerLeave.bind(this);
            this._boundPointerDown = this._handlePointerDown.bind(this);
            this._boundPointerUp = this._handlePointerUp.bind(this);
            this._boundWheel = this._handleWheel.bind(this);
            this._boundClick = this._handleClick.bind(this);
            this._boundKeydown = this._handleKeydown.bind(this);

            this._cleanupResize = createResizeObserver(
                this.canvas,
                () => this.resize()
            );

            if (this.options.interactive) {
                this.canvas.tabIndex = this.canvas.tabIndex >= 0
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
                    "click",
                    this._boundClick
                );
                this.canvas.addEventListener(
                    "keydown",
                    this._boundKeydown
                );
                this.canvas.addEventListener(
                    "wheel",
                    this._boundWheel,
                    { passive: false }
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

            const rectangle = this.canvas.getBoundingClientRect();
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

            this.layout.width = rectangle.width || DEFAULT_WIDTH;
            this.layout.height = rectangle.height || DEFAULT_HEIGHT;
            this.layout.plotX = this.options.padding;
            this.layout.plotY = this.options.padding;
            this.layout.plotWidth = Math.max(
                1,
                this.layout.width - this.options.padding * 2
            );
            this.layout.plotHeight = Math.max(
                1,
                this.layout.height - this.options.padding * 2
            );

            this.metrics.resizes += 1;
            this.rebuild();
            this.draw();

            this._emit("resize", {
                width: this.layout.width,
                height: this.layout.height
            });
        }

        setData(data) {
            this.records = normalizeRecords(data);
            this.metrics.inputRecords = this.records.length;
            this.rebuild();
            this.draw();

            this._emit("data", {
                records: this.records.length,
                points: this.points.length,
                bins: this.bins.length
            });

            return this;
        }

        append(data) {
            const records = normalizeRecords(data);

            this.records.push(...records);

            if (this.records.length > MAX_RECORDS) {
                this.records.splice(
                    0,
                    this.records.length - MAX_RECORDS
                );
            }

            this.metrics.inputRecords = this.records.length;
            this.rebuild();
            this.draw();

            this._emit("append", {
                added: records.length,
                records: this.records.length
            });

            return records.length;
        }

        rebuild() {
            this.points = [];
            this.binIndex.clear();
            this.bins = [];

            let minX = Infinity;
            let maxX = -Infinity;
            let minY = Infinity;
            let maxY = -Infinity;
            let rejected = 0;

            this.records.forEach((record, index) => {
                const coordinates =
                    extractCoordinates(record, this.options);

                if (!coordinates) {
                    rejected += 1;
                    return;
                }

                let x = coordinates.x;
                let y = coordinates.y;
                const geographic =
                    coordinates.source === "geographic" ||
                    coordinates.source === "geometry";

                if (
                    this.options.geographic &&
                    geographic
                ) {
                    const projected = projectGeographic(x, y);
                    x = projected.x;
                    y = projected.y;
                }

                const point = {
                    x,
                    y,
                    rawX: coordinates.x,
                    rawY: coordinates.y,
                    weight: extractWeight(record, this.options),
                    label: labelForRecord(record, index),
                    record,
                    index,
                    geographic
                };

                this.points.push(point);
                minX = Math.min(minX, x);
                maxX = Math.max(maxX, x);
                minY = Math.min(minY, y);
                maxY = Math.max(maxY, y);
            });

            if (!this.points.length) {
                this.bounds = {
                    minX: 0,
                    maxX: 1,
                    minY: 0,
                    maxY: 1
                };
                this.metrics.mappedRecords = 0;
                this.metrics.rejectedRecords = rejected;
                this.metrics.bins = 0;
                return;
            }

            if (minX === maxX) {
                minX -= 0.5;
                maxX += 0.5;
            }

            if (minY === maxY) {
                minY -= 0.5;
                maxY += 0.5;
            }

            this.bounds = {
                minX,
                maxX,
                minY,
                maxY
            };

            const radius = this.options.hexRadius;
            const width = this.layout.plotWidth;
            const height = this.layout.plotHeight;

            for (const point of this.points) {
                const normalizedX =
                    (point.x - minX) /
                    (maxX - minX);
                const normalizedY =
                    (point.y - minY) /
                    (maxY - minY);
                const pixelX = normalizedX * width;
                const pixelY =
                    (1 - normalizedY) * height;
                const axial = pixelToAxial(
                    pixelX,
                    pixelY,
                    radius
                );
                const rounded = cubeRound(
                    axial.q,
                    axial.r
                );
                const key = `${rounded.q}:${rounded.r}`;

                if (!this.binIndex.has(key)) {
                    this.binIndex.set(key, {
                        key,
                        q: rounded.q,
                        r: rounded.r,
                        records: [],
                        points: [],
                        count: 0,
                        weight: 0,
                        minWeight: Infinity,
                        maxWeight: -Infinity,
                        averageWeight: 0
                    });
                }

                const bin = this.binIndex.get(key);
                bin.records.push(point.record);
                bin.points.push(point);
                bin.count += 1;
                bin.weight += point.weight;
                bin.minWeight = Math.min(
                    bin.minWeight,
                    point.weight
                );
                bin.maxWeight = Math.max(
                    bin.maxWeight,
                    point.weight
                );
            }

            this.bins = Array.from(
                this.binIndex.values()
            );

            for (const bin of this.bins) {
                bin.averageWeight =
                    bin.count
                        ? bin.weight / bin.count
                        : 0;
                const pixel = axialToPixel(
                    bin.q,
                    bin.r,
                    radius
                );
                bin.baseX =
                    this.layout.plotX + pixel.x;
                bin.baseY =
                    this.layout.plotY + pixel.y;
                bin.value =
                    this.options.aggregation === "average"
                        ? bin.averageWeight
                        : this.options.aggregation === "max"
                            ? bin.maxWeight
                            : bin.weight;
            }

            this.metrics.mappedRecords = this.points.length;
            this.metrics.rejectedRecords = rejected;
            this.metrics.bins = this.bins.length;
        }

        _screenPoint(x, y) {
            const centerX = this.layout.width / 2;
            const centerY = this.layout.height / 2;

            return {
                x:
                    centerX +
                    (x - centerX) *
                    this.transform.zoom +
                    this.transform.x,
                y:
                    centerY +
                    (y - centerY) *
                    this.transform.zoom +
                    this.transform.y
            };
        }

        _inverseScreenPoint(x, y) {
            const centerX = this.layout.width / 2;
            const centerY = this.layout.height / 2;

            return {
                x:
                    centerX +
                    (x - centerX - this.transform.x) /
                    this.transform.zoom,
                y:
                    centerY +
                    (y - centerY - this.transform.y) /
                    this.transform.zoom
            };
        }

        _hexPath(x, y, radius) {
            const path = new Path2D();

            for (let side = 0; side < 6; side += 1) {
                const angle =
                    Math.PI / 180 *
                    (60 * side - 30);
                const pointX =
                    x + radius * Math.cos(angle);
                const pointY =
                    y + radius * Math.sin(angle);

                if (side === 0) {
                    path.moveTo(pointX, pointY);
                } else {
                    path.lineTo(pointX, pointY);
                }
            }

            path.closePath();
            return path;
        }

        _drawBackground() {
            this.context.clearRect(
                0,
                0,
                this.layout.width,
                this.layout.height
            );
            this.context.fillStyle =
                this.options.background;
            this.context.fillRect(
                0,
                0,
                this.layout.width,
                this.layout.height
            );
        }

        _valueRange() {
            if (!this.bins.length) {
                return {
                    minimum: 0,
                    maximum: 1
                };
            }

            const values = this.bins.map(
                (bin) => bin.value
            );
            const minimum = Math.min(...values);
            const maximum = Math.max(...values);

            return {
                minimum,
                maximum:
                    maximum === minimum
                        ? minimum + 1
                        : maximum
            };
        }

        draw() {
            if (this.destroyed) {
                return;
            }

            this._drawBackground();

            if (!this.bins.length) {
                this.context.fillStyle =
                    this.options.foreground;
                this.context.globalAlpha = 0.72;
                this.context.font =
                    '14px "IBM Plex Mono", monospace';
                this.context.textAlign = "center";
                this.context.textBaseline = "middle";
                this.context.fillText(
                    DEFAULT_EMPTY_TEXT,
                    this.layout.width / 2,
                    this.layout.height / 2
                );
                this.context.globalAlpha = 1;
                this.metrics.draws += 1;
                return;
            }

            const range = this._valueRange();
            const radius =
                this.options.hexRadius *
                this.transform.zoom;

            for (const bin of this.bins) {
                const screen = this._screenPoint(
                    bin.baseX,
                    bin.baseY
                );
                const normalized =
                    (bin.value - range.minimum) /
                    (range.maximum - range.minimum);
                const alpha =
                    this.options.minOpacity +
                    normalized *
                    (
                        this.options.maxOpacity -
                        this.options.minOpacity
                    );
                const hovered =
                    this.hovered?.key === bin.key;
                const selected =
                    this.selected?.key === bin.key;
                const path = this._hexPath(
                    screen.x,
                    screen.y,
                    radius
                );

                this.context.fillStyle =
                    hovered || selected
                        ? this.options.highlight
                        : interpolateColor(
                            this.options.gridColor,
                            this.options.foreground,
                            normalized,
                            alpha
                        );
                this.context.globalAlpha =
                    hovered || selected
                        ? 0.96
                        : alpha;
                this.context.fill(path);

                if (this.options.showGrid) {
                    this.context.strokeStyle =
                        hovered || selected
                            ? this.options.highlight
                            : this.options.gridColor;
                    this.context.globalAlpha =
                        hovered || selected
                            ? 1
                            : 0.55;
                    this.context.lineWidth =
                        hovered || selected
                            ? 2
                            : 1;
                    this.context.stroke(path);
                }

                if (
                    this.options.showLabels &&
                    radius >= 10
                ) {
                    this.context.globalAlpha =
                        hovered || selected
                            ? 1
                            : 0.8;
                    this.context.fillStyle =
                        this.options.background;
                    this.context.font =
                        `${Math.max(8, radius * 0.62)}px "IBM Plex Mono", monospace`;
                    this.context.textAlign = "center";
                    this.context.textBaseline = "middle";
                    this.context.fillText(
                        String(bin.count),
                        screen.x,
                        screen.y
                    );
                }

                bin.screenX = screen.x;
                bin.screenY = screen.y;
                bin.screenRadius = radius;
                bin.path = path;
            }

            this.context.globalAlpha = 1;
            this.context.lineWidth = 1;

            if (this.options.showLegend) {
                this._drawLegend(range);
            }

            this.metrics.draws += 1;
        }

        _drawLegend(range) {
            const width = 160;
            const height = 10;
            const x =
                this.layout.width -
                this.options.padding -
                width;
            const y =
                this.layout.height -
                this.options.padding -
                28;
            const gradient =
                this.context.createLinearGradient(
                    x,
                    0,
                    x + width,
                    0
                );

            gradient.addColorStop(
                0,
                this.options.gridColor
            );
            gradient.addColorStop(
                1,
                this.options.foreground
            );

            this.context.fillStyle = gradient;
            this.context.globalAlpha = 0.85;
            this.context.fillRect(
                x,
                y,
                width,
                height
            );
            this.context.strokeStyle =
                this.options.gridColor;
            this.context.globalAlpha = 1;
            this.context.strokeRect(
                x,
                y,
                width,
                height
            );

            this.context.fillStyle =
                this.options.foreground;
            this.context.font =
                '10px "IBM Plex Mono", monospace';
            this.context.textBaseline = "top";
            this.context.textAlign = "left";
            this.context.fillText(
                String(
                    Number(range.minimum.toFixed(2))
                ),
                x,
                y + 14
            );
            this.context.textAlign = "right";
            this.context.fillText(
                String(
                    Number(range.maximum.toFixed(2))
                ),
                x + width,
                y + 14
            );
            this.context.textAlign = "left";
        }

        _pointFromEvent(event) {
            const rectangle =
                this.canvas.getBoundingClientRect();

            return {
                x: event.clientX - rectangle.left,
                y: event.clientY - rectangle.top
            };
        }

        hitTest(x, y) {
            for (
                let index = this.bins.length - 1;
                index >= 0;
                index -= 1
            ) {
                const bin = this.bins[index];

                if (
                    bin.path &&
                    this.context.isPointInPath(
                        bin.path,
                        x,
                        y
                    )
                ) {
                    return bin;
                }
            }

            return null;
        }

        _handlePointerMove(event) {
            const point = this._pointFromEvent(event);

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

            const hovered = this.hitTest(
                point.x,
                point.y
            );
            const changed =
                hovered?.key !== this.hovered?.key;

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
                    bin: hovered
                        ? this.describeBin(hovered)
                        : null
                });
            }
        }

        _handlePointerLeave() {
            if (this.drag) {
                this.drag = null;
            }

            if (this.hovered) {
                this.hovered = null;
                this.draw();
                this._emit("hover", {
                    bin: null
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

            const point = this._pointFromEvent(event);

            this.drag = {
                startX: point.x,
                startY: point.y,
                originX: this.transform.x,
                originY: this.transform.y
            };

            this.canvas.setPointerCapture?.(
                event.pointerId
            );
            this.canvas.style.cursor = "grabbing";
        }

        _handlePointerUp(event) {
            if (!this.drag) {
                return;
            }

            this.canvas.releasePointerCapture?.(
                event.pointerId
            );
            this.drag = null;
            this.canvas.style.cursor =
                this.options.pannable
                    ? "grab"
                    : "default";

            this._emit("pan", {
                transform: clone(this.transform)
            });
        }

        _handleWheel(event) {
            if (!this.options.zoomable) {
                return;
            }

            event.preventDefault();

            const point = this._pointFromEvent(event);
            const before = this._inverseScreenPoint(
                point.x,
                point.y
            );
            const factor =
                event.deltaY < 0
                    ? 1.12
                    : 1 / 1.12;
            const zoom = Math.max(
                this.options.minZoom,
                Math.min(
                    this.options.maxZoom,
                    this.transform.zoom * factor
                )
            );
            const centerX = this.layout.width / 2;
            const centerY = this.layout.height / 2;

            this.transform.zoom = zoom;
            this.transform.x =
                point.x -
                centerX -
                (before.x - centerX) * zoom;
            this.transform.y =
                point.y -
                centerY -
                (before.y - centerY) * zoom;
            this.metrics.zooms += 1;
            this.draw();

            this._emit("zoom", {
                zoom,
                transform: clone(this.transform)
            });
        }

        _handleClick(event) {
            if (this.drag) {
                return;
            }

            const point = this._pointFromEvent(event);
            const bin = this.hitTest(
                point.x,
                point.y
            );

            this.selected =
                bin?.key === this.selected?.key
                    ? null
                    : bin;
            this.metrics.selections += 1;
            this.draw();

            this._emit("select", {
                bin: this.selected
                    ? this.describeBin(this.selected)
                    : null
            });
        }

        _handleKeydown(event) {
            if (event.key === "+" || event.key === "=") {
                event.preventDefault();
                this.setZoom(
                    this.transform.zoom * 1.2
                );
            } else if (event.key === "-") {
                event.preventDefault();
                this.setZoom(
                    this.transform.zoom / 1.2
                );
            } else if (event.key === "0") {
                event.preventDefault();
                this.resetView();
            } else if (event.key === "Escape") {
                this.selected = null;
                this.draw();
            } else if (event.key === "ArrowLeft") {
                event.preventDefault();
                this.panBy(24, 0);
            } else if (event.key === "ArrowRight") {
                event.preventDefault();
                this.panBy(-24, 0);
            } else if (event.key === "ArrowUp") {
                event.preventDefault();
                this.panBy(0, 24);
            } else if (event.key === "ArrowDown") {
                event.preventDefault();
                this.panBy(0, -24);
            }
        }

        setZoom(value) {
            this.transform.zoom = Math.max(
                this.options.minZoom,
                Math.min(
                    this.options.maxZoom,
                    parseNumber(
                        value,
                        this.transform.zoom
                    )
                )
            );
            this.draw();

            this._emit("zoom", {
                zoom: this.transform.zoom,
                transform: clone(this.transform)
            });

            return this.transform.zoom;
        }

        panBy(x, y) {
            this.transform.x += Number(x) || 0;
            this.transform.y += Number(y) || 0;
            this.metrics.pans += 1;
            this.draw();

            this._emit("pan", {
                transform: clone(this.transform)
            });

            return clone(this.transform);
        }

        resetView() {
            this.transform = {
                zoom: 1,
                x: 0,
                y: 0
            };
            this.draw();

            this._emit("resetView", {
                transform: clone(this.transform)
            });

            return clone(this.transform);
        }

        describeBin(bin) {
            if (!bin) {
                return null;
            }

            return {
                key: bin.key,
                q: bin.q,
                r: bin.r,
                count: bin.count,
                weight: bin.weight,
                averageWeight: bin.averageWeight,
                minWeight: bin.minWeight,
                maxWeight: bin.maxWeight,
                value: bin.value,
                records: bin.records.map(clone),
                labels: bin.points
                    .slice(0, 20)
                    .map((point) => point.label)
            };
        }

        selectBin(key) {
            const bin = this.binIndex.get(String(key));

            if (!bin) {
                return null;
            }

            this.selected = bin;
            this.draw();

            return this.describeBin(bin);
        }

        getSelected() {
            return this.selected
                ? this.describeBin(this.selected)
                : null;
        }

        update(options = {}) {
            if (!isObject(options)) {
                throw new TypeError(
                    "HexMap options must be an object."
                );
            }

            const rebuildRequired = [
                "xKey",
                "yKey",
                "weightKey",
                "weight",
                "geographic",
                "hexRadius",
                "aggregation"
            ].some((key) =>
                options[key] !== undefined
            );

            Object.assign(this.options, {
                xKey:
                    options.xKey !== undefined
                        ? options.xKey
                        : this.options.xKey,
                yKey:
                    options.yKey !== undefined
                        ? options.yKey
                        : this.options.yKey,
                weightKey:
                    options.weightKey !== undefined
                        ? options.weightKey
                        : this.options.weightKey,
                weight:
                    options.weight !== undefined
                        ? options.weight
                        : this.options.weight,
                geographic:
                    options.geographic !== undefined
                        ? Boolean(options.geographic)
                        : this.options.geographic,
                hexRadius:
                    options.hexRadius !== undefined
                        ? parseNumber(
                            options.hexRadius,
                            this.options.hexRadius,
                            3,
                            100
                        )
                        : this.options.hexRadius,
                padding:
                    options.padding !== undefined
                        ? parseNumber(
                            options.padding,
                            this.options.padding,
                            0,
                            500
                        )
                        : this.options.padding,
                foreground:
                    options.foreground ||
                    this.options.foreground,
                background:
                    options.background ||
                    this.options.background,
                gridColor:
                    options.gridColor ||
                    this.options.gridColor,
                highlight:
                    options.highlight ||
                    this.options.highlight,
                minOpacity:
                    options.minOpacity !== undefined
                        ? parseNumber(
                            options.minOpacity,
                            this.options.minOpacity,
                            0,
                            1
                        )
                        : this.options.minOpacity,
                maxOpacity:
                    options.maxOpacity !== undefined
                        ? parseNumber(
                            options.maxOpacity,
                            this.options.maxOpacity,
                            0,
                            1
                        )
                        : this.options.maxOpacity,
                showGrid:
                    options.showGrid !== undefined
                        ? Boolean(options.showGrid)
                        : this.options.showGrid,
                showLabels:
                    options.showLabels !== undefined
                        ? Boolean(options.showLabels)
                        : this.options.showLabels,
                showLegend:
                    options.showLegend !== undefined
                        ? Boolean(options.showLegend)
                        : this.options.showLegend,
                aggregation:
                    options.aggregation ||
                    this.options.aggregation
            });

            if (rebuildRequired) {
                this.rebuild();
            }

            this.draw();

            this._emit("update", {
                options: clone(this.options)
            });

            return this;
        }

        export(format = "json") {
            const normalized = String(format).toLowerCase();

            if (normalized === "png") {
                return this.canvas.toDataURL("image/png");
            }

            if (normalized === "json") {
                return JSON.stringify(
                    {
                        generatedAt: iso(),
                        options: this.options,
                        transform: this.transform,
                        bounds: this.bounds,
                        bins: this.bins.map((bin) =>
                            this.describeBin(bin)
                        )
                    },
                    null,
                    2
                );
            }

            if (normalized === "csv") {
                const rows = [[
                    "key",
                    "q",
                    "r",
                    "count",
                    "weight",
                    "averageWeight",
                    "minWeight",
                    "maxWeight",
                    "value"
                ]];

                for (const bin of this.bins) {
                    rows.push([
                        bin.key,
                        bin.q,
                        bin.r,
                        bin.count,
                        bin.weight,
                        bin.averageWeight,
                        bin.minWeight,
                        bin.maxWeight,
                        bin.value
                    ]);
                }

                return rows
                    .map((row) =>
                        row.map((value) => {
                            const text = String(value ?? "");

                            return /[",\n\r]/.test(text)
                                ? `"${text.replace(/"/g, '""')}"`
                                : text;
                        }).join(",")
                    )
                    .join("\r\n");
            }

            throw new Error(
                `Unsupported HexMap export format: ${format}`
            );
        }

        status() {
            return {
                name: "hexmap",
                module: MODULE_NAME,
                records: this.records.length,
                points: this.points.length,
                bins: this.bins.length,
                bounds: clone(this.bounds),
                transform: clone(this.transform),
                selected: this.getSelected(),
                hovered: this.hovered
                    ? this.describeBin(this.hovered)
                    : null,
                options: clone(this.options),
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
                    "click",
                    this._boundClick
                );
                this.canvas.removeEventListener(
                    "keydown",
                    this._boundKeydown
                );
                this.canvas.removeEventListener(
                    "wheel",
                    this._boundWheel
                );
            }

            this.records = [];
            this.points = [];
            this.bins = [];
            this.binIndex.clear();
            this.destroyed = true;

            this._emit("destroy", {});
            return true;
        }
    }

    function mount(target, data = [], options = {}) {
        if (isObject(data) && !Array.isArray(data) && options === undefined) {
            options = data;
            data = [];
        }

        return new HexMapController(
            target,
            data,
            options
        );
    }

    function render(data = [], options = {}) {
        const container =
            createElement(
                "section",
                "terminal-visualization terminal-visualization-hexmap"
            );

        container.dataset.visualization = "hexmap";
        container.setAttribute(
            "role",
            "region"
        );
        container.setAttribute(
            "aria-label",
            options.label ||
            "HexMap visualization"
        );

        const canvas =
            document.createElement("canvas");
        canvas.className =
            "terminal-hexmap-canvas";
        canvas.width =
            Number(options.width) ||
            DEFAULT_WIDTH;
        canvas.height =
            Number(options.height) ||
            DEFAULT_HEIGHT;
        canvas.setAttribute(
            "aria-label",
            options.label ||
            "HexMap visualization"
        );

        const status =
            createElement(
                "div",
                "terminal-hexmap-status"
            );
        status.setAttribute(
            "aria-live",
            "polite"
        );

        const tooltip =
            createElement(
                "div",
                "terminal-hexmap-tooltip"
            );
        tooltip.hidden = true;

        container.append(
            canvas,
            status,
            tooltip
        );

        const controller =
            new HexMapController(
                canvas,
                data,
                options
            );

        const updateStatus = () => {
            const snapshot =
                controller.status();

            status.textContent =
                `${snapshot.points} mapped record` +
                `${snapshot.points === 1 ? "" : "s"} · ` +
                `${snapshot.bins} hex bin` +
                `${snapshot.bins === 1 ? "" : "s"} · ` +
                `${snapshot.transform.zoom.toFixed(2)}×`;
        };

        controller.addEventListener(
            "hover",
            (event) => {
                const bin = event.detail?.bin;

                if (!bin) {
                    tooltip.hidden = true;
                    return;
                }

                tooltip.hidden = false;
                tooltip.textContent =
                    `${bin.count} record${bin.count === 1 ? "" : "s"} · ` +
                    `value ${Number(bin.value.toFixed(3))}`;
            }
        );

        for (const eventName of [
            "data",
            "append",
            "resize",
            "zoom",
            "pan",
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
            controller.records;
        container.destroy = () =>
            controller.destroy();

        return container;
    }

    function initialize(context = {}) {
        const dataset =
            context.root?.dataset || {};
        const config =
            context.config?.hexmap || {};

        const defaults = {
            xKey:
                dataset.terminalHexmapXKey ||
                config.xKey ||
                null,

            yKey:
                dataset.terminalHexmapYKey ||
                config.yKey ||
                null,

            weightKey:
                dataset.terminalHexmapWeightKey ||
                config.weightKey ||
                null,

            geographic: parseBoolean(
                dataset.terminalHexmapGeographic,
                config.geographic !== false
            ),

            hexRadius:
                dataset.terminalHexmapRadius ||
                config.hexRadius ||
                DEFAULT_HEX_RADIUS,

            foreground:
                dataset.terminalHexmapForeground ||
                config.foreground ||
                DEFAULT_FOREGROUND,

            background:
                dataset.terminalHexmapBackground ||
                config.background ||
                DEFAULT_BACKGROUND,

            gridColor:
                dataset.terminalHexmapGrid ||
                config.gridColor ||
                DEFAULT_GRID,

            highlight:
                dataset.terminalHexmapHighlight ||
                config.highlight ||
                DEFAULT_HIGHLIGHT,

            showGrid: parseBoolean(
                dataset.terminalHexmapShowGrid,
                config.showGrid !== false
            ),

            showLabels: parseBoolean(
                dataset.terminalHexmapShowLabels,
                config.showLabels === true
            ),

            showLegend: parseBoolean(
                dataset.terminalHexmapShowLegend,
                config.showLegend !== false
            ),

            interactive: parseBoolean(
                dataset.terminalHexmapInteractive,
                config.interactive !== false
            )
        };

        const visualization = {
            mount(target, data = [], options = {}) {
                return new HexMapController(
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
                HexMapController,

            normalizeRecords,

            extractCoordinates
        };

        context.registerVisualization?.(
            "hexmap",
            visualization
        );

        context.registerRenderer?.(
            "hexmap",
            visualization
        );

        context.hexmap =
            visualization;

        safeDispatch(
            document,
            "speciedex:terminal-hexmap-ready",
            {
                visualization
            }
        );

        return visualization;
    }

    const commands = [{
        name: "hexmap",
        category: "visualization",
        description:
            "Render and control a hexagonal spatial aggregation map.",
        usage:
            "hexmap [collection|status|zoom|pan|reset|export] [arguments]",
        handler: ({
            args = [],
            context,
            writeJSON,
            write,
            writeError
        }) => {
            const action =
                String(
                    args[0] || "records"
                );
            const lower =
                action.toLowerCase();
            const controller =
                context.hexmapController ||
                context.terminalHexmapController;

            try {
                if (controller) {
                    switch (lower) {
                        case "status":
                        case "show":
                        case "info":
                            return writeJSON(
                                controller.status()
                            );

                        case "zoom":
                            if (args[1] === undefined) {
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
                                    args[1] || "json"
                                ),
                                "data"
                            );

                        default:
                            break;
                    }
                }

                const collection = action;
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
                        ...context.config?.hexmap,
                        label:
                            `HexMap for ${collection}`
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
        HexMapController,
        normalizeRecords,
        extractCoordinates,
        projectGeographic,
        axialToPixel,
        pixelToAxial,
        cubeRound,
        mount,
        render,
        initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalHexMap =
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
