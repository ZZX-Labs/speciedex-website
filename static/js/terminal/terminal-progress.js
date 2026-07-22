/*
========================================================================
Speciedex.org
Terminal Progress Renderer
========================================================================

Progress rendering and coordination service for SpeciedexTerminal.

Provides:

    • determinate progress bars
    • indeterminate progress bars
    • multiple concurrent progress tasks
    • weighted aggregate progress
    • elapsed and estimated remaining time
    • labels, descriptions, and status text
    • cancellation controls
    • task completion and failure states
    • progress history
    • renderer updates
    • integration with terminal-loading.js
    • root, document, and event-bus propagation
    • terminal commands
    • JSON export
    • clean teardown

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "Progress";

    const VERSION =
        "2.0.0";

    const PRIMARY_COLOR =
        "#c0d674";

    const ACCENT_COLOR =
        "#e6a42b";

    const DEFAULT_OPTIONS =
        Object.freeze({
            minimum:
                0,

            maximum:
                100,

            value:
                0,

            label:
                "Progress",

            description:
                "",

            indeterminate:
                false,

            cancellable:
                false,

            showValue:
                true,

            showPercent:
                true,

            showElapsed:
                true,

            showRemaining:
                true,

            showDescription:
                true,

            animated:
                true,

            striped:
                false,

            compact:
                false,

            weight:
                1,

            historyLimit:
                500,

            integrateLoading:
                true,

            injectStyles:
                true
        });

    const STATES =
        Object.freeze([
            "idle",
            "running",
            "paused",
            "success",
            "warning",
            "error",
            "cancelled"
        ]);

    /*
    ==========================================================================
    Utilities
    ==========================================================================
    */

    function clamp(
        value,
        minimum,
        maximum
    ) {
        return Math.min(
            maximum,
            Math.max(
                minimum,
                value
            )
        );
    }

    function parseNumber(
        value,
        fallback = 0
    ) {
        const numeric =
            Number(value);

        return Number.isFinite(
            numeric
        )
            ? numeric
            : fallback;
    }

    function parseBoolean(
        value,
        fallback = false
    ) {
        if (
            value === undefined ||
            value === null ||
            value === ""
        ) {
            return fallback;
        }

        return ![
            "false",
            "0",
            "no",
            "off"
        ].includes(
            String(value)
                .trim()
                .toLowerCase()
        );
    }

    function normalizeState(
        value
    ) {
        const state =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase();

        return STATES.includes(
            state
        )
            ? state
            : "idle";
    }

    function normalizeID(
        value
    ) {
        const id =
            String(
                value ?? ""
            ).trim();

        if (!id) {
            throw new Error(
                "Progress task ID is required."
            );
        }

        return id;
    }

    function makeID() {
        if (
            window.crypto &&
            typeof window.crypto.randomUUID ===
            "function"
        ) {
            return window.crypto.randomUUID();
        }

        return (
            `progress:${Date.now()}:` +
            Math.random()
                .toString(16)
                .slice(2)
        );
    }

    function formatDuration(
        milliseconds
    ) {
        const value =
            Math.max(
                0,
                Number(
                    milliseconds
                ) ||
                0
            );

        if (value < 1000) {
            return `${Math.round(value)}ms`;
        }

        const totalSeconds =
            Math.floor(
                value /
                1000
            );

        const hours =
            Math.floor(
                totalSeconds /
                3600
            );

        const minutes =
            Math.floor(
                (
                    totalSeconds %
                    3600
                ) /
                60
            );

        const seconds =
            totalSeconds %
            60;

        if (hours) {
            return [
                hours,
                String(minutes).padStart(
                    2,
                    "0"
                ),
                String(seconds).padStart(
                    2,
                    "0"
                )
            ].join(":");
        }

        return [
            minutes,
            String(seconds).padStart(
                2,
                "0"
            )
        ].join(":");
    }

    function estimateRemaining(
        minimum,
        maximum,
        value,
        elapsed
    ) {
        const range =
            maximum -
            minimum;

        if (
            range <=
                0 ||
            elapsed <=
                0
        ) {
            return null;
        }

        const completed =
            value -
            minimum;

        if (
            completed <=
                0
        ) {
            return null;
        }

        const ratio =
            completed /
            range;

        if (
            ratio <=
                0 ||
            ratio >=
                1
        ) {
            return ratio >=
                1
                ? 0
                : null;
        }

        return (
            elapsed /
            ratio
        ) -
        elapsed;
    }

    function serializeTask(
        task
    ) {
        return {
            id:
                task.id,

            label:
                task.label,

            description:
                task.description,

            minimum:
                task.minimum,

            maximum:
                task.maximum,

            value:
                task.value,

            percent:
                task.percent,

            state:
                task.state,

            indeterminate:
                task.indeterminate,

            cancellable:
                task.cancellable,

            weight:
                task.weight,

            startedAt:
                task.startedAt,

            updatedAt:
                task.updatedAt,

            completedAt:
                task.completedAt,

            elapsed:
                task.elapsed,

            remaining:
                task.remaining,

            metadata:
                task.metadata,

            error:
                task.error
        };
    }

    /*
    ==========================================================================
    Styles
    ==========================================================================
    */

    function injectProgressStyles() {
        if (
            document.getElementById(
                "speciedex-terminal-progress-styles"
            )
        ) {
            return;
        }

        const style =
            document.createElement(
                "style"
            );

        style.id =
            "speciedex-terminal-progress-styles";

        style.textContent = `
            .terminal-progress {
                --progress-color: ${PRIMARY_COLOR};
                --progress-accent: ${ACCENT_COLOR};
                display: grid;
                gap: 0.42rem;
                width: 100%;
                padding: 0.7rem 0.8rem;
                border: 1px solid rgba(192, 214, 116, 0.2);
                background: rgba(4, 10, 6, 0.9);
                color: #d8e6db;
                font-family:
                    "IBM Plex Mono",
                    ui-monospace,
                    SFMono-Regular,
                    Consolas,
                    monospace;
            }

            .terminal-progress[data-compact="true"] {
                gap: 0.28rem;
                padding: 0.48rem 0.58rem;
            }

            .terminal-progress-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 1rem;
            }

            .terminal-progress-title {
                color: var(--progress-color);
                font-size: 0.76rem;
                letter-spacing: 0.04em;
                overflow-wrap: anywhere;
            }

            .terminal-progress-value {
                color: rgba(216, 230, 219, 0.75);
                font-size: 0.7rem;
                white-space: nowrap;
            }

            .terminal-progress-track {
                position: relative;
                overflow: hidden;
                height: 0.72rem;
                border: 1px solid rgba(192, 214, 116, 0.16);
                background: rgba(216, 230, 219, 0.055);
            }

            .terminal-progress-bar {
                display: block;
                width: 0;
                height: 100%;
                background:
                    linear-gradient(
                        90deg,
                        rgba(192, 214, 116, 0.72),
                        var(--progress-color)
                    );
                box-shadow:
                    0 0 0.7rem rgba(192, 214, 116, 0.32);
                transition:
                    width 160ms ease;
            }

            .terminal-progress[data-animated="true"]
            .terminal-progress-bar {
                background-size: 200% 100%;
                animation:
                    speciedex-terminal-progress-shift
                    1.25s linear infinite;
            }

            .terminal-progress[data-striped="true"]
            .terminal-progress-bar {
                background-image:
                    linear-gradient(
                        135deg,
                        rgba(255, 255, 255, 0.18) 25%,
                        transparent 25%,
                        transparent 50%,
                        rgba(255, 255, 255, 0.18) 50%,
                        rgba(255, 255, 255, 0.18) 75%,
                        transparent 75%,
                        transparent
                    ),
                    linear-gradient(
                        90deg,
                        rgba(192, 214, 116, 0.72),
                        var(--progress-color)
                    );
                background-size:
                    1rem 1rem,
                    200% 100%;
            }

            .terminal-progress[data-indeterminate="true"]
            .terminal-progress-bar {
                width: 36% !important;
                animation:
                    speciedex-terminal-progress-indeterminate
                    1.15s ease-in-out infinite;
            }

            .terminal-progress[data-state="success"] {
                --progress-color: #c0d674;
            }

            .terminal-progress[data-state="warning"] {
                --progress-color: #e6a42b;
            }

            .terminal-progress[data-state="error"] {
                --progress-color: #ff7d73;
            }

            .terminal-progress[data-state="cancelled"] {
                --progress-color: #9ca3af;
            }

            .terminal-progress[data-state="paused"] {
                --progress-color: #7fc8ff;
            }

            .terminal-progress-description {
                margin: 0;
                color: rgba(216, 230, 219, 0.68);
                font-size: 0.67rem;
                line-height: 1.45;
            }

            .terminal-progress-meta {
                display: flex;
                flex-wrap: wrap;
                gap: 0.65rem;
                color: rgba(216, 230, 219, 0.56);
                font-size: 0.64rem;
            }

            .terminal-progress-actions {
                display: flex;
                justify-content: flex-end;
                gap: 0.4rem;
            }

            .terminal-progress-cancel {
                border: 1px solid rgba(230, 164, 43, 0.42);
                background: rgba(4, 10, 6, 0.8);
                color: var(--progress-accent);
                padding: 0.28rem 0.46rem;
                font: inherit;
                font-size: 0.65rem;
                cursor: pointer;
            }

            .terminal-progress-cancel:hover,
            .terminal-progress-cancel:focus-visible {
                background: rgba(230, 164, 43, 0.1);
                outline: none;
            }

            .terminal-progress-list {
                display: grid;
                gap: 0.6rem;
            }

            @keyframes speciedex-terminal-progress-shift {
                to {
                    background-position:
                        -200% 0;
                }
            }

            @keyframes speciedex-terminal-progress-indeterminate {
                0% {
                    transform: translateX(-120%);
                }

                50% {
                    transform: translateX(90%);
                }

                100% {
                    transform: translateX(300%);
                }
            }

            @media (prefers-reduced-motion: reduce) {
                .terminal-progress-bar {
                    transition: none;
                }

                .terminal-progress[data-animated="true"]
                .terminal-progress-bar,
                .terminal-progress[data-indeterminate="true"]
                .terminal-progress-bar {
                    animation-duration: 3s;
                }
            }
        `;

        document.head.appendChild(
            style
        );
    }

    /*
    ==========================================================================
    Progress View
    ==========================================================================
    */

    class ProgressView
        extends EventTarget {
        constructor(
            task,
            options = {}
        ) {
            super();

            this.task =
                task;

            this.options = {
                ...DEFAULT_OPTIONS,
                ...options
            };

            this.destroyed =
                false;

            if (
                this.options.injectStyles
            ) {
                injectProgressStyles();
            }

            this.element =
                this.build();

            this.update(
                task
            );
        }

        build() {
            const wrapper =
                document.createElement(
                    "section"
                );

            wrapper.className =
                "terminal-progress";

            wrapper.dataset.progressId =
                this.task.id;

            wrapper.dataset.state =
                this.task.state;

            wrapper.dataset.indeterminate =
                String(
                    this.task.indeterminate
                );

            wrapper.dataset.compact =
                String(
                    this.options.compact ===
                    true
                );

            wrapper.dataset.animated =
                String(
                    this.options.animated !==
                    false
                );

            wrapper.dataset.striped =
                String(
                    this.options.striped ===
                    true
                );

            wrapper.setAttribute(
                "role",
                "progressbar"
            );

            const header =
                document.createElement(
                    "header"
                );

            header.className =
                "terminal-progress-header";

            const title =
                document.createElement(
                    "span"
                );

            title.className =
                "terminal-progress-title";

            title.dataset.progressTitle =
                "";

            const value =
                document.createElement(
                    "span"
                );

            value.className =
                "terminal-progress-value";

            value.dataset.progressValue =
                "";

            header.append(
                title,
                value
            );

            const track =
                document.createElement(
                    "div"
                );

            track.className =
                "terminal-progress-track";

            const bar =
                document.createElement(
                    "span"
                );

            bar.className =
                "terminal-progress-bar";

            bar.dataset.progressBar =
                "";

            track.appendChild(
                bar
            );

            const description =
                document.createElement(
                    "p"
                );

            description.className =
                "terminal-progress-description";

            description.dataset.progressDescription =
                "";

            const meta =
                document.createElement(
                    "div"
                );

            meta.className =
                "terminal-progress-meta";

            const elapsed =
                document.createElement(
                    "span"
                );

            elapsed.dataset.progressElapsed =
                "";

            const remaining =
                document.createElement(
                    "span"
                );

            remaining.dataset.progressRemaining =
                "";

            const state =
                document.createElement(
                    "span"
                );

            state.dataset.progressState =
                "";

            meta.append(
                elapsed,
                remaining,
                state
            );

            const actions =
                document.createElement(
                    "div"
                );

            actions.className =
                "terminal-progress-actions";

            const cancel =
                document.createElement(
                    "button"
                );

            cancel.type =
                "button";

            cancel.className =
                "terminal-progress-cancel";

            cancel.dataset.progressCancel =
                "";

            cancel.textContent =
                "Cancel";

            cancel.addEventListener(
                "click",
                () => {
                    this.dispatchEvent(
                        new CustomEvent(
                            "cancel",
                            {
                                detail: {
                                    task:
                                        this.task
                                }
                            }
                        )
                    );
                }
            );

            actions.appendChild(
                cancel
            );

            wrapper.append(
                header,
                track,
                description,
                meta,
                actions
            );

            this.elements = {
                wrapper,
                title,
                value,
                bar,
                description,
                elapsed,
                remaining,
                state,
                actions,
                cancel
            };

            return wrapper;
        }

        update(
            task =
                this.task
        ) {
            this.task =
                task;

            const {
                wrapper,
                title,
                value,
                bar,
                description,
                elapsed,
                remaining,
                state,
                actions,
                cancel
            } = this.elements;

            wrapper.dataset.state =
                task.state;

            wrapper.dataset.indeterminate =
                String(
                    task.indeterminate
                );

            wrapper.setAttribute(
                "aria-valuemin",
                String(
                    task.minimum
                )
            );

            wrapper.setAttribute(
                "aria-valuemax",
                String(
                    task.maximum
                )
            );

            if (
                task.indeterminate
            ) {
                wrapper.removeAttribute(
                    "aria-valuenow"
                );

                wrapper.setAttribute(
                    "aria-valuetext",
                    `${task.label}: indeterminate`
                );
            } else {
                wrapper.setAttribute(
                    "aria-valuenow",
                    String(
                        task.value
                    )
                );

                wrapper.setAttribute(
                    "aria-valuetext",
                    `${task.label}: ${Math.round(task.percent)}%`
                );
            }

            title.textContent =
                task.label;

            value.hidden =
                !this.options.showValue;

            value.textContent =
                task.indeterminate
                    ? task.state
                    : this.options.showPercent
                        ? `${Math.round(task.percent)}%`
                        : `${task.value} / ${task.maximum}`;

            bar.style.width =
                task.indeterminate
                    ? "36%"
                    : `${task.percent}%`;

            description.hidden =
                !this.options.showDescription ||
                !task.description;

            description.textContent =
                task.description;

            elapsed.hidden =
                !this.options.showElapsed;

            elapsed.textContent =
                `Elapsed: ${formatDuration(task.elapsed)}`;

            remaining.hidden =
                !this.options.showRemaining ||
                task.remaining ===
                    null;

            remaining.textContent =
                task.remaining ===
                    null
                    ? ""
                    : `Remaining: ${formatDuration(task.remaining)}`;

            state.textContent =
                `State: ${task.state}`;

            actions.hidden =
                !task.cancellable ||
                ![
                    "running",
                    "paused"
                ].includes(
                    task.state
                );

            cancel.disabled =
                actions.hidden;

            return this;
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.element.remove();

            this.destroyed =
                true;

            this.dispatchEvent(
                new CustomEvent(
                    "destroy"
                )
            );
        }
    }

    /*
    ==========================================================================
    Progress Coordinator
    ==========================================================================
    */

    class ProgressCoordinator
        extends EventTarget {
        constructor(
            context,
            options = {}
        ) {
            super();

            this.context =
                context;

            this.options = {
                ...DEFAULT_OPTIONS,
                ...options,

                historyLimit:
                    clampInteger(
                        options.historyLimit,
                        DEFAULT_OPTIONS.historyLimit,
                        10,
                        10000
                    )
            };

            this.tasks =
                new Map();

            this.history =
                [];

            this.views =
                new Map();

            this.ticker =
                0;

            this.destroyed =
                false;

            if (
                this.options.injectStyles
            ) {
                injectProgressStyles();
            }
        }

        /*
        ======================================================================
        Task Creation
        ======================================================================
        */

        create(
            id =
                makeID(),
            label =
                DEFAULT_OPTIONS.label,
            options = {}
        ) {
            const taskID =
                normalizeID(
                    id
                );

            if (
                this.tasks.has(
                    taskID
                )
            ) {
                throw new Error(
                    `Progress task already exists: ${taskID}`
                );
            }

            const minimum =
                parseNumber(
                    options.minimum,
                    DEFAULT_OPTIONS.minimum
                );

            const maximum =
                parseNumber(
                    options.maximum,
                    DEFAULT_OPTIONS.maximum
                );

            if (
                maximum <=
                minimum
            ) {
                throw new Error(
                    "Progress maximum must be greater than minimum."
                );
            }

            const now =
                performance.now();

            const value =
                clamp(
                    parseNumber(
                        options.value,
                        minimum
                    ),
                    minimum,
                    maximum
                );

            const task = {
                id:
                    taskID,

                label:
                    String(
                        label ||
                        taskID
                    ),

                description:
                    String(
                        options.description ||
                        ""
                    ),

                minimum,

                maximum,

                value,

                percent:
                    (
                        (
                            value -
                            minimum
                        ) /
                        (
                            maximum -
                            minimum
                        )
                    ) *
                    100,

                state:
                    normalizeState(
                        options.state ||
                        "running"
                    ),

                indeterminate:
                    parseBoolean(
                        options.indeterminate,
                        false
                    ),

                cancellable:
                    parseBoolean(
                        options.cancellable,
                        false
                    ),

                weight:
                    Math.max(
                        0,
                        parseNumber(
                            options.weight,
                            DEFAULT_OPTIONS.weight
                        )
                    ),

                startedAt:
                    now,

                updatedAt:
                    now,

                completedAt:
                    null,

                pausedAt:
                    null,

                pausedDuration:
                    0,

                elapsed:
                    0,

                remaining:
                    null,

                metadata:
                    options.metadata &&
                    typeof options.metadata ===
                    "object"
                        ? {
                            ...options.metadata
                        }
                        : {},

                error:
                    null,

                abortController:
                    options.abortController ||
                    null
            };

            this.tasks.set(
                taskID,
                task
            );

            this.ensureTicker();
            this.integrateLoadingBegin(
                task
            );
            this.emit(
                "create",
                task
            );
            this.updateAggregate();

            return task;
        }

        begin(
            id,
            label =
                id,
            options = {}
        ) {
            return this.create(
                id,
                label,
                {
                    ...options,
                    state:
                        "running"
                }
            );
        }

        /*
        ======================================================================
        Task Updates
        ======================================================================
        */

        get(
            id
        ) {
            return (
                this.tasks.get(
                    normalizeID(
                        id
                    )
                ) ||
                null
            );
        }

        set(
            id,
            value,
            options = {}
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                throw new Error(
                    `Unknown progress task: ${id}`
                );
            }

            if (
                [
                    "success",
                    "error",
                    "cancelled"
                ].includes(
                    task.state
                )
            ) {
                return task;
            }

            if (
                options.indeterminate !==
                undefined
            ) {
                task.indeterminate =
                    parseBoolean(
                        options.indeterminate,
                        task.indeterminate
                    );
            }

            if (
                value !==
                    undefined &&
                value !==
                    null
            ) {
                task.value =
                    clamp(
                        parseNumber(
                            value,
                            task.value
                        ),
                        task.minimum,
                        task.maximum
                    );

                task.indeterminate =
                    false;
            }

            if (
                options.label !==
                undefined
            ) {
                task.label =
                    String(
                        options.label
                    );
            }

            if (
                options.description !==
                undefined
            ) {
                task.description =
                    String(
                        options.description
                    );
            }

            if (
                options.state !==
                undefined
            ) {
                task.state =
                    normalizeState(
                        options.state
                    );
            }

            if (
                options.metadata &&
                typeof options.metadata ===
                "object"
            ) {
                task.metadata = {
                    ...task.metadata,
                    ...options.metadata
                };
            }

            task.percent =
                (
                    (
                        task.value -
                        task.minimum
                    ) /
                    (
                        task.maximum -
                        task.minimum
                    )
                ) *
                100;

            task.updatedAt =
                performance.now();

            this.updateTiming(
                task
            );

            if (
                task.value >=
                    task.maximum &&
                options.complete !==
                false
            ) {
                return this.complete(
                    id,
                    options.result
                );
            }

            this.updateView(
                task
            );
            this.integrateLoadingUpdate(
                task
            );
            this.emit(
                "update",
                task
            );
            this.updateAggregate();

            return task;
        }

        increment(
            id,
            amount = 1,
            options = {}
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                throw new Error(
                    `Unknown progress task: ${id}`
                );
            }

            return this.set(
                id,
                task.value +
                parseNumber(
                    amount,
                    1
                ),
                options
            );
        }

        pause(
            id
        ) {
            const task =
                this.get(
                    id
                );

            if (
                !task ||
                task.state !==
                    "running"
            ) {
                return false;
            }

            task.state =
                "paused";

            task.pausedAt =
                performance.now();

            task.updatedAt =
                task.pausedAt;

            this.updateTiming(
                task
            );
            this.updateView(
                task
            );
            this.emit(
                "pause",
                task
            );

            return true;
        }

        resume(
            id
        ) {
            const task =
                this.get(
                    id
                );

            if (
                !task ||
                task.state !==
                    "paused"
            ) {
                return false;
            }

            const now =
                performance.now();

            if (
                task.pausedAt !==
                null
            ) {
                task.pausedDuration +=
                    now -
                    task.pausedAt;
            }

            task.pausedAt =
                null;

            task.state =
                "running";

            task.updatedAt =
                now;

            this.updateTiming(
                task
            );
            this.updateView(
                task
            );
            this.emit(
                "resume",
                task
            );

            return true;
        }

        complete(
            id,
            result = null
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                return null;
            }

            task.value =
                task.maximum;

            task.percent =
                100;

            task.state =
                "success";

            task.completedAt =
                performance.now();

            task.updatedAt =
                task.completedAt;

            task.remaining =
                0;

            task.metadata = {
                ...task.metadata,
                result
            };

            this.updateTiming(
                task
            );
            this.updateView(
                task
            );
            this.integrateLoadingEnd(
                task
            );
            this.archive(
                task
            );
            this.emit(
                "complete",
                task
            );
            this.updateAggregate();

            return task;
        }

        fail(
            id,
            error
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                return null;
            }

            task.state =
                "error";

            task.completedAt =
                performance.now();

            task.updatedAt =
                task.completedAt;

            task.error =
                error instanceof
                Error
                    ? {
                        name:
                            error.name,

                        message:
                            error.message,

                        stack:
                            error.stack ||
                            null
                    }
                    : {
                        name:
                            "Error",

                        message:
                            String(error)
                    };

            this.updateTiming(
                task
            );
            this.updateView(
                task
            );
            this.integrateLoadingFail(
                task,
                error
            );
            this.archive(
                task
            );
            this.emit(
                "fail",
                task
            );
            this.updateAggregate();

            return task;
        }

        cancel(
            id,
            reason =
                "cancelled"
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                return null;
            }

            task.abortController?.
                abort?.();

            task.state =
                "cancelled";

            task.completedAt =
                performance.now();

            task.updatedAt =
                task.completedAt;

            task.metadata = {
                ...task.metadata,
                reason
            };

            this.updateTiming(
                task
            );
            this.updateView(
                task
            );
            this.integrateLoadingCancel(
                task
            );
            this.archive(
                task
            );
            this.emit(
                "cancel",
                task
            );
            this.updateAggregate();

            return task;
        }

        remove(
            id
        ) {
            const taskID =
                normalizeID(
                    id
                );

            const task =
                this.tasks.get(
                    taskID
                );

            if (!task) {
                return false;
            }

            this.tasks.delete(
                taskID
            );

            const view =
                this.views.get(
                    taskID
                );

            view?.
                destroy?.();

            this.views.delete(
                taskID
            );

            this.updateAggregate();

            return true;
        }

        clear(
            options = {}
        ) {
            const includeRunning =
                options.includeRunning ===
                true;

            const ids =
                [
                    ...this.tasks.values()
                ]
                    .filter(
                        task =>
                            includeRunning ||
                            ![
                                "running",
                                "paused"
                            ].includes(
                                task.state
                            )
                    )
                    .map(
                        task =>
                            task.id
                    );

            for (const id of ids) {
                this.remove(
                    id
                );
            }

            return ids.length;
        }

        /*
        ======================================================================
        Timing
        ======================================================================
        */

        updateTiming(
            task
        ) {
            const now =
                task.completedAt ??
                performance.now();

            let paused =
                task.pausedDuration;

            if (
                task.state ===
                    "paused" &&
                task.pausedAt !==
                    null
            ) {
                paused +=
                    now -
                    task.pausedAt;
            }

            task.elapsed =
                Math.max(
                    0,
                    now -
                    task.startedAt -
                    paused
                );

            task.remaining =
                task.indeterminate
                    ? null
                    : estimateRemaining(
                        task.minimum,
                        task.maximum,
                        task.value,
                        task.elapsed
                    );
        }

        ensureTicker() {
            if (this.ticker) {
                return;
            }

            const tick =
                () => {
                    if (this.destroyed) {
                        return;
                    }

                    let active =
                        false;

                    for (const task of this.tasks.values()) {
                        if (
                            [
                                "running",
                                "paused"
                            ].includes(
                                task.state
                            )
                        ) {
                            active =
                                true;

                            this.updateTiming(
                                task
                            );

                            this.updateView(
                                task
                            );
                        }
                    }

                    if (active) {
                        this.ticker =
                            window.requestAnimationFrame(
                                tick
                            );
                    } else {
                        this.ticker =
                            0;
                    }
                };

            this.ticker =
                window.requestAnimationFrame(
                    tick
                );
        }

        /*
        ======================================================================
        Views
        ======================================================================
        */

        createView(
            id,
            options = {}
        ) {
            const task =
                this.get(
                    id
                );

            if (!task) {
                throw new Error(
                    `Unknown progress task: ${id}`
                );
            }

            if (
                this.views.has(
                    task.id
                )
            ) {
                return this.views.get(
                    task.id
                );
            }

            const view =
                new ProgressView(
                    task,
                    {
                        ...this.options,
                        ...options
                    }
                );

            view.addEventListener(
                "cancel",
                () =>
                    this.cancel(
                        task.id,
                        "user"
                    )
            );

            this.views.set(
                task.id,
                view
            );

            return view;
        }

        updateView(
            task
        ) {
            this.views.get(
                task.id
            )?.
                update?.(
                    task
                );
        }

        renderList(
            options = {}
        ) {
            const container =
                document.createElement(
                    "div"
                );

            container.className =
                "terminal-progress-list";

            const tasks =
                [
                    ...this.tasks.values()
                ]
                    .filter(
                        task =>
                            options.states
                                ? options.states.includes(
                                    task.state
                                )
                                : true
                    )
                    .sort(
                        (
                            left,
                            right
                        ) =>
                            left.startedAt -
                            right.startedAt
                    );

            for (const task of tasks) {
                const view =
                    this.createView(
                        task.id,
                        options
                    );

                container.appendChild(
                    view.element
                );
            }

            container.controller =
                this;

            return container;
        }

        /*
        ======================================================================
        Aggregate Progress
        ======================================================================
        */

        aggregate() {
            const active =
                [
                    ...this.tasks.values()
                ].filter(
                    task =>
                        [
                            "running",
                            "paused"
                        ].includes(
                            task.state
                        )
                );

            const determinate =
                active.filter(
                    task =>
                        !task.indeterminate
                );

            const totalWeight =
                determinate.reduce(
                    (
                        total,
                        task
                    ) =>
                        total +
                        task.weight,
                    0
                );

            const percent =
                totalWeight >
                    0
                    ? determinate.reduce(
                        (
                            total,
                            task
                        ) =>
                            total +
                            task.percent *
                            task.weight,
                        0
                    ) /
                    totalWeight
                    : null;

            return {
                active:
                    active.length,

                determinate:
                    determinate.length,

                indeterminate:
                    active.length -
                    determinate.length,

                percent,

                tasks:
                    active.map(
                        serializeTask
                    )
            };
        }

        updateAggregate() {
            const aggregate =
                this.aggregate();

            this.context.root?.
                classList.toggle(
                    "terminal-has-progress",
                    aggregate.active >
                    0
                );

            this.emit(
                "aggregate",
                aggregate
            );

            return aggregate;
        }

        /*
        ======================================================================
        Loading Integration
        ======================================================================
        */

        integrateLoadingBegin(
            task
        ) {
            if (
                !this.options.integrateLoading ||
                !this.context.loading
            ) {
                return;
            }

            try {
                this.context.loading.begin(
                    `progress:${task.id}`,
                    task.label,
                    {
                        progress:
                            task.indeterminate
                                ? null
                                : task.percent,
                        metadata: {
                            source:
                                "progress"
                        }
                    }
                );
            } catch (error) {
                /*
                --------------------------------------------------------------
                Loading integration must not interrupt progress tracking.
                --------------------------------------------------------------
                */
            }
        }

        integrateLoadingUpdate(
            task
        ) {
            if (
                !this.options.integrateLoading ||
                !this.context.loading
            ) {
                return;
            }

            try {
                this.context.loading.setProgress(
                    `progress:${task.id}`,
                    task.indeterminate
                        ? null
                        : task.percent,
                    task.label
                );
            } catch (error) {
                /*
                --------------------------------------------------------------
                The loading task may have been removed independently.
                --------------------------------------------------------------
                */
            }
        }

        integrateLoadingEnd(
            task
        ) {
            if (
                !this.options.integrateLoading ||
                !this.context.loading
            ) {
                return;
            }

            this.context.loading.end?.(
                `progress:${task.id}`,
                task.metadata?.result
            );
        }

        integrateLoadingFail(
            task,
            error
        ) {
            if (
                !this.options.integrateLoading ||
                !this.context.loading
            ) {
                return;
            }

            this.context.loading.fail?.(
                `progress:${task.id}`,
                error
            );
        }

        integrateLoadingCancel(
            task
        ) {
            if (
                !this.options.integrateLoading ||
                !this.context.loading
            ) {
                return;
            }

            this.context.loading.cancel?.(
                `progress:${task.id}`
            );
        }

        /*
        ======================================================================
        History and Diagnostics
        ======================================================================
        */

        archive(
            task
        ) {
            const serialized =
                serializeTask(
                    task
                );

            this.history.push(
                serialized
            );

            this.history =
                this.history.slice(
                    -this.options.historyLimit
                );

            return serialized;
        }

        list(
            options = {}
        ) {
            const state =
                options.state
                    ? normalizeState(
                        options.state
                    )
                    : null;

            const activeOnly =
                options.active ===
                true;

            const records =
                [
                    ...this.tasks.values()
                ]
                    .filter(
                        task =>
                            (
                                !state ||
                                task.state ===
                                state
                            ) &&
                            (
                                !activeOnly ||
                                [
                                    "running",
                                    "paused"
                                ].includes(
                                    task.state
                                )
                            )
                    )
                    .map(
                        serializeTask
                    );

            return records;
        }

        status() {
            return {
                version:
                    VERSION,

                tasks:
                    this.tasks.size,

                views:
                    this.views.size,

                history:
                    this.history.length,

                aggregate:
                    this.aggregate(),

                integrateLoading:
                    this.options.integrateLoading
            };
        }

        export() {
            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                status:
                    this.status(),

                tasks:
                    this.list(),

                history:
                    [
                        ...this.history
                    ]
            };
        }

        emit(
            type,
            detail
        ) {
            const payload =
                detail &&
                detail.id
                    ? serializeTask(
                        detail
                    )
                    : detail;

            this.dispatchEvent(
                new CustomEvent(
                    type,
                    {
                        detail:
                            payload
                    }
                )
            );

            this.context.events?.emit?.(
                `progress:${type}`,
                payload
            );

            this.context.root?.
                dispatchEvent?.(
                    new CustomEvent(
                        `speciedex:terminal-progress-${type}`,
                        {
                            bubbles:
                                true,

                            detail:
                                payload
                        }
                    )
                );

            document.dispatchEvent(
                new CustomEvent(
                    `speciedex:terminal-progress-${type}`,
                    {
                        detail:
                            payload
                    }
                )
            );
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            if (this.ticker) {
                window.cancelAnimationFrame(
                    this.ticker
                );

                this.ticker =
                    0;
            }

            for (const view of this.views.values()) {
                view.destroy();
            }

            this.views.clear();
            this.tasks.clear();

            this.destroyed =
                true;

            this.dispatchEvent(
                new CustomEvent(
                    "destroy"
                )
            );
        }
    }

    /*
    ==========================================================================
    Legacy-Compatible Renderer
    ==========================================================================
    */

    function createProgress(
        label =
            "Progress",
        value =
            0,
        options = {}
    ) {
        const minimum =
            parseNumber(
                options.minimum,
                DEFAULT_OPTIONS.minimum
            );

        const maximum =
            parseNumber(
                options.maximum,
                DEFAULT_OPTIONS.maximum
            );

        const now =
            performance.now();

        const task = {
            id:
                String(
                    options.id ||
                    makeID()
                ),

            label:
                String(
                    label
                ),

            description:
                String(
                    options.description ||
                    ""
                ),

            minimum,

            maximum,

            value:
                clamp(
                    parseNumber(
                        value,
                        minimum
                    ),
                    minimum,
                    maximum
                ),

            percent:
                0,

            state:
                normalizeState(
                    options.state ||
                    "running"
                ),

            indeterminate:
                parseBoolean(
                    options.indeterminate,
                    false
                ),

            cancellable:
                parseBoolean(
                    options.cancellable,
                    false
                ),

            weight:
                Math.max(
                    0,
                    parseNumber(
                        options.weight,
                        1
                    )
                ),

            startedAt:
                now,

            updatedAt:
                now,

            completedAt:
                null,

            pausedAt:
                null,

            pausedDuration:
                0,

            elapsed:
                0,

            remaining:
                null,

            metadata:
                {},

            error:
                null
        };

        task.percent =
            (
                (
                    task.value -
                    task.minimum
                ) /
                (
                    task.maximum -
                    task.minimum
                )
            ) *
            100;

        const view =
            new ProgressView(
                task,
                options
            );

        const wrapper =
            view.element;

        wrapper.controller =
            view;

        wrapper.update =
            (
                next,
                updateOptions = {}
            ) => {
                task.value =
                    clamp(
                        parseNumber(
                            next,
                            task.value
                        ),
                        task.minimum,
                        task.maximum
                    );

                task.percent =
                    (
                        (
                            task.value -
                            task.minimum
                        ) /
                        (
                            task.maximum -
                            task.minimum
                        )
                    ) *
                    100;

                task.state =
                    updateOptions.state
                        ? normalizeState(
                            updateOptions.state
                        )
                        : task.value >=
                            task.maximum
                            ? "success"
                            : task.state;

                task.description =
                    updateOptions.description ??
                    task.description;

                task.indeterminate =
                    updateOptions.indeterminate ??
                    task.indeterminate;

                task.updatedAt =
                    performance.now();

                task.elapsed =
                    task.updatedAt -
                    task.startedAt;

                task.remaining =
                    estimateRemaining(
                        task.minimum,
                        task.maximum,
                        task.value,
                        task.elapsed
                    );

                if (
                    task.value >=
                    task.maximum
                ) {
                    task.completedAt =
                        task.updatedAt;
                }

                view.update(
                    task
                );

                return wrapper;
            };

        wrapper.setState =
            state => {
                task.state =
                    normalizeState(
                        state
                    );

                view.update(
                    task
                );

                return wrapper;
            };

        wrapper.destroy =
            () =>
                view.destroy();

        return wrapper;
    }

    /*
    ==========================================================================
    Initialization
    ==========================================================================
    */

    function initialize(
        context
    ) {
        if (
            context.progress instanceof
            ProgressCoordinator
        ) {
            return context.progress;
        }

        const coordinator =
            new ProgressCoordinator(
                context,
                {
                    historyLimit:
                        context.root?.
                            dataset.
                            terminalProgressHistoryLimit,

                    integrateLoading:
                        parseBoolean(
                            context.root?.
                                dataset.
                                terminalProgressLoading,
                            true
                        ),

                    injectStyles:
                        parseBoolean(
                            context.root?.
                                dataset.
                                terminalProgressInjectStyles,
                            true
                        )
                }
            );

        context.progress =
            coordinator;

        context.createProgress =
            createProgress;

        context.registerService?.(
            "progress",
            coordinator
        );

        context.registerRenderer?.(
            "progress",
            {
                create:
                    createProgress,

                render:
                    createProgress,

                Coordinator:
                    ProgressCoordinator,

                View:
                    ProgressView
            }
        );

        return coordinator;
    }

    /*
    ==========================================================================
    Commands
    ==========================================================================
    */

    const commands =
        [
            {
                name:
                    "progress",

                category:
                    "system",

                description:
                    "Display progress coordinator status.",

                usage:
                    "progress",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.progress.status()
                    )
            },

            {
                name:
                    "progress-list",

                category:
                    "system",

                description:
                    "List progress tasks.",

                usage:
                    "progress-list [state]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.progress.list({
                            state:
                                args[0] ||
                                null
                        })
                    )
            },

            {
                name:
                    "progress-begin",

                category:
                    "system",

                description:
                    "Create a progress task.",

                usage:
                    "progress-begin <id> [label] [--max N] [--indeterminate] [--cancellable]",

                handler: ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const id =
                        args.shift();

                    if (!id) {
                        throw new Error(
                            "A progress task ID is required."
                        );
                    }

                    const label =
                        args.join(
                            " "
                        ) ||
                        id;

                    return writeJSON(
                        serializeTask(
                            context.progress.begin(
                                id,
                                label,
                                {
                                    maximum:
                                        parsed.options.max ||
                                        parsed.options.maximum ||
                                        100,

                                    minimum:
                                        parsed.options.min ||
                                        parsed.options.minimum ||
                                        0,

                                    indeterminate:
                                        parsed.flags.indeterminate ===
                                        true,

                                    cancellable:
                                        parsed.flags.cancellable ===
                                        true,

                                    description:
                                        parsed.options.description ||
                                        ""
                                }
                            )
                        )
                    );
                }
            },

            {
                name:
                    "progress-set",

                category:
                    "system",

                description:
                    "Set progress task value.",

                usage:
                    "progress-set <id> <value> [label]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const id =
                        args.shift();

                    const value =
                        args.shift();

                    if (
                        !id ||
                        value ===
                        undefined
                    ) {
                        throw new Error(
                            "Usage: progress-set <id> <value> [label]"
                        );
                    }

                    return writeJSON(
                        serializeTask(
                            context.progress.set(
                                id,
                                value,
                                {
                                    label:
                                        args.join(
                                            " "
                                        ) ||
                                        undefined
                                }
                            )
                        )
                    );
                }
            },

            {
                name:
                    "progress-increment",

                category:
                    "system",

                description:
                    "Increment a progress task.",

                usage:
                    "progress-increment <id> [amount]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const id =
                        args[0];

                    if (!id) {
                        throw new Error(
                            "A progress task ID is required."
                        );
                    }

                    return writeJSON(
                        serializeTask(
                            context.progress.increment(
                                id,
                                args[1] ||
                                1
                            )
                        )
                    );
                }
            },

            {
                name:
                    "progress-complete",

                category:
                    "system",

                description:
                    "Complete a progress task.",

                usage:
                    "progress-complete <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const task =
                        context.progress.complete(
                            args[0]
                        );

                    if (!task) {
                        throw new Error(
                            `Unknown progress task: ${args[0]}`
                        );
                    }

                    return writeJSON(
                        serializeTask(
                            task
                        )
                    );
                }
            },

            {
                name:
                    "progress-fail",

                category:
                    "system",

                description:
                    "Fail a progress task.",

                usage:
                    "progress-fail <id> <message>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const id =
                        args.shift();

                    if (!id) {
                        throw new Error(
                            "A progress task ID is required."
                        );
                    }

                    const task =
                        context.progress.fail(
                            id,
                            new Error(
                                args.join(
                                    " "
                                ) ||
                                "Progress task failed."
                            )
                        );

                    if (!task) {
                        throw new Error(
                            `Unknown progress task: ${id}`
                        );
                    }

                    return writeJSON(
                        serializeTask(
                            task
                        )
                    );
                }
            },

            {
                name:
                    "progress-cancel",

                category:
                    "system",

                description:
                    "Cancel a progress task.",

                usage:
                    "progress-cancel <id>",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const task =
                        context.progress.cancel(
                            args[0],
                            "command"
                        );

                    if (!task) {
                        throw new Error(
                            `Unknown progress task: ${args[0]}`
                        );
                    }

                    return writeJSON(
                        serializeTask(
                            task
                        )
                    );
                }
            },

            {
                name:
                    "progress-render",

                category:
                    "system",

                description:
                    "Render active progress tasks in the terminal.",

                usage:
                    "progress-render",

                handler: ({
                    context
                }) =>
                    context.progress.renderList()
            },

            {
                name:
                    "progress-demo",

                category:
                    "system",

                description:
                    "Run a progress demonstration.",

                usage:
                    "progress-demo [seconds]",

                handler: async ({
                    args,
                    context,
                    write
                }) => {
                    const seconds =
                        clamp(
                            parseNumber(
                                args[0],
                                5
                            ),
                            1,
                            60
                        );

                    const id =
                        `demo:${Date.now()}`;

                    context.progress.begin(
                        id,
                        "Speciedex progress demonstration",
                        {
                            maximum:
                                100,
                            cancellable:
                                true,
                            description:
                                "Demonstrating coordinated progress and loading state."
                        }
                    );

                    const started =
                        performance.now();

                    while (
                        performance.now() -
                        started <
                        seconds *
                        1000
                    ) {
                        const task =
                            context.progress.get(
                                id
                            );

                        if (
                            !task ||
                            task.state ===
                                "cancelled"
                        ) {
                            return write(
                                "Progress demonstration cancelled.",
                                "warning"
                            );
                        }

                        const elapsed =
                            performance.now() -
                            started;

                        context.progress.set(
                            id,
                            clamp(
                                (
                                    elapsed /
                                    (
                                        seconds *
                                        1000
                                    )
                                ) *
                                100,
                                0,
                                100
                            ),
                            {
                                complete:
                                    false
                            }
                        );

                        await new Promise(
                            resolve =>
                                window.setTimeout(
                                    resolve,
                                    80
                                )
                        );
                    }

                    context.progress.complete(
                        id
                    );

                    return write(
                        "Progress demonstration complete.",
                        "success"
                    );
                }
            },

            {
                name:
                    "progress-export",

                category:
                    "system",

                description:
                    "Export progress tasks and history as JSON.",

                usage:
                    "progress-export [filename]",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const filename =
                        args[0] ||
                        "speciedex-terminal-progress.json";

                    const payload =
                        JSON.stringify(
                            context.progress.export(),
                            null,
                            2
                        );

                    const blob =
                        new Blob(
                            [
                                payload
                            ],
                            {
                                type:
                                    "application/json"
                            }
                        );

                    const url =
                        URL.createObjectURL(
                            blob
                        );

                    const anchor =
                        document.createElement(
                            "a"
                        );

                    anchor.href =
                        url;

                    anchor.download =
                        filename;

                    anchor.click();

                    window.setTimeout(
                        () =>
                            URL.revokeObjectURL(
                                url
                            ),
                        1000
                    );

                    return write(
                        `Progress exported to ${filename}.`,
                        "success"
                    );
                }
            }
        ];

    /*
    ==========================================================================
    Public Module API
    ==========================================================================
    */

    const api =
        Object.freeze({
            name:
                MODULE_NAME,

            version:
                VERSION,

            PRIMARY_COLOR,
            ACCENT_COLOR,
            DEFAULT_OPTIONS,
            STATES,

            ProgressView,
            ProgressCoordinator,

            clamp,
            parseNumber,
            parseBoolean,
            normalizeState,
            normalizeID,
            formatDuration,
            estimateRemaining,
            serializeTask,
            injectProgressStyles,

            createProgress,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalProgress =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules ||
        {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] =
        api;

    document.dispatchEvent(
        new CustomEvent(
            "speciedex:terminal-module-available",
            {
                detail: {
                    name:
                        MODULE_NAME,

                    module:
                        api
                }
            }
        )
    );
})(window, document);
