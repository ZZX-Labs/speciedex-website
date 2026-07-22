/*
========================================================================
Speciedex.org
Terminal Charts Renderer
========================================================================

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Charts";
    const SVG_NS = "http://www.w3.org/2000/svg";
    const DEFAULT_WIDTH = 720;
    const DEFAULT_HEIGHT = 360;
    const DEFAULT_LIMIT = 100;
    const CHART_TYPES = new Set(["bar", "line", "area", "scatter"]);

    function isObject(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function clamp(value, minimum, maximum) {
        return Math.min(maximum, Math.max(minimum, value));
    }

    function finiteNumber(value, fallback = 0) {
        const number = Number(value);
        return Number.isFinite(number) ? number : fallback;
    }

    function text(value) {
        return String(value ?? "").trim();
    }

    function titleCase(value) {
        return text(value)
            .replace(/[_-]+/g, " ")
            .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
            .replace(/\b\w/g, character => character.toUpperCase());
    }

    function createSVGElement(name, attributes = {}) {
        const element = document.createElementNS(SVG_NS, name);

        for (const [key, value] of Object.entries(attributes)) {
            if (value !== undefined && value !== null) {
                element.setAttribute(key, String(value));
            }
        }

        return element;
    }

    function dispatch(target, name, detail) {
        try {
            target.dispatchEvent(new CustomEvent(name, { detail }));
        } catch (error) {
            /* Chart events must never interrupt terminal rendering. */
        }
    }

    function normalizeOptions(options = {}) {
        const type = text(options.type || options.kind || "bar").toLowerCase();

        return {
            type: CHART_TYPES.has(type) ? type : "bar",
            title: text(options.title),
            description: text(options.description),
            labelKey: text(options.labelKey || options.xKey || "label"),
            valueKey: text(options.valueKey || options.yKey || "value"),
            seriesKey: text(options.seriesKey),
            width: clamp(finiteNumber(options.width, DEFAULT_WIDTH), 320, 1920),
            height: clamp(finiteNumber(options.height, DEFAULT_HEIGHT), 200, 1080),
            limit: clamp(Math.floor(finiteNumber(options.limit, DEFAULT_LIMIT)), 1, 5000),
            showLegend: options.showLegend !== false,
            showTable: options.showTable !== false,
            showValues: options.showValues !== false,
            sort: text(options.sort || "none").toLowerCase(),
            emptyText: text(options.emptyText || "No chart data."),
            ariaLabel: text(options.ariaLabel),
            min: Number.isFinite(Number(options.min)) ? Number(options.min) : null,
            max: Number.isFinite(Number(options.max)) ? Number(options.max) : null
        };
    }

    function rowFromValue(value, index, options) {
        if (isObject(value)) {
            const label = value[options.labelKey] ?? value.label ?? value.name ?? value.key ?? index + 1;
            const amount = value[options.valueKey] ?? value.value ?? value.count ?? value.total ?? 0;
            const series = options.seriesKey
                ? value[options.seriesKey]
                : value.series ?? value.group ?? "Series";

            return {
                label: text(label) || String(index + 1),
                value: finiteNumber(amount),
                series: text(series) || "Series",
                source: value
            };
        }

        if (Array.isArray(value)) {
            return {
                label: text(value[0]) || String(index + 1),
                value: finiteNumber(value[1]),
                series: text(value[2]) || "Series",
                source: value
            };
        }

        return {
            label: String(index + 1),
            value: finiteNumber(value),
            series: "Series",
            source: value
        };
    }

    function normalizeData(data, options = {}) {
        const normalizedOptions = normalizeOptions(options);
        let rows = [];

        if (data instanceof Map) {
            rows = Array.from(data.entries()).map(([label, value], index) =>
                rowFromValue([label, value], index, normalizedOptions)
            );
        } else if (Array.isArray(data)) {
            rows = data.map((value, index) =>
                rowFromValue(value, index, normalizedOptions)
            );
        } else if (isObject(data)) {
            if (Array.isArray(data.data)) {
                rows = data.data.map((value, index) =>
                    rowFromValue(value, index, normalizedOptions)
                );
            } else if (Array.isArray(data.items)) {
                rows = data.items.map((value, index) =>
                    rowFromValue(value, index, normalizedOptions)
                );
            } else {
                rows = Object.entries(data).map(([label, value], index) =>
                    rowFromValue([label, value], index, normalizedOptions)
                );
            }
        } else if (typeof data === "string") {
            const trimmed = data.trim();

            if (trimmed) {
                try {
                    return normalizeData(JSON.parse(trimmed), normalizedOptions);
                } catch (error) {
                    rows = trimmed.split(/\r?\n/).filter(Boolean).map((line, index) => {
                        const parts = line.split(/[,:\t]/);
                        return rowFromValue([parts.shift(), parts.join(":")], index, normalizedOptions);
                    });
                }
            }
        } else if (data !== undefined && data !== null) {
            rows = [rowFromValue(data, 0, normalizedOptions)];
        }

        rows = rows.slice(0, normalizedOptions.limit);

        if (normalizedOptions.sort === "asc") {
            rows.sort((left, right) => left.value - right.value);
        } else if (normalizedOptions.sort === "desc") {
            rows.sort((left, right) => right.value - left.value);
        } else if (normalizedOptions.sort === "label") {
            rows.sort((left, right) => left.label.localeCompare(right.label, undefined, {
                numeric: true,
                sensitivity: "base"
            }));
        }

        return rows;
    }

    function bounds(rows, options) {
        const values = rows.map(row => row.value);
        let minimum = options.min ?? Math.min(0, ...values);
        let maximum = options.max ?? Math.max(0, ...values);

        if (!Number.isFinite(minimum)) minimum = 0;
        if (!Number.isFinite(maximum)) maximum = 1;
        if (minimum === maximum) {
            const padding = Math.abs(minimum || 1) * 0.1;
            minimum -= padding;
            maximum += padding;
        }

        return { minimum, maximum, range: maximum - minimum };
    }

    function appendTitle(container, options) {
        if (!options.title && !options.description) return;

        const header = document.createElement("header");
        header.className = "terminal-chart-header";

        if (options.title) {
            const heading = document.createElement("h3");
            heading.className = "terminal-chart-title";
            heading.textContent = options.title;
            header.appendChild(heading);
        }

        if (options.description) {
            const description = document.createElement("p");
            description.className = "terminal-chart-description";
            description.textContent = options.description;
            header.appendChild(description);
        }

        container.appendChild(header);
    }

    function appendAxis(svg, dimensions, chartBounds) {
        const { left, top, plotWidth, plotHeight } = dimensions;
        const zeroRatio = (0 - chartBounds.minimum) / chartBounds.range;
        const zeroY = top + plotHeight - clamp(zeroRatio, 0, 1) * plotHeight;

        svg.appendChild(createSVGElement("line", {
            class: "terminal-chart-axis terminal-chart-axis-y",
            x1: left,
            y1: top,
            x2: left,
            y2: top + plotHeight
        }));

        svg.appendChild(createSVGElement("line", {
            class: "terminal-chart-axis terminal-chart-axis-x",
            x1: left,
            y1: zeroY,
            x2: left + plotWidth,
            y2: zeroY
        }));

        for (let index = 0; index <= 4; index += 1) {
            const ratio = index / 4;
            const y = top + plotHeight - ratio * plotHeight;
            const value = chartBounds.minimum + ratio * chartBounds.range;

            svg.appendChild(createSVGElement("line", {
                class: "terminal-chart-gridline",
                x1: left,
                y1: y,
                x2: left + plotWidth,
                y2: y
            }));

            const label = createSVGElement("text", {
                class: "terminal-chart-axis-label",
                x: left - 8,
                y: y + 4,
                "text-anchor": "end"
            });
            label.textContent = Number(value.toPrecision(4)).toLocaleString();
            svg.appendChild(label);
        }
    }

    function renderBar(svg, rows, options, dimensions, chartBounds) {
        const { left, top, plotWidth, plotHeight } = dimensions;
        const slot = plotWidth / Math.max(rows.length, 1);
        const barWidth = Math.max(2, slot * 0.72);
        const zeroY = top + plotHeight - clamp((0 - chartBounds.minimum) / chartBounds.range, 0, 1) * plotHeight;

        rows.forEach((row, index) => {
            const ratio = (row.value - chartBounds.minimum) / chartBounds.range;
            const valueY = top + plotHeight - ratio * plotHeight;
            const y = Math.min(valueY, zeroY);
            const height = Math.max(1, Math.abs(zeroY - valueY));
            const x = left + slot * index + (slot - barWidth) / 2;

            const group = createSVGElement("g", {
                class: "terminal-chart-datum terminal-chart-bar",
                tabindex: "0",
                role: "img",
                "aria-label": `${row.label}: ${row.value}`
            });

            const rect = createSVGElement("rect", {
                x,
                y,
                width: barWidth,
                height,
                rx: 1,
                "data-chart-label": row.label,
                "data-chart-value": row.value
            });
            group.appendChild(rect);

            const label = createSVGElement("text", {
                class: "terminal-chart-category-label",
                x: x + barWidth / 2,
                y: top + plotHeight + 18,
                "text-anchor": "middle"
            });
            label.textContent = row.label.length > 12 ? `${row.label.slice(0, 11)}…` : row.label;
            group.appendChild(label);

            if (options.showValues) {
                const valueLabel = createSVGElement("text", {
                    class: "terminal-chart-value-label",
                    x: x + barWidth / 2,
                    y: row.value >= 0 ? y - 5 : y + height + 13,
                    "text-anchor": "middle"
                });
                valueLabel.textContent = row.value.toLocaleString();
                group.appendChild(valueLabel);
            }

            svg.appendChild(group);
        });
    }

    function pointCoordinates(rows, dimensions, chartBounds) {
        const { left, top, plotWidth, plotHeight } = dimensions;
        const denominator = Math.max(rows.length - 1, 1);

        return rows.map((row, index) => ({
            row,
            x: left + (index / denominator) * plotWidth,
            y: top + plotHeight - ((row.value - chartBounds.minimum) / chartBounds.range) * plotHeight
        }));
    }

    function renderLine(svg, rows, options, dimensions, chartBounds) {
        const points = pointCoordinates(rows, dimensions, chartBounds);
        const coordinates = points.map(point => `${point.x},${point.y}`).join(" ");

        if (options.type === "area" && points.length) {
            const baseline = dimensions.top + dimensions.plotHeight;
            const area = createSVGElement("polygon", {
                class: "terminal-chart-area",
                points: `${points[0].x},${baseline} ${coordinates} ${points[points.length - 1].x},${baseline}`
            });
            svg.appendChild(area);
        }

        if (options.type !== "scatter") {
            svg.appendChild(createSVGElement("polyline", {
                class: "terminal-chart-line",
                points: coordinates,
                fill: "none"
            }));
        }

        points.forEach(point => {
            const group = createSVGElement("g", {
                class: "terminal-chart-datum terminal-chart-point",
                tabindex: "0",
                role: "img",
                "aria-label": `${point.row.label}: ${point.row.value}`
            });

            group.appendChild(createSVGElement("circle", {
                cx: point.x,
                cy: point.y,
                r: options.type === "scatter" ? 5 : 3,
                "data-chart-label": point.row.label,
                "data-chart-value": point.row.value
            }));

            const label = createSVGElement("text", {
                class: "terminal-chart-category-label",
                x: point.x,
                y: dimensions.top + dimensions.plotHeight + 18,
                "text-anchor": "middle"
            });
            label.textContent = point.row.label.length > 12
                ? `${point.row.label.slice(0, 11)}…`
                : point.row.label;
            group.appendChild(label);

            svg.appendChild(group);
        });
    }

    function appendTable(container, rows, options) {
        if (!options.showTable) return;

        const details = document.createElement("details");
        details.className = "terminal-chart-data";

        const summary = document.createElement("summary");
        summary.textContent = `Chart data (${rows.length})`;
        details.appendChild(summary);

        const table = document.createElement("table");
        table.className = "terminal-chart-table";

        const thead = document.createElement("thead");
        const headRow = document.createElement("tr");
        for (const label of ["Label", "Value", "Series"]) {
            const th = document.createElement("th");
            th.scope = "col";
            th.textContent = label;
            headRow.appendChild(th);
        }
        thead.appendChild(headRow);
        table.appendChild(thead);

        const tbody = document.createElement("tbody");
        rows.forEach(row => {
            const tr = document.createElement("tr");
            [row.label, row.value.toLocaleString(), row.series].forEach(value => {
                const td = document.createElement("td");
                td.textContent = value;
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        details.appendChild(table);
        container.appendChild(details);
    }

    function render(data, rawOptions = {}) {
        const options = normalizeOptions(rawOptions);
        const rows = normalizeData(data, options);
        const container = document.createElement("figure");

        container.className = `terminal-renderer terminal-renderer-chart terminal-chart-${options.type}`;
        container.dataset.renderer = "chart";
        container.dataset.chartType = options.type;
        container.dataset.chartRows = String(rows.length);

        appendTitle(container, options);

        if (!rows.length) {
            const empty = document.createElement("p");
            empty.className = "terminal-chart-empty";
            empty.textContent = options.emptyText;
            container.appendChild(empty);
            return container;
        }

        const svg = createSVGElement("svg", {
            class: "terminal-chart-svg",
            viewBox: `0 0 ${options.width} ${options.height}`,
            width: options.width,
            height: options.height,
            role: "img",
            "aria-label": options.ariaLabel || options.title || `${titleCase(options.type)} chart`
        });

        const title = createSVGElement("title");
        title.textContent = options.ariaLabel || options.title || `${titleCase(options.type)} chart`;
        svg.appendChild(title);

        const dimensions = {
            left: 72,
            top: 28,
            plotWidth: options.width - 100,
            plotHeight: options.height - 82
        };
        const chartBounds = bounds(rows, options);

        appendAxis(svg, dimensions, chartBounds);

        if (options.type === "bar") {
            renderBar(svg, rows, options, dimensions, chartBounds);
        } else {
            renderLine(svg, rows, options, dimensions, chartBounds);
        }

        container.appendChild(svg);
        appendTable(container, rows, options);

        dispatch(container, "speciedex:terminal-chart-rendered", {
            container,
            rows,
            options
        });

        return container;
    }

    class ChartRenderer {
        constructor(context) {
            this.context = context;
        }

        render(data, options = {}) {
            return render(data, options);
        }

        normalize(data, options = {}) {
            return normalizeData(data, options);
        }

        types() {
            return Array.from(CHART_TYPES);
        }
    }

    function initialize(context) {
        if (!context || typeof context !== "object") {
            throw new TypeError("A terminal context is required to initialize Charts.");
        }

        if (context.chartRenderer instanceof ChartRenderer) {
            return context.chartRenderer;
        }

        const renderer = new ChartRenderer(context);
        context.chartRenderer = renderer;
        context.registerRenderer?.("chart", renderer);
        context.registerService?.("charts", renderer);
        return renderer;
    }

    function parseCommand(args) {
        const tokens = Array.isArray(args) ? [...args] : [];
        const options = {};
        const values = [];

        while (tokens.length) {
            const token = tokens.shift();

            if (token.startsWith("--")) {
                const raw = token.slice(2);
                const equals = raw.indexOf("=");
                const key = equals >= 0 ? raw.slice(0, equals) : raw;
                const value = equals >= 0 ? raw.slice(equals + 1) : tokens.shift();

                if (["table", "values", "legend"].includes(key)) {
                    options[`show${titleCase(key)}`] = !["0", "false", "no", "off"].includes(
                        text(value || "true").toLowerCase()
                    );
                } else {
                    options[key] = value ?? true;
                }
                continue;
            }

            values.push(token);
        }

        if (values.length && CHART_TYPES.has(values[0].toLowerCase())) {
            options.type = values.shift().toLowerCase();
        }

        return { options, source: values.join(" ").trim() };
    }

    const commands = [{
        name: "chart",
        aliases: ["charts", "plot"],
        category: "visualization",
        description: "Render terminal data as an accessible SVG chart.",
        usage: "chart [bar|line|area|scatter] <JSON|label:value ...> [--title=TEXT] [--sort=asc|desc|label]",
        handler: ({ args, context, write }) => {
            const parsed = parseCommand(args);

            if (!parsed.source) {
                return write(
                    "Usage: chart [bar|line|area|scatter] <JSON|label:value ...> [--title=TEXT]",
                    "help"
                );
            }

            let data;
            try {
                data = JSON.parse(parsed.source);
            } catch (error) {
                data = parsed.source.split(/\s+/).filter(Boolean).map((item, index) => {
                    const separator = item.indexOf(":");
                    return separator >= 0
                        ? [item.slice(0, separator), item.slice(separator + 1)]
                        : [String(index + 1), item];
                });
            }

            const renderer = context.chartRenderer || context.getRenderer?.("chart");
            if (!renderer || typeof renderer.render !== "function") {
                throw new Error("The chart renderer is unavailable.");
            }

            const node = renderer.render(data, parsed.options);
            if (typeof context.app?.append === "function") {
                context.app.append(node);
                return node;
            }

            return write(node.textContent || "Chart rendered.", "output");
        }
    }];

    const api = Object.freeze({
        name: MODULE_NAME,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        render,
        normalizeData,
        ChartRenderer,
        commands
    });

    window.SpeciedexTerminalCharts = api;
    window.SpeciedexTerminalModules = window.SpeciedexTerminalModules || {};
    window.SpeciedexTerminalModules[MODULE_NAME] = api;

    document.dispatchEvent(new CustomEvent("speciedex:terminal-module-available", {
        detail: { name: MODULE_NAME, module: api }
    }));
})(window, document);
