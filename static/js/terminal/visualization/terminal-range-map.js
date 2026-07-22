/*
========================================================================
Speciedex.org
Terminal RangeMap Visualization
========================================================================

Interactive geographic range renderer for Speciedex records. Supports point,
bounding-box, polygon, multipolygon, GeoJSON, and inferred occurrence ranges;
equirectangular and Mercator projections; density grids; grouping; filtering;
hover inspection; selection; pan; zoom; responsive high-DPI rendering;
JSON, CSV, GeoJSON, and PNG export; diagnostics; and lifecycle control.

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "RangeMap";
    const DEFAULT_WIDTH = 960;
    const DEFAULT_HEIGHT = 540;
    const DEFAULT_BACKGROUND = "#020a05";
    const DEFAULT_FOREGROUND = "#c0d674";
    const DEFAULT_HIGHLIGHT = "#eef7c8";
    const DEFAULT_GRID = "#1f3a27";
    const DEFAULT_LAND = "#102619";
    const DEFAULT_WATER = "#04120a";
    const DEFAULT_RANGE = "#c0d674";
    const DEFAULT_POINT = "#eef7c8";
    const DEFAULT_PADDING = 24;
    const DEFAULT_POINT_RADIUS = 3;
    const DEFAULT_GRID_SIZE = 36;
    const DEFAULT_MAX_RECORDS = 250000;
    const MAX_MERCATOR_LATITUDE = 85.05112878;

    const LATITUDE_FIELDS = Object.freeze([
        "latitude",
        "lat",
        "decimalLatitude",
        "decimal_latitude",
        "y"
    ]);

    const LONGITUDE_FIELDS = Object.freeze([
        "longitude",
        "lon",
        "lng",
        "decimalLongitude",
        "decimal_longitude",
        "x"
    ]);

    const LABEL_FIELDS = Object.freeze([
        "scientific_name",
        "scientificName",
        "canonical_name",
        "canonicalName",
        "common_name",
        "commonName",
        "name",
        "label",
        "id"
    ]);

    const GROUP_FIELDS = Object.freeze([
        "kingdom",
        "phylum",
        "class",
        "order",
        "family",
        "genus",
        "rank",
        "provider",
        "source",
        "status",
        "category"
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
            "RangeMap requires a canvas or container element."
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
            return data.slice(0, DEFAULT_MAX_RECORDS);
        }

        if (isObject(data)) {
            if (
                data.type === "FeatureCollection" &&
                Array.isArray(data.features)
            ) {
                return data.features.slice(0, DEFAULT_MAX_RECORDS);
            }

            for (const key of [
                "records",
                "results",
                "items",
                "features",
                "ranges",
                "occurrences",
                "data"
            ]) {
                if (Array.isArray(data[key])) {
                    return data[key].slice(0, DEFAULT_MAX_RECORDS);
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

    function firstFinite(record, keys, fallback = null) {
        for (const key of keys) {
            const value = Number(record?.[key]);

            if (Number.isFinite(value)) {
                return value;
            }
        }

        return fallback;
    }

    function normalizeLongitude(value) {
        let longitude = Number(value);

        if (!Number.isFinite(longitude)) {
            return null;
        }

        longitude %= 360;

        if (longitude > 180) {
            longitude -= 360;
        } else if (longitude < -180) {
            longitude += 360;
        }

        return longitude;
    }

    function clampLatitude(value) {
        const latitude = Number(value);

        if (!Number.isFinite(latitude)) {
            return null;
        }

        return Math.max(-90, Math.min(90, latitude));
    }

    function labelForRecord(record, index) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (!isObject(source)) {
            return String(source ?? `Range ${index + 1}`);
        }

        return String(
            firstValue(
                source,
                LABEL_FIELDS,
                `Range ${index + 1}`
            )
        );
    }

    function groupForRecord(record) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (!isObject(source)) {
            return "ungrouped";
        }

        return String(
            firstValue(
                source,
                GROUP_FIELDS,
                "ungrouped"
            )
        );
    }

    function idForRecord(record, index) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (!isObject(source)) {
            return `range-${index + 1}`;
        }

        return String(
            firstValue(source, [
                "speciedex_id",
                "speciedexId",
                "taxon_id",
                "taxonId",
                "id",
                "key",
                "uuid"
            ], `range-${index + 1}`)
        );
    }

    function weightForRecord(record) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (!isObject(source)) {
            return 1;
        }

        for (const key of [
            "weight",
            "count",
            "value",
            "abundance",
            "occurrenceCount",
            "occurrence_count",
            "score"
        ]) {
            const value = Number(source[key]);

            if (Number.isFinite(value)) {
                return Math.max(0.01, value);
            }
        }

        return 1;
    }

    function extractPoint(record) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (
            isObject(record?.geometry) &&
            record.geometry.type === "Point" &&
            Array.isArray(record.geometry.coordinates)
        ) {
            const longitude = normalizeLongitude(
                record.geometry.coordinates[0]
            );
            const latitude = clampLatitude(
                record.geometry.coordinates[1]
            );

            if (longitude !== null && latitude !== null) {
                return [longitude, latitude];
            }
        }

        if (!isObject(source)) {
            return null;
        }

        const longitude = normalizeLongitude(
            firstFinite(source, LONGITUDE_FIELDS, null)
        );
        const latitude = clampLatitude(
            firstFinite(source, LATITUDE_FIELDS, null)
        );

        return longitude !== null && latitude !== null
            ? [longitude, latitude]
            : null;
    }

    function extractBounds(record) {
        const source =
            record?.properties && isObject(record.properties)
                ? { ...record, ...record.properties }
                : record;

        if (!isObject(source)) {
            return null;
        }

        const west = normalizeLongitude(firstFinite(source, [
            "west",
            "minLongitude",
            "min_longitude",
            "minimumLongitude",
            "bbox_west"
        ], null));
        const east = normalizeLongitude(firstFinite(source, [
            "east",
            "maxLongitude",
            "max_longitude",
            "maximumLongitude",
            "bbox_east"
        ], null));
        const south = clampLatitude(firstFinite(source, [
            "south",
            "minLatitude",
            "min_latitude",
            "minimumLatitude",
            "bbox_south"
        ], null));
        const north = clampLatitude(firstFinite(source, [
            "north",
            "maxLatitude",
            "max_latitude",
            "maximumLatitude",
            "bbox_north"
        ], null));

        if (
            west === null ||
            east === null ||
            south === null ||
            north === null
        ) {
            return null;
        }

        return {
            west,
            east,
            south,
            north
        };
    }

    function geometryForRecord(record) {
        if (isObject(record?.geometry)) {
            return clone(record.geometry);
        }

        if (
            isObject(record) &&
            ["Point", "Polygon", "MultiPolygon"].includes(record.type)
        ) {
            return clone(record);
        }

        const bounds = extractBounds(record);

        if (bounds) {
            return {
                type: "Polygon",
                coordinates: [[
                    [bounds.west, bounds.south],
                    [bounds.east, bounds.south],
                    [bounds.east, bounds.north],
                    [bounds.west, bounds.north],
                    [bounds.west, bounds.south]
                ]]
            };
        }

        const point = extractPoint(record);

        if (point) {
            return {
                type: "Point",
                coordinates: point
            };
        }

        return null;
    }

    function colorHash(value) {
        let hash = 0;

        for (const character of String(value || "")) {
            hash = ((hash << 5) - hash) + character.charCodeAt(0);
            hash |= 0;
        }

        return `hsl(${Math.abs(hash) % 360} 55% 60%)`;
    }

    function escapeCsv(value) {
        const text = String(value ?? "");

        return /[",\n\r]/.test(text)
            ? `"${text.replace(/"/g, '""')}"`
            : text;
    }

    class RangeMapController extends EventTarget {
        constructor(target, data = [], options = {}) {
            super();

            this.canvas = resolveCanvas(target);
            this.context = this.canvas.getContext("2d", {
                alpha: true,
                desynchronized: true
            });

            if (!this.context) {
                throw new Error(
                    "Unable to acquire RangeMap 2D canvas context."
                );
            }

            this.options = {
                projection:
                    options.projection === "mercator"
                        ? "mercator"
                        : "equirectangular",
                background:
                    options.background ||
                    DEFAULT_BACKGROUND,
                foreground:
                    options.foreground ||
                    DEFAULT_FOREGROUND,
                highlight:
                    options.highlight ||
                    DEFAULT_HIGHLIGHT,
                gridColor:
                    options.gridColor ||
                    DEFAULT_GRID,
                landColor:
                    options.landColor ||
                    DEFAULT_LAND,
                waterColor:
                    options.waterColor ||
                    DEFAULT_WATER,
                rangeColor:
                    options.rangeColor ||
                    DEFAULT_RANGE,
                pointColor:
                    options.pointColor ||
                    DEFAULT_POINT,
                padding: parseNumber(
                    options.padding,
                    DEFAULT_PADDING,
                    0,
                    200
                ),
                pointRadius: parseNumber(
                    options.pointRadius,
                    DEFAULT_POINT_RADIUS,
                    1,
                    20
                ),
                gridSize: parseNumber(
                    options.gridSize,
                    DEFAULT_GRID_SIZE,
                    8,
                    256
                ),
                fillAlpha: parseNumber(
                    options.fillAlpha,
                    0.28,
                    0,
                    1
                ),
                strokeAlpha: parseNumber(
                    options.strokeAlpha,
                    0.9,
                    0,
                    1
                ),
                showGrid:
                    options.showGrid !== false,
                showPoints:
                    options.showPoints !== false,
                showRanges:
                    options.showRanges !== false,
                showDensity:
                    options.showDensity === true,
                showLabels:
                    options.showLabels === true,
                groupColors:
                    options.groupColors !== false,
                interactive:
                    options.interactive !== false,
                zoomable:
                    options.zoomable !== false,
                pannable:
                    options.pannable !== false,
                label:
                    options.label ||
                    "RangeMap visualization"
            };

            this.records = [];
            this.features = [];
            this.points = [];
            this.ranges = [];
            this.density = [];
            this.bounds = {
                width: 1,
                height: 1
            };
            this.transform = {
                zoom: 1,
                x: 0,
                y: 0
            };
            this.hovered = null;
            this.selected = null;
            this.drag = null;
            this.query = "";
            this.groupFilter = null;
            this.destroyed = false;
            this.lastError = null;
            this.metrics = {
                inputRecords: 0,
                acceptedRecords: 0,
                rejectedRecords: 0,
                points: 0,
                ranges: 0,
                densityCells: 0,
                groups: 0,
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
            this.draw();

            this._emit("resize", clone(this.bounds));
        }

        setData(data) {
            try {
                this.records = normalizeRecords(data);
                this.features = [];
                this.points = [];
                this.ranges = [];

                let accepted = 0;
                let rejected = 0;

                this.records.forEach((record, index) => {
                    const geometry =
                        geometryForRecord(record);

                    if (!geometry) {
                        rejected += 1;
                        return;
                    }

                    const feature = {
                        id:
                            idForRecord(record, index),
                        label:
                            labelForRecord(record, index),
                        group:
                            groupForRecord(record),
                        weight:
                            weightForRecord(record),
                        geometry,
                        record,
                        visible: true,
                        paths: []
                    };

                    this.features.push(feature);

                    if (geometry.type === "Point") {
                        this.points.push(feature);
                    } else if (
                        geometry.type === "Polygon" ||
                        geometry.type === "MultiPolygon"
                    ) {
                        this.ranges.push(feature);
                    }

                    accepted += 1;
                });

                this._applyFilters();
                this._buildDensity();
                this.metrics.inputRecords =
                    this.records.length;
                this.metrics.acceptedRecords =
                    accepted;
                this.metrics.rejectedRecords =
                    rejected;
                this.metrics.points =
                    this.points.length;
                this.metrics.ranges =
                    this.ranges.length;
                this.metrics.groups =
                    new Set(
                        this.features.map(
                            (feature) => feature.group
                        )
                    ).size;

                this.draw();

                this._emit("data", {
                    records:
                        this.records.length,
                    features:
                        this.features.length,
                    points:
                        this.points.length,
                    ranges:
                        this.ranges.length
                });
            } catch (error) {
                this._recordError(error);
            }

            return this;
        }

        append(data) {
            const records = normalizeRecords(data);

            this.records.push(...records);

            if (
                this.records.length >
                DEFAULT_MAX_RECORDS
            ) {
                this.records.splice(
                    0,
                    this.records.length -
                    DEFAULT_MAX_RECORDS
                );
            }

            this.setData(this.records);

            this._emit("append", {
                added: records.length
            });

            return records.length;
        }

        _applyFilters() {
            const query =
                this.query.toLowerCase();

            for (const feature of this.features) {
                feature.visible =
                    (
                        !query ||
                        feature.label
                            .toLowerCase()
                            .includes(query) ||
                        feature.group
                            .toLowerCase()
                            .includes(query) ||
                        feature.id
                            .toLowerCase()
                            .includes(query)
                    ) &&
                    (
                        !this.groupFilter ||
                        feature.group ===
                        this.groupFilter
                    );
            }
        }

        _buildDensity() {
            const size = this.options.gridSize;
            const cells = new Map();

            for (const feature of this.points) {
                if (!feature.visible) {
                    continue;
                }

                const [
                    longitude,
                    latitude
                ] = feature.geometry.coordinates;
                const x = Math.max(
                    0,
                    Math.min(
                        size - 1,
                        Math.floor(
                            (
                                longitude + 180
                            ) /
                            360 *
                            size
                        )
                    )
                );
                const y = Math.max(
                    0,
                    Math.min(
                        size - 1,
                        Math.floor(
                            (
                                90 - latitude
                            ) /
                            180 *
                            size
                        )
                    )
                );
                const key = `${x}:${y}`;

                if (!cells.has(key)) {
                    cells.set(key, {
                        x,
                        y,
                        count: 0,
                        weight: 0,
                        features: []
                    });
                }

                const cell = cells.get(key);

                cell.count += 1;
                cell.weight +=
                    feature.weight;

                if (
                    cell.features.length <
                    100
                ) {
                    cell.features.push(
                        feature
                    );
                }
            }

            const maximum = Math.max(
                ...Array.from(cells.values()).map(
                    (cell) => cell.weight
                ),
                1
            );

            this.density =
                Array.from(cells.values()).map(
                    (cell) => ({
                        ...cell,
                        normalized:
                            cell.weight /
                            maximum
                    })
                );

            this.metrics.densityCells =
                this.density.length;
        }

        _project(longitude, latitude) {
            const padding =
                this.options.padding;
            const width = Math.max(
                1,
                this.bounds.width -
                padding * 2
            );
            const height = Math.max(
                1,
                this.bounds.height -
                padding * 2
            );
            const lon =
                normalizeLongitude(longitude);
            const lat =
                clampLatitude(latitude);

            if (lon === null || lat === null) {
                return null;
            }

            let normalizedY;

            if (
                this.options.projection ===
                "mercator"
            ) {
                const limitedLatitude =
                    Math.max(
                        -MAX_MERCATOR_LATITUDE,
                        Math.min(
                            MAX_MERCATOR_LATITUDE,
                            lat
                        )
                    );
                const radians =
                    limitedLatitude *
                    Math.PI /
                    180;
                const mercator =
                    Math.log(
                        Math.tan(
                            Math.PI / 4 +
                            radians / 2
                        )
                    );
                const maximum =
                    Math.log(
                        Math.tan(
                            Math.PI / 4 +
                            MAX_MERCATOR_LATITUDE *
                            Math.PI /
                            360
                        )
                    );

                normalizedY =
                    0.5 -
                    mercator /
                    (
                        2 *
                        maximum
                    );
            } else {
                normalizedY =
                    (
                        90 - lat
                    ) /
                    180;
            }

            const baseX =
                padding +
                (
                    lon + 180
                ) /
                360 *
                width;
            const baseY =
                padding +
                normalizedY *
                height;
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;

            return {
                x:
                    centerX +
                    (
                        baseX - centerX
                    ) *
                    this.transform.zoom +
                    this.transform.x,
                y:
                    centerY +
                    (
                        baseY - centerY
                    ) *
                    this.transform.zoom +
                    this.transform.y
            };
        }

        _inverseProject(x, y) {
            const padding =
                this.options.padding;
            const width = Math.max(
                1,
                this.bounds.width -
                padding * 2
            );
            const height = Math.max(
                1,
                this.bounds.height -
                padding * 2
            );
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;
            const baseX =
                centerX +
                (
                    x -
                    centerX -
                    this.transform.x
                ) /
                this.transform.zoom;
            const baseY =
                centerY +
                (
                    y -
                    centerY -
                    this.transform.y
                ) /
                this.transform.zoom;
            const normalizedX =
                (
                    baseX - padding
                ) /
                width;
            const normalizedY =
                (
                    baseY - padding
                ) /
                height;
            const longitude =
                normalizedX *
                360 -
                180;
            let latitude;

            if (
                this.options.projection ===
                "mercator"
            ) {
                const maximum =
                    Math.log(
                        Math.tan(
                            Math.PI / 4 +
                            MAX_MERCATOR_LATITUDE *
                            Math.PI /
                            360
                        )
                    );
                const mercator =
                    (
                        0.5 -
                        normalizedY
                    ) *
                    2 *
                    maximum;

                latitude =
                    (
                        2 *
                        Math.atan(
                            Math.exp(mercator)
                        ) -
                        Math.PI / 2
                    ) *
                    180 /
                    Math.PI;
            } else {
                latitude =
                    90 -
                    normalizedY *
                    180;
            }

            return {
                longitude:
                    normalizeLongitude(
                        longitude
                    ),
                latitude:
                    clampLatitude(
                        latitude
                    )
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

            this._drawBase();

            if (this.options.showGrid) {
                this._drawGrid();
            }

            if (this.options.showDensity) {
                this._drawDensity();
            }

            if (this.options.showRanges) {
                this._drawRanges();
            }

            if (this.options.showPoints) {
                this._drawPoints();
            }

            this.metrics.draws += 1;
        }

        _drawBase() {
            this.context.save();
            this.context.fillStyle =
                this.options.waterColor;
            this.context.globalAlpha = 0.95;
            this.context.fillRect(
                0,
                0,
                this.bounds.width,
                this.bounds.height
            );

            const equatorStart =
                this._project(-180, 0);
            const equatorEnd =
                this._project(180, 0);

            if (equatorStart && equatorEnd) {
                this.context.strokeStyle =
                    this.options.landColor;
                this.context.globalAlpha = 0.18;
                this.context.lineWidth = 2;
                this.context.beginPath();
                this.context.moveTo(
                    equatorStart.x,
                    equatorStart.y
                );
                this.context.lineTo(
                    equatorEnd.x,
                    equatorEnd.y
                );
                this.context.stroke();
            }

            this.context.restore();
        }

        _drawGrid() {
            this.context.save();
            this.context.strokeStyle =
                this.options.gridColor;
            this.context.globalAlpha =
                0.45;
            this.context.lineWidth = 1;

            for (
                let longitude = -180;
                longitude <= 180;
                longitude += 15
            ) {
                let started = false;

                this.context.beginPath();

                for (
                    let latitude = -90;
                    latitude <= 90;
                    latitude += 2
                ) {
                    const point =
                        this._project(
                            longitude,
                            latitude
                        );

                    if (!point) {
                        continue;
                    }

                    if (!started) {
                        this.context.moveTo(
                            point.x,
                            point.y
                        );
                        started = true;
                    } else {
                        this.context.lineTo(
                            point.x,
                            point.y
                        );
                    }
                }

                this.context.stroke();
            }

            for (
                let latitude = -75;
                latitude <= 75;
                latitude += 15
            ) {
                let started = false;

                this.context.beginPath();

                for (
                    let longitude = -180;
                    longitude <= 180;
                    longitude += 2
                ) {
                    const point =
                        this._project(
                            longitude,
                            latitude
                        );

                    if (!point) {
                        continue;
                    }

                    if (!started) {
                        this.context.moveTo(
                            point.x,
                            point.y
                        );
                        started = true;
                    } else {
                        this.context.lineTo(
                            point.x,
                            point.y
                        );
                    }
                }

                this.context.stroke();
            }

            this.context.restore();
        }

        _drawDensity() {
            const size =
                this.options.gridSize;
            const cellLongitude =
                360 / size;
            const cellLatitude =
                180 / size;

            this.context.save();

            for (const cell of this.density) {
                const west =
                    -180 +
                    cell.x *
                    cellLongitude;
                const east =
                    west +
                    cellLongitude;
                const north =
                    90 -
                    cell.y *
                    cellLatitude;
                const south =
                    north -
                    cellLatitude;
                const topLeft =
                    this._project(
                        west,
                        north
                    );
                const bottomRight =
                    this._project(
                        east,
                        south
                    );

                if (!topLeft || !bottomRight) {
                    continue;
                }

                this.context.fillStyle =
                    this.options.rangeColor;
                this.context.globalAlpha =
                    0.08 +
                    cell.normalized *
                    0.52;
                this.context.fillRect(
                    topLeft.x,
                    topLeft.y,
                    bottomRight.x -
                    topLeft.x,
                    bottomRight.y -
                    topLeft.y
                );
            }

            this.context.restore();
        }

        _drawRanges() {
            this.context.save();

            for (const feature of this.ranges) {
                if (!feature.visible) {
                    continue;
                }

                const emphasized =
                    feature === this.hovered ||
                    feature === this.selected;
                const color =
                    emphasized
                        ? this.options.highlight
                        : this.options.groupColors
                            ? colorHash(
                                feature.group
                            )
                            : this.options.rangeColor;

                feature.paths = [];

                const polygons =
                    feature.geometry.type ===
                    "MultiPolygon"
                        ? feature.geometry.coordinates
                        : [
                            feature.geometry.coordinates
                        ];

                for (const polygon of polygons) {
                    for (const ring of polygon) {
                        const screenRing = [];
                        let started = false;

                        this.context.beginPath();

                        for (const coordinate of ring) {
                            const point =
                                this._project(
                                    coordinate[0],
                                    coordinate[1]
                                );

                            if (!point) {
                                continue;
                            }

                            screenRing.push(point);

                            if (!started) {
                                this.context.moveTo(
                                    point.x,
                                    point.y
                                );
                                started = true;
                            } else {
                                this.context.lineTo(
                                    point.x,
                                    point.y
                                );
                            }
                        }

                        if (started) {
                            this.context.closePath();
                            this.context.fillStyle =
                                color;
                            this.context.globalAlpha =
                                emphasized
                                    ? Math.min(
                                        1,
                                        this.options.fillAlpha +
                                        0.25
                                    )
                                    : this.options.fillAlpha;
                            this.context.fill();
                            this.context.strokeStyle =
                                color;
                            this.context.globalAlpha =
                                emphasized
                                    ? 1
                                    : this.options.strokeAlpha;
                            this.context.lineWidth =
                                emphasized ? 2.5 : 1.2;
                            this.context.stroke();
                            feature.paths.push(
                                screenRing
                            );
                        }
                    }
                }

                if (
                    this.options.showLabels &&
                    feature.paths.length
                ) {
                    const points =
                        feature.paths.flat();
                    const centerX =
                        points.reduce(
                            (sum, point) =>
                                sum + point.x,
                            0
                        ) /
                        points.length;
                    const centerY =
                        points.reduce(
                            (sum, point) =>
                                sum + point.y,
                            0
                        ) /
                        points.length;

                    this.context.fillStyle =
                        this.options.foreground;
                    this.context.globalAlpha =
                        0.8;
                    this.context.font =
                        '10px "IBM Plex Mono", monospace';
                    this.context.textAlign =
                        "center";
                    this.context.textBaseline =
                        "middle";
                    this.context.fillText(
                        feature.label,
                        centerX,
                        centerY
                    );
                }
            }

            this.context.restore();
        }

        _drawPoints() {
            this.context.save();

            for (const feature of this.points) {
                if (!feature.visible) {
                    continue;
                }

                const point =
                    this._project(
                        feature.geometry.coordinates[0],
                        feature.geometry.coordinates[1]
                    );

                if (!point) {
                    continue;
                }

                const emphasized =
                    feature === this.hovered ||
                    feature === this.selected;
                const radius =
                    this.options.pointRadius *
                    (
                        0.8 +
                        Math.min(
                            2.5,
                            Math.sqrt(
                                feature.weight
                            ) *
                            0.18
                        )
                    ) *
                    Math.sqrt(
                        this.transform.zoom
                    );

                this.context.beginPath();
                this.context.arc(
                    point.x,
                    point.y,
                    emphasized
                        ? radius * 1.45
                        : radius,
                    0,
                    Math.PI * 2
                );
                this.context.fillStyle =
                    emphasized
                        ? this.options.highlight
                        : this.options.groupColors
                            ? colorHash(
                                feature.group
                            )
                            : this.options.pointColor;
                this.context.globalAlpha =
                    emphasized ? 1 : 0.82;

                if (emphasized) {
                    this.context.shadowColor =
                        this.options.highlight;
                    this.context.shadowBlur = 10;
                } else {
                    this.context.shadowBlur = 0;
                }

                this.context.fill();

                feature.screenX = point.x;
                feature.screenY = point.y;
                feature.screenRadius =
                    emphasized
                        ? radius * 1.45
                        : radius;

                if (this.options.showLabels) {
                    this.context.fillStyle =
                        this.options.foreground;
                    this.context.globalAlpha =
                        0.75;
                    this.context.font =
                        '10px "IBM Plex Mono", monospace';
                    this.context.textAlign =
                        "left";
                    this.context.textBaseline =
                        "middle";
                    this.context.fillText(
                        feature.label,
                        point.x +
                        radius +
                        4,
                        point.y
                    );
                }
            }

            this.context.restore();
        }

        _pointInPolygon(x, y, polygon) {
            let inside = false;

            for (
                let left = 0,
                    right = polygon.length - 1;
                left < polygon.length;
                right = left++
            ) {
                const xi = polygon[left].x;
                const yi = polygon[left].y;
                const xj = polygon[right].x;
                const yj = polygon[right].y;

                const intersects =
                    (
                        yi > y
                    ) !== (
                        yj > y
                    ) &&
                    x <
                    (
                        xj - xi
                    ) *
                    (
                        y - yi
                    ) /
                    (
                        yj - yi ||
                        1e-9
                    ) +
                    xi;

                if (intersects) {
                    inside = !inside;
                }
            }

            return inside;
        }

        hitTest(x, y) {
            for (
                let index =
                    this.points.length - 1;
                index >= 0;
                index -= 1
            ) {
                const feature =
                    this.points[index];

                if (
                    !feature.visible ||
                    !Number.isFinite(
                        feature.screenX
                    )
                ) {
                    continue;
                }

                const dx =
                    x - feature.screenX;
                const dy =
                    y - feature.screenY;
                const radius =
                    feature.screenRadius + 4;

                if (
                    dx * dx + dy * dy <=
                    radius * radius
                ) {
                    return feature;
                }
            }

            for (
                let index =
                    this.ranges.length - 1;
                index >= 0;
                index -= 1
            ) {
                const feature =
                    this.ranges[index];

                if (!feature.visible) {
                    continue;
                }

                for (const path of feature.paths) {
                    if (
                        path.length >= 3 &&
                        this._pointInPolygon(
                            x,
                            y,
                            path
                        )
                    ) {
                        return feature;
                    }
                }
            }

            return null;
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
                    feature:
                        hovered
                            ? this.describeFeature(
                                hovered
                            )
                            : null,
                    coordinate:
                        this._inverseProject(
                            point.x,
                            point.y
                        )
                });
            }
        }

        _handlePointerLeave() {
            this.drag = null;

            if (this.hovered) {
                this.hovered = null;
                this.draw();
                this._emit("hover", {
                    feature: null
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
            const previousZoom =
                this.transform.zoom;
            const factor =
                event.deltaY < 0
                    ? 1.12
                    : 1 / 1.12;
            const zoom = Math.max(
                0.5,
                Math.min(
                    16,
                    previousZoom *
                    factor
                )
            );
            const centerX =
                this.bounds.width / 2;
            const centerY =
                this.bounds.height / 2;
            const worldX =
                centerX +
                (
                    point.x -
                    centerX -
                    this.transform.x
                ) /
                previousZoom;
            const worldY =
                centerY +
                (
                    point.y -
                    centerY -
                    this.transform.y
                ) /
                previousZoom;

            this.transform.zoom = zoom;
            this.transform.x =
                point.x -
                centerX -
                (
                    worldX -
                    centerX
                ) *
                zoom;
            this.transform.y =
                point.y -
                centerY -
                (
                    worldY -
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
            const feature =
                this.hitTest(
                    point.x,
                    point.y
                );

            this.selected =
                feature?.id ===
                this.selected?.id
                    ? null
                    : feature;
            this.metrics.selections += 1;
            this.draw();

            this._emit("select", {
                feature:
                    this.selected
                        ? this.describeFeature(
                            this.selected
                        )
                        : null,
                coordinate:
                    this._inverseProject(
                        point.x,
                        point.y
                    )
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

        setProjection(projection) {
            if (
                ![
                    "equirectangular",
                    "mercator"
                ].includes(projection)
            ) {
                throw new Error(
                    `Unknown range-map projection: ${projection}`
                );
            }

            this.options.projection =
                projection;
            this.draw();

            return projection;
        }

        setZoom(value) {
            this.transform.zoom =
                Math.max(
                    0.5,
                    Math.min(
                        16,
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
            this._buildDensity();
            this.draw();

            this._emit("filter", {
                query:
                    this.query,
                visible:
                    this.features.filter(
                        (feature) =>
                            feature.visible
                    ).length
            });

            return this.query;
        }

        setGroup(group = null) {
            this.groupFilter =
                group
                    ? String(group)
                    : null;
            this._applyFilters();
            this._buildDensity();
            this.draw();

            return this.groupFilter;
        }

        describeFeature(feature) {
            if (!feature) {
                return null;
            }

            return {
                id:
                    feature.id,
                label:
                    feature.label,
                group:
                    feature.group,
                weight:
                    feature.weight,
                geometry:
                    clone(
                        feature.geometry
                    ),
                visible:
                    feature.visible,
                record:
                    clone(
                        feature.record
                    )
            };
        }

        selectFeature(id) {
            const feature =
                this.features.find(
                    (candidate) =>
                        candidate.id ===
                        String(id)
                );

            if (!feature) {
                return null;
            }

            this.selected = feature;
            this.draw();

            return this.describeFeature(
                feature
            );
        }

        update(options = {}) {
            if (!isObject(options)) {
                throw new TypeError(
                    "RangeMap options must be an object."
                );
            }

            const rebuildDensity =
                options.gridSize !== undefined ||
                options.showDensity !== undefined;

            Object.assign(
                this.options,
                {
                    projection:
                        options.projection ||
                        this.options.projection,
                    background:
                        options.background ||
                        this.options.background,
                    foreground:
                        options.foreground ||
                        this.options.foreground,
                    highlight:
                        options.highlight ||
                        this.options.highlight,
                    gridColor:
                        options.gridColor ||
                        this.options.gridColor,
                    landColor:
                        options.landColor ||
                        this.options.landColor,
                    waterColor:
                        options.waterColor ||
                        this.options.waterColor,
                    rangeColor:
                        options.rangeColor ||
                        this.options.rangeColor,
                    pointColor:
                        options.pointColor ||
                        this.options.pointColor,
                    padding:
                        options.padding !== undefined
                            ? parseNumber(
                                options.padding,
                                this.options.padding,
                                0,
                                200
                            )
                            : this.options.padding,
                    pointRadius:
                        options.pointRadius !== undefined
                            ? parseNumber(
                                options.pointRadius,
                                this.options.pointRadius,
                                1,
                                20
                            )
                            : this.options.pointRadius,
                    gridSize:
                        options.gridSize !== undefined
                            ? parseNumber(
                                options.gridSize,
                                this.options.gridSize,
                                8,
                                256
                            )
                            : this.options.gridSize,
                    fillAlpha:
                        options.fillAlpha !== undefined
                            ? parseNumber(
                                options.fillAlpha,
                                this.options.fillAlpha,
                                0,
                                1
                            )
                            : this.options.fillAlpha,
                    strokeAlpha:
                        options.strokeAlpha !== undefined
                            ? parseNumber(
                                options.strokeAlpha,
                                this.options.strokeAlpha,
                                0,
                                1
                            )
                            : this.options.strokeAlpha,
                    showGrid:
                        options.showGrid !== undefined
                            ? Boolean(
                                options.showGrid
                            )
                            : this.options.showGrid,
                    showPoints:
                        options.showPoints !== undefined
                            ? Boolean(
                                options.showPoints
                            )
                            : this.options.showPoints,
                    showRanges:
                        options.showRanges !== undefined
                            ? Boolean(
                                options.showRanges
                            )
                            : this.options.showRanges,
                    showDensity:
                        options.showDensity !== undefined
                            ? Boolean(
                                options.showDensity
                            )
                            : this.options.showDensity,
                    showLabels:
                        options.showLabels !== undefined
                            ? Boolean(
                                options.showLabels
                            )
                            : this.options.showLabels,
                    groupColors:
                        options.groupColors !== undefined
                            ? Boolean(
                                options.groupColors
                            )
                            : this.options.groupColors
                }
            );

            if (rebuildDensity) {
                this._buildDensity();
            }

            this.draw();

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

            if (normalized === "geojson") {
                return JSON.stringify(
                    {
                        type:
                            "FeatureCollection",
                        features:
                            this.features.map(
                                (feature) => ({
                                    type:
                                        "Feature",
                                    id:
                                        feature.id,
                                    properties: {
                                        label:
                                            feature.label,
                                        group:
                                            feature.group,
                                        weight:
                                            feature.weight
                                    },
                                    geometry:
                                        clone(
                                            feature.geometry
                                        )
                                })
                            )
                    },
                    null,
                    2
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
                        features:
                            this.features.map(
                                (feature) =>
                                    this.describeFeature(
                                        feature
                                    )
                            ),
                        density:
                            this.density.map(
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
                    "weight",
                    "geometryType",
                    "longitude",
                    "latitude"
                ]];

                for (
                    const feature
                    of this.features
                ) {
                    const point =
                        feature.geometry.type ===
                        "Point"
                            ? feature.geometry.coordinates
                            : ["", ""];

                    rows.push([
                        feature.id,
                        feature.label,
                        feature.group,
                        feature.weight,
                        feature.geometry.type,
                        point[0],
                        point[1]
                    ]);
                }

                return rows
                    .map(
                        (row) =>
                            row
                                .map(escapeCsv)
                                .join(",")
                    )
                    .join("\r\n");
            }

            throw new Error(
                `Unsupported RangeMap export format: ${format}`
            );
        }

        status() {
            return {
                name:
                    "range-map",
                module:
                    MODULE_NAME,
                records:
                    this.records.length,
                features:
                    this.features.length,
                visibleFeatures:
                    this.features.filter(
                        (feature) =>
                            feature.visible
                    ).length,
                points:
                    this.points.length,
                ranges:
                    this.ranges.length,
                densityCells:
                    this.density.length,
                groups:
                    this.metrics.groups,
                projection:
                    this.options.projection,
                query:
                    this.query,
                groupFilter:
                    this.groupFilter,
                transform:
                    clone(
                        this.transform
                    ),
                selected:
                    this.selected
                        ? this.describeFeature(
                            this.selected
                        )
                        : null,
                hovered:
                    this.hovered
                        ? this.describeFeature(
                            this.hovered
                        )
                        : null,
                options:
                    clone(
                        this.options
                    ),
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
                    "keydown",
                    this._boundKeydown
                );
            }

            this.records = [];
            this.features = [];
            this.points = [];
            this.ranges = [];
            this.density = [];
            this.destroyed = true;

            this._emit("destroy", {});
            return true;
        }
    }

    function mount(target, data = [], options = {}) {
        return new RangeMapController(
            target,
            data,
            options
        );
    }

    function render(data = [], options = {}) {
        const container = createElement(
            "section",
            "terminal-visualization terminal-visualization-range-map"
        );
        container.dataset.visualization =
            "range-map";
        container.setAttribute(
            "role",
            "region"
        );
        container.setAttribute(
            "aria-label",
            options.label ||
            "RangeMap visualization"
        );

        const canvas =
            document.createElement("canvas");
        canvas.className =
            "terminal-range-map-canvas";
        canvas.width =
            Number(options.width) ||
            DEFAULT_WIDTH;
        canvas.height =
            Number(options.height) ||
            DEFAULT_HEIGHT;
        canvas.setAttribute(
            "aria-label",
            options.label ||
            "RangeMap visualization"
        );

        const status = createElement(
            "div",
            "terminal-range-map-status"
        );
        status.setAttribute(
            "aria-live",
            "polite"
        );

        const tooltip = createElement(
            "div",
            "terminal-range-map-tooltip"
        );
        tooltip.hidden = true;

        container.append(
            canvas,
            status,
            tooltip
        );

        const controller =
            new RangeMapController(
                canvas,
                data,
                options
            );

        const updateStatus = () => {
            const snapshot =
                controller.status();

            status.textContent =
                `${snapshot.visibleFeatures} of ${snapshot.features} feature` +
                `${snapshot.features === 1 ? "" : "s"} · ` +
                `${snapshot.points} point` +
                `${snapshot.points === 1 ? "" : "s"} · ` +
                `${snapshot.ranges} range` +
                `${snapshot.ranges === 1 ? "" : "s"} · ` +
                `${snapshot.projection} · ` +
                `${snapshot.transform.zoom.toFixed(2)}×`;
        };

        controller.addEventListener(
            "hover",
            (event) => {
                const feature =
                    event.detail?.feature;
                const coordinate =
                    event.detail?.coordinate;

                if (!feature && !coordinate) {
                    tooltip.hidden = true;
                    return;
                }

                tooltip.hidden = false;
                tooltip.textContent =
                    feature
                        ? `${feature.label} · ${feature.group} · ${feature.geometry.type}`
                        : (
                            `${coordinate.latitude.toFixed(4)}, ` +
                            `${coordinate.longitude.toFixed(4)}`
                        );
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
            controller.features;
        container.destroy = () =>
            controller.destroy();

        return container;
    }

    function initialize(context = {}) {
        const dataset =
            context.root?.dataset || {};
        const config =
            context.config?.rangeMap ||
            context.config?.["range-map"] ||
            {};

        const defaults = {
            projection:
                dataset.terminalRangeMapProjection ||
                config.projection ||
                "equirectangular",

            background:
                dataset.terminalRangeMapBackground ||
                config.background ||
                DEFAULT_BACKGROUND,

            foreground:
                dataset.terminalRangeMapForeground ||
                config.foreground ||
                DEFAULT_FOREGROUND,

            highlight:
                dataset.terminalRangeMapHighlight ||
                config.highlight ||
                DEFAULT_HIGHLIGHT,

            gridColor:
                dataset.terminalRangeMapGrid ||
                config.gridColor ||
                DEFAULT_GRID,

            landColor:
                dataset.terminalRangeMapLand ||
                config.landColor ||
                DEFAULT_LAND,

            waterColor:
                dataset.terminalRangeMapWater ||
                config.waterColor ||
                DEFAULT_WATER,

            rangeColor:
                dataset.terminalRangeMapRange ||
                config.rangeColor ||
                DEFAULT_RANGE,

            pointColor:
                dataset.terminalRangeMapPoint ||
                config.pointColor ||
                DEFAULT_POINT,

            pointRadius:
                dataset.terminalRangeMapPointRadius ||
                config.pointRadius ||
                DEFAULT_POINT_RADIUS,

            gridSize:
                dataset.terminalRangeMapGridSize ||
                config.gridSize ||
                DEFAULT_GRID_SIZE,

            showGrid: parseBoolean(
                dataset.terminalRangeMapShowGrid,
                config.showGrid !== false
            ),

            showPoints: parseBoolean(
                dataset.terminalRangeMapShowPoints,
                config.showPoints !== false
            ),

            showRanges: parseBoolean(
                dataset.terminalRangeMapShowRanges,
                config.showRanges !== false
            ),

            showDensity: parseBoolean(
                dataset.terminalRangeMapShowDensity,
                config.showDensity === true
            ),

            showLabels: parseBoolean(
                dataset.terminalRangeMapShowLabels,
                config.showLabels === true
            ),

            interactive: parseBoolean(
                dataset.terminalRangeMapInteractive,
                config.interactive !== false
            )
        };

        const visualization = {
            mount(target, data = [], options = {}) {
                return new RangeMapController(
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
                RangeMapController,

            normalizeRecords,

            extractPoint,

            extractBounds,

            geometryForRecord
        };

        context.registerVisualization?.(
            "range-map",
            visualization
        );
        context.registerRenderer?.(
            "range-map",
            visualization
        );
        context.rangeMap =
            visualization;

        safeDispatch(
            document,
            "speciedex:terminal-range-map-ready",
            {
                visualization
            }
        );

        return visualization;
    }

    const commands = [{
        name: "range-map",
        category: "visualization",
        description:
            "Render and control geographic occurrence points, polygons, ranges, and density.",
        usage:
            "range-map [collection|status|projection|filter|group|density|" +
            "zoom|pan|reset|export] [arguments]",
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
                context.rangeMapController ||
                context.terminalRangeMapController;

            try {
                if (controller) {
                    switch (lower) {
                        case "status":
                        case "show":
                        case "info":
                            return writeJSON(
                                controller.status()
                            );

                        case "projection":
                            if (!args[1]) {
                                return writeJSON({
                                    projection:
                                        controller.options.projection
                                });
                            }

                            return writeJSON({
                                projection:
                                    controller.setProjection(
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

                        case "density":
                            controller.update({
                                showDensity:
                                    args[1] === undefined
                                        ? !controller.options.showDensity
                                        : parseBoolean(
                                            args[1],
                                            controller.options.showDensity
                                        )
                            });

                            return writeJSON({
                                showDensity:
                                    controller.options.showDensity
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
                        ...context.config?.rangeMap,
                        ...context.config?.["range-map"],
                        label:
                            `RangeMap for ${collection}`
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
        RangeMapController,
        normalizeRecords,
        extractPoint,
        extractBounds,
        geometryForRecord,
        mount,
        render,
        initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalRangeMap =
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
