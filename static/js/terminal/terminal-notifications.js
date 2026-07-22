/*
========================================================================
Speciedex.org
Terminal Notifications
========================================================================

Accessible notification and toast service for SpeciedexTerminal.

Provides:

    • stacked notifications
    • info, success, warning, error, critical, and system levels
    • priorities
    • optional persistence
    • automatic timeout and progress bars
    • pause on hover and focus
    • deduplication
    • actions
    • manual dismissal
    • notification history
    • filtering and counters
    • export
    • root, document, and event-bus propagation
    • terminal commands
    • clean teardown

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME =
        "Notifications";

    const VERSION =
        "2.0.0";

    const LEVELS =
        Object.freeze([
            "info",
            "success",
            "warning",
            "error",
            "critical",
            "system"
        ]);

    const PRIORITIES =
        Object.freeze([
            "low",
            "normal",
            "high",
            "urgent"
        ]);

    const DEFAULT_OPTIONS =
        Object.freeze({
            timeout:
                4000,

            maximumVisible:
                5,

            maximumHistory:
                500,

            position:
                "top-right",

            pauseOnHover:
                true,

            pauseOnFocus:
                true,

            dismissible:
                true,

            showProgress:
                true,

            deduplicate:
                true,

            deduplicateWindow:
                2500,

            persist:
                false,

            announce:
                true,

            injectStyles:
                true
        });

    /*
    ==========================================================================
    Utilities
    ==========================================================================
    */

    function normalizeType(
        value
    ) {
        const normalized =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase();

        if (
            normalized ===
            "warn"
        ) {
            return "warning";
        }

        if (
            normalized ===
            "fatal"
        ) {
            return "critical";
        }

        return LEVELS.includes(
            normalized
        )
            ? normalized
            : "info";
    }

    function normalizePriority(
        value
    ) {
        const normalized =
            String(
                value ?? ""
            )
                .trim()
                .toLowerCase();

        return PRIORITIES.includes(
            normalized
        )
            ? normalized
            : "normal";
    }

    function clampInteger(
        value,
        fallback,
        minimum,
        maximum
    ) {
        const parsed =
            Number.parseInt(
                value,
                10
            );

        if (!Number.isFinite(parsed)) {
            return fallback;
        }

        return Math.min(
            maximum,
            Math.max(
                minimum,
                parsed
            )
        );
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

    function safeStorage() {
        try {
            const key =
                "__speciedex_notifications_probe__";

            window.localStorage.setItem(
                key,
                key
            );

            window.localStorage.removeItem(
                key
            );

            return window.localStorage;
        } catch (error) {
            return null;
        }
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
            `notification:${Date.now()}:` +
            Math.random()
                .toString(16)
                .slice(2)
        );
    }

    function normalizeActions(
        actions
    ) {
        if (!Array.isArray(actions)) {
            return [];
        }

        return actions
            .map(
                (
                    action,
                    index
                ) => {
                    if (
                        typeof action ===
                        "string"
                    ) {
                        return {
                            id:
                                `action:${index}`,

                            label:
                                action,

                            value:
                                action,

                            close:
                                true,

                            handler:
                                null
                        };
                    }

                    if (
                        !action ||
                        typeof action !==
                        "object"
                    ) {
                        return null;
                    }

                    return {
                        id:
                            String(
                                action.id ||
                                `action:${index}`
                            ),

                        label:
                            String(
                                action.label ||
                                action.title ||
                                action.id ||
                                `Action ${index + 1}`
                            ),

                        value:
                            action.value ??
                            action.id ??
                            index,

                        close:
                            action.close !==
                            false,

                        className:
                            String(
                                action.className ||
                                ""
                            ),

                        handler:
                            typeof action.handler ===
                            "function"
                                ? action.handler
                                : null
                    };
                }
            )
            .filter(Boolean);
    }

    function serializeItem(
        item
    ) {
        return {
            id:
                item.id,

            message:
                item.message,

            title:
                item.title,

            type:
                item.type,

            priority:
                item.priority,

            createdAt:
                item.createdAt,

            updatedAt:
                item.updatedAt,

            timeout:
                item.timeout,

            persistent:
                item.persistent,

            count:
                item.count,

            dismissed:
                item.dismissed,

            dismissedAt:
                item.dismissedAt,

            reason:
                item.reason,

            metadata:
                item.metadata,

            actions:
                item.actions.map(
                    action => ({
                        id:
                            action.id,

                        label:
                            action.label,

                        value:
                            action.value,

                        close:
                            action.close,

                        className:
                            action.className
                    })
                )
        };
    }

    /*
    ==========================================================================
    Styles
    ==========================================================================
    */

    function injectNotificationStyles() {
        if (
            document.getElementById(
                "speciedex-terminal-notification-styles"
            )
        ) {
            return;
        }

        const style =
            document.createElement(
                "style"
            );

        style.id =
            "speciedex-terminal-notification-styles";

        style.textContent = `
            .terminal-notification-center {
                position: fixed;
                z-index: 12000;
                display: grid;
                width: min(26rem, calc(100vw - 2rem));
                gap: 0.65rem;
                pointer-events: none;
                font-family:
                    "IBM Plex Mono",
                    ui-monospace,
                    SFMono-Regular,
                    Consolas,
                    monospace;
            }

            .terminal-notification-center[data-position="top-right"] {
                top: 1rem;
                right: 1rem;
            }

            .terminal-notification-center[data-position="top-left"] {
                top: 1rem;
                left: 1rem;
            }

            .terminal-notification-center[data-position="bottom-right"] {
                right: 1rem;
                bottom: 1rem;
            }

            .terminal-notification-center[data-position="bottom-left"] {
                left: 1rem;
                bottom: 1rem;
            }

            .terminal-notification {
                --notification-accent: #c0d674;
                position: relative;
                display: grid;
                grid-template-columns: auto minmax(0, 1fr) auto;
                gap: 0.75rem;
                align-items: start;
                overflow: hidden;
                padding: 0.85rem 0.9rem 0.72rem;
                border: 1px solid color-mix(
                    in srgb,
                    var(--notification-accent) 42%,
                    transparent
                );
                background:
                    linear-gradient(
                        90deg,
                        color-mix(
                            in srgb,
                            var(--notification-accent) 8%,
                            transparent
                        ),
                        transparent 35%
                    ),
                    rgba(4, 10, 6, 0.97);
                color: #d8e6db;
                box-shadow:
                    0 0.75rem 2rem rgba(0, 0, 0, 0.42),
                    0 0 1rem color-mix(
                        in srgb,
                        var(--notification-accent) 10%,
                        transparent
                    );
                pointer-events: auto;
                transform: translateX(0);
                opacity: 1;
                transition:
                    transform 180ms ease,
                    opacity 180ms ease;
            }

            .terminal-notification[data-entering="true"] {
                transform: translateX(1rem);
                opacity: 0;
            }

            .terminal-notification[data-leaving="true"] {
                transform: translateX(1rem);
                opacity: 0;
            }

            .terminal-notification-info {
                --notification-accent: #7fc8ff;
            }

            .terminal-notification-success {
                --notification-accent: #c0d674;
            }

            .terminal-notification-warning {
                --notification-accent: #e6a42b;
            }

            .terminal-notification-error {
                --notification-accent: #ff7d73;
            }

            .terminal-notification-critical {
                --notification-accent: #ff3b30;
            }

            .terminal-notification-system {
                --notification-accent: #c7a7ff;
            }

            .terminal-notification-indicator {
                width: 0.55rem;
                height: 0.55rem;
                margin-top: 0.24rem;
                border-radius: 50%;
                background: var(--notification-accent);
                box-shadow:
                    0 0 0.7rem var(--notification-accent);
            }

            .terminal-notification-content {
                min-width: 0;
            }

            .terminal-notification-title {
                margin: 0 0 0.22rem;
                color: var(--notification-accent);
                font-size: 0.78rem;
                letter-spacing: 0.05em;
                text-transform: uppercase;
            }

            .terminal-notification-message {
                margin: 0;
                overflow-wrap: anywhere;
                font-size: 0.76rem;
                line-height: 1.5;
            }

            .terminal-notification-meta {
                display: flex;
                flex-wrap: wrap;
                gap: 0.45rem;
                margin-top: 0.46rem;
                color: rgba(216, 230, 219, 0.58);
                font-size: 0.64rem;
            }

            .terminal-notification-count {
                display: inline-grid;
                min-width: 1.25rem;
                height: 1.25rem;
                place-items: center;
                border: 1px solid color-mix(
                    in srgb,
                    var(--notification-accent) 48%,
                    transparent
                );
                border-radius: 999px;
                color: var(--notification-accent);
                font-size: 0.62rem;
            }

            .terminal-notification-dismiss {
                border: 0;
                background: transparent;
                color: rgba(216, 230, 219, 0.68);
                font: inherit;
                font-size: 0.85rem;
                line-height: 1;
                cursor: pointer;
            }

            .terminal-notification-dismiss:hover,
            .terminal-notification-dismiss:focus-visible {
                color: var(--notification-accent);
                outline: none;
            }

            .terminal-notification-actions {
                grid-column: 2 / -1;
                display: flex;
                flex-wrap: wrap;
                gap: 0.4rem;
            }

            .terminal-notification-action {
                border: 1px solid color-mix(
                    in srgb,
                    var(--notification-accent) 40%,
                    transparent
                );
                background: rgba(4, 10, 6, 0.72);
                color: var(--notification-accent);
                padding: 0.28rem 0.48rem;
                font: inherit;
                font-size: 0.65rem;
                cursor: pointer;
            }

            .terminal-notification-action:hover,
            .terminal-notification-action:focus-visible {
                background: color-mix(
                    in srgb,
                    var(--notification-accent) 12%,
                    rgba(4, 10, 6, 0.72)
                );
                outline: none;
            }

            .terminal-notification-progress {
                position: absolute;
                right: 0;
                bottom: 0;
                left: 0;
                height: 2px;
                background: rgba(216, 230, 219, 0.08);
            }

            .terminal-notification-progress-bar {
                display: block;
                width: 100%;
                height: 100%;
                transform-origin: left center;
                background: var(--notification-accent);
                box-shadow: 0 0 0.55rem var(--notification-accent);
            }

            @media (max-width: 640px) {
                .terminal-notification-center {
                    top: auto !important;
                    right: 0.5rem !important;
                    bottom: 0.5rem !important;
                    left: 0.5rem !important;
                    width: auto;
                }
            }

            @media (prefers-reduced-motion: reduce) {
                .terminal-notification {
                    transition: none;
                }
            }
        `;

        document.head.appendChild(
            style
        );
    }

    /*
    ==========================================================================
    Notification Center
    ==========================================================================
    */

    class NotificationCenter
        extends EventTarget {
        constructor(
            context,
            options = {}
        ) {
            super();

            this.context =
                context;

            this.options = {
                timeout:
                    clampInteger(
                        options.timeout,
                        DEFAULT_OPTIONS.timeout,
                        0,
                        600000
                    ),

                maximumVisible:
                    clampInteger(
                        options.maximumVisible,
                        DEFAULT_OPTIONS.maximumVisible,
                        1,
                        50
                    ),

                maximumHistory:
                    clampInteger(
                        options.maximumHistory,
                        DEFAULT_OPTIONS.maximumHistory,
                        10,
                        10000
                    ),

                position:
                    String(
                        options.position ||
                        DEFAULT_OPTIONS.position
                    ),

                pauseOnHover:
                    parseBoolean(
                        options.pauseOnHover,
                        DEFAULT_OPTIONS.pauseOnHover
                    ),

                pauseOnFocus:
                    parseBoolean(
                        options.pauseOnFocus,
                        DEFAULT_OPTIONS.pauseOnFocus
                    ),

                dismissible:
                    parseBoolean(
                        options.dismissible,
                        DEFAULT_OPTIONS.dismissible
                    ),

                showProgress:
                    parseBoolean(
                        options.showProgress,
                        DEFAULT_OPTIONS.showProgress
                    ),

                deduplicate:
                    parseBoolean(
                        options.deduplicate,
                        DEFAULT_OPTIONS.deduplicate
                    ),

                deduplicateWindow:
                    clampInteger(
                        options.deduplicateWindow,
                        DEFAULT_OPTIONS.deduplicateWindow,
                        0,
                        600000
                    ),

                persist:
                    parseBoolean(
                        options.persist,
                        DEFAULT_OPTIONS.persist
                    ),

                announce:
                    parseBoolean(
                        options.announce,
                        DEFAULT_OPTIONS.announce
                    ),

                injectStyles:
                    parseBoolean(
                        options.injectStyles,
                        DEFAULT_OPTIONS.injectStyles
                    )
            };

            this.items =
                [];

            this.visible =
                new Map();

            this.nodes =
                new Map();

            this.timers =
                new Map();

            this.container =
                null;

            this.storage =
                safeStorage();

            this.storageKey =
                `speciedex-terminal:notifications:${
                    context.root?.
                        dataset.
                        terminalInstance ||
                    "default"
                }`;

            this.destroyed =
                false;

            if (
                this.options.injectStyles
            ) {
                injectNotificationStyles();
            }

            this.mount();

            if (
                this.options.persist
            ) {
                this.restore();
            }
        }

        /*
        ======================================================================
        Mount
        ======================================================================
        */

        mount() {
            const existing =
                this.context.root?.
                    querySelector?.(
                        "[data-terminal-notification-center]"
                    );

            if (existing) {
                this.container =
                    existing;

                return existing;
            }

            const container =
                document.createElement(
                    "aside"
                );

            container.className =
                "terminal-notification-center";

            container.dataset.terminalNotificationCenter =
                "";

            container.dataset.position =
                this.options.position;

            container.setAttribute(
                "aria-label",
                "SpeciedexTerminal notifications"
            );

            container.setAttribute(
                "aria-live",
                this.options.announce
                    ? "polite"
                    : "off"
            );

            document.body.appendChild(
                container
            );

            this.container =
                container;

            return container;
        }

        /*
        ======================================================================
        Notification Creation
        ======================================================================
        */

        findDuplicate(
            message,
            type
        ) {
            if (
                !this.options.deduplicate
            ) {
                return null;
            }

            const threshold =
                Date.now() -
                this.options.deduplicateWindow;

            for (
                let index =
                    this.items.length -
                    1;
                index >=
                    0;
                index -=
                    1
            ) {
                const item =
                    this.items[
                        index
                    ];

                if (
                    Date.parse(
                        item.createdAt
                    ) <
                    threshold
                ) {
                    break;
                }

                if (
                    item.message ===
                        message &&
                    item.type ===
                        type &&
                    !item.dismissed
                ) {
                    return item;
                }
            }

            return null;
        }

        notify(
            message,
            type =
                "info",
            timeout =
                this.options.timeout,
            options = {}
        ) {
            if (this.destroyed) {
                throw new Error(
                    "NotificationCenter has been destroyed."
                );
            }

            const normalizedType =
                normalizeType(
                    type
                );

            const normalizedMessage =
                String(
                    message ?? ""
                ).trim();

            if (!normalizedMessage) {
                throw new Error(
                    "Notification message is required."
                );
            }

            const duplicate =
                this.findDuplicate(
                    normalizedMessage,
                    normalizedType
                );

            if (duplicate) {
                duplicate.count +=
                    1;

                duplicate.updatedAt =
                    new Date().toISOString();

                this.updateNode(
                    duplicate
                );

                this.restartTimer(
                    duplicate
                );

                this.emit(
                    "duplicate",
                    duplicate
                );

                return duplicate;
            }

            const normalizedTimeout =
                options.persistent ===
                    true ||
                timeout ===
                    null
                    ? 0
                    : clampInteger(
                        timeout,
                        this.options.timeout,
                        0,
                        600000
                    );

            const item = {
                id:
                    String(
                        options.id ||
                        makeID()
                    ),

                message:
                    normalizedMessage,

                title:
                    String(
                        options.title ||
                        ""
                    ),

                type:
                    normalizedType,

                priority:
                    normalizePriority(
                        options.priority
                    ),

                createdAt:
                    new Date().toISOString(),

                updatedAt:
                    null,

                timeout:
                    normalizedTimeout,

                persistent:
                    options.persistent ===
                        true ||
                    normalizedTimeout ===
                        0,

                dismissible:
                    options.dismissible ??
                    this.options.dismissible,

                showProgress:
                    options.showProgress ??
                    this.options.showProgress,

                metadata:
                    options.metadata &&
                    typeof options.metadata ===
                    "object"
                        ? {
                            ...options.metadata
                        }
                        : {},

                actions:
                    normalizeActions(
                        options.actions
                    ),

                count:
                    1,

                dismissed:
                    false,

                dismissedAt:
                    null,

                reason:
                    null,

                paused:
                    false,

                remaining:
                    normalizedTimeout,

                timerStartedAt:
                    null
            };

            this.items.push(
                item
            );

            this.items =
                this.items.slice(
                    -this.options.maximumHistory
                );

            this.visible.set(
                item.id,
                item
            );

            this.enforceVisibleLimit();
            this.renderItem(
                item
            );
            this.startTimer(
                item
            );
            this.persist();
            this.emit(
                "notify",
                item
            );

            return item;
        }

        info(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "info",
                options.timeout ??
                this.options.timeout,
                options
            );
        }

        success(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "success",
                options.timeout ??
                this.options.timeout,
                options
            );
        }

        warning(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "warning",
                options.timeout ??
                this.options.timeout,
                options
            );
        }

        warn(
            message,
            options = {}
        ) {
            return this.warning(
                message,
                options
            );
        }

        error(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "error",
                options.timeout ??
                Math.max(
                    this.options.timeout,
                    6500
                ),
                options
            );
        }

        critical(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "critical",
                options.timeout ??
                0,
                {
                    ...options,
                    persistent:
                        options.persistent ??
                        true,
                    priority:
                        options.priority ||
                        "urgent"
                }
            );
        }

        system(
            message,
            options = {}
        ) {
            return this.notify(
                message,
                "system",
                options.timeout ??
                this.options.timeout,
                options
            );
        }

        /*
        ======================================================================
        Rendering
        ======================================================================
        */

        renderItem(
            item
        ) {
            if (
                !this.container ||
                this.nodes.has(
                    item.id
                )
            ) {
                return;
            }

            const node =
                document.createElement(
                    "article"
                );

            node.className =
                `terminal-notification terminal-notification-${item.type}`;

            node.dataset.notificationId =
                item.id;

            node.dataset.priority =
                item.priority;

            node.dataset.entering =
                "true";

            node.setAttribute(
                "role",
                [
                    "error",
                    "critical"
                ].includes(
                    item.type
                )
                    ? "alert"
                    : "status"
            );

            node.setAttribute(
                "aria-atomic",
                "true"
            );

            const indicator =
                document.createElement(
                    "span"
                );

            indicator.className =
                "terminal-notification-indicator";

            indicator.setAttribute(
                "aria-hidden",
                "true"
            );

            const content =
                document.createElement(
                    "div"
                );

            content.className =
                "terminal-notification-content";

            const title =
                document.createElement(
                    "h4"
                );

            title.className =
                "terminal-notification-title";

            title.dataset.notificationTitle =
                "";

            title.textContent =
                item.title ||
                item.type;

            const message =
                document.createElement(
                    "p"
                );

            message.className =
                "terminal-notification-message";

            message.dataset.notificationMessage =
                "";

            message.textContent =
                item.message;

            const meta =
                document.createElement(
                    "div"
                );

            meta.className =
                "terminal-notification-meta";

            const time =
                document.createElement(
                    "time"
                );

            time.dateTime =
                item.createdAt;

            time.textContent =
                new Date(
                    item.createdAt
                ).toLocaleTimeString();

            const priority =
                document.createElement(
                    "span"
                );

            priority.textContent =
                `priority: ${item.priority}`;

            const count =
                document.createElement(
                    "span"
                );

            count.className =
                "terminal-notification-count";

            count.dataset.notificationCount =
                "";

            count.textContent =
                String(
                    item.count
                );

            count.hidden =
                item.count <=
                1;

            meta.append(
                time,
                priority,
                count
            );

            content.append(
                title,
                message,
                meta
            );

            const dismiss =
                document.createElement(
                    "button"
                );

            dismiss.type =
                "button";

            dismiss.className =
                "terminal-notification-dismiss";

            dismiss.dataset.notificationDismiss =
                "";

            dismiss.setAttribute(
                "aria-label",
                "Dismiss notification"
            );

            dismiss.textContent =
                "×";

            dismiss.hidden =
                !item.dismissible;

            dismiss.addEventListener(
                "click",
                () =>
                    this.dismiss(
                        item.id,
                        "manual"
                    )
            );

            node.append(
                indicator,
                content,
                dismiss
            );

            if (
                item.actions.length
            ) {
                const actions =
                    document.createElement(
                        "div"
                    );

                actions.className =
                    "terminal-notification-actions";

                for (
                    const action of
                    item.actions
                ) {
                    const button =
                        document.createElement(
                            "button"
                        );

                    button.type =
                        "button";

                    button.className =
                        [
                            "terminal-notification-action",
                            action.className
                        ]
                            .filter(Boolean)
                            .join(" ");

                    button.textContent =
                        action.label;

                    button.addEventListener(
                        "click",
                        () =>
                            this.runAction(
                                item,
                                action
                            )
                    );

                    actions.appendChild(
                        button
                    );
                }

                node.appendChild(
                    actions
                );
            }

            if (
                item.showProgress &&
                item.timeout >
                    0
            ) {
                const progress =
                    document.createElement(
                        "div"
                    );

                progress.className =
                    "terminal-notification-progress";

                const bar =
                    document.createElement(
                        "span"
                    );

                bar.className =
                    "terminal-notification-progress-bar";

                bar.dataset.notificationProgress =
                    "";

                progress.appendChild(
                    bar
                );

                node.appendChild(
                    progress
                );
            }

            if (
                this.options.pauseOnHover
            ) {
                node.addEventListener(
                    "pointerenter",
                    () =>
                        this.pause(
                            item.id
                        )
                );

                node.addEventListener(
                    "pointerleave",
                    () =>
                        this.resume(
                            item.id
                        )
                );
            }

            if (
                this.options.pauseOnFocus
            ) {
                node.addEventListener(
                    "focusin",
                    () =>
                        this.pause(
                            item.id
                        )
                );

                node.addEventListener(
                    "focusout",
                    event => {
                        if (
                            !node.contains(
                                event.relatedTarget
                            )
                        ) {
                            this.resume(
                                item.id
                            );
                        }
                    }
                );
            }

            this.container.appendChild(
                node
            );

            this.nodes.set(
                item.id,
                node
            );

            window.requestAnimationFrame(
                () => {
                    node.dataset.entering =
                        "false";
                }
            );

            this.updateNode(
                item
            );
        }

        updateNode(
            item
        ) {
            const node =
                this.nodes.get(
                    item.id
                );

            if (!node) {
                return;
            }

            const message =
                node.querySelector(
                    "[data-notification-message]"
                );

            const title =
                node.querySelector(
                    "[data-notification-title]"
                );

            const count =
                node.querySelector(
                    "[data-notification-count]"
                );

            if (message) {
                message.textContent =
                    item.message;
            }

            if (title) {
                title.textContent =
                    item.title ||
                    item.type;
            }

            if (count) {
                count.textContent =
                    String(
                        item.count
                    );

                count.hidden =
                    item.count <=
                    1;
            }
        }

        /*
        ======================================================================
        Timing
        ======================================================================
        */

        startTimer(
            item
        ) {
            if (
                item.persistent ||
                item.timeout <=
                    0 ||
                item.paused
            ) {
                return;
            }

            this.stopTimer(
                item.id
            );

            item.timerStartedAt =
                performance.now();

            const timer =
                window.setTimeout(
                    () =>
                        this.dismiss(
                            item.id,
                            "timeout"
                        ),
                    item.remaining
                );

            this.timers.set(
                item.id,
                timer
            );

            this.animateProgress(
                item
            );
        }

        stopTimer(
            id
        ) {
            const timer =
                this.timers.get(
                    id
                );

            if (timer) {
                window.clearTimeout(
                    timer
                );
            }

            this.timers.delete(
                id
            );
        }

        restartTimer(
            item
        ) {
            item.remaining =
                item.timeout;

            item.paused =
                false;

            this.startTimer(
                item
            );
        }

        pause(
            id
        ) {
            const item =
                this.visible.get(
                    id
                );

            if (
                !item ||
                item.paused ||
                item.persistent ||
                item.timeout <=
                    0
            ) {
                return false;
            }

            item.paused =
                true;

            if (
                item.timerStartedAt !==
                null
            ) {
                item.remaining =
                    Math.max(
                        0,
                        item.remaining -
                        (
                            performance.now() -
                            item.timerStartedAt
                        )
                    );
            }

            this.stopTimer(
                id
            );

            return true;
        }

        resume(
            id
        ) {
            const item =
                this.visible.get(
                    id
                );

            if (
                !item ||
                !item.paused
            ) {
                return false;
            }

            item.paused =
                false;

            this.startTimer(
                item
            );

            return true;
        }

        animateProgress(
            item
        ) {
            const node =
                this.nodes.get(
                    item.id
                );

            const bar =
                node?.querySelector(
                    "[data-notification-progress]"
                );

            if (!bar) {
                return;
            }

            const update =
                () => {
                    if (
                        !this.visible.has(
                            item.id
                        ) ||
                        item.dismissed
                    ) {
                        return;
                    }

                    if (
                        item.persistent ||
                        item.timeout <=
                            0
                    ) {
                        bar.style.transform =
                            "scaleX(1)";

                        return;
                    }

                    let remaining =
                        item.remaining;

                    if (
                        !item.paused &&
                        item.timerStartedAt !==
                        null
                    ) {
                        remaining =
                            Math.max(
                                0,
                                item.remaining -
                                (
                                    performance.now() -
                                    item.timerStartedAt
                                )
                            );
                    }

                    const ratio =
                        clamp(
                            remaining /
                            item.timeout,
                            0,
                            1
                        );

                    bar.style.transform =
                        `scaleX(${ratio})`;

                    window.requestAnimationFrame(
                        update
                    );
                };

            update();
        }

        /*
        ======================================================================
        Actions and Dismissal
        ======================================================================
        */

        async runAction(
            item,
            action
        ) {
            let result =
                action.value;

            if (
                typeof action.handler ===
                "function"
            ) {
                result =
                    await action.handler(
                        item,
                        action,
                        this
                    );
            }

            this.emit(
                "action",
                {
                    item,
                    action,
                    result
                }
            );

            if (
                action.close
            ) {
                this.dismiss(
                    item.id,
                    `action:${action.id}`
                );
            }

            return result;
        }

        async dismiss(
            id,
            reason =
                "manual"
        ) {
            const item =
                this.visible.get(
                    id
                );

            if (!item) {
                return null;
            }

            this.stopTimer(
                id
            );

            item.dismissed =
                true;

            item.dismissedAt =
                new Date().toISOString();

            item.reason =
                reason;

            this.visible.delete(
                id
            );

            const node =
                this.nodes.get(
                    id
                );

            if (node) {
                node.dataset.leaving =
                    "true";

                await new Promise(
                    resolve =>
                        window.setTimeout(
                            resolve,
                            180
                        )
                );

                node.remove();
            }

            this.nodes.delete(
                id
            );

            this.persist();
            this.emit(
                "dismiss",
                item
            );

            return item;
        }

        clear(
            options = {}
        ) {
            const includePersistent =
                options.includePersistent ===
                true;

            const ids =
                [
                    ...this.visible.values()
                ]
                    .filter(
                        item =>
                            includePersistent ||
                            !item.persistent
                    )
                    .map(
                        item =>
                            item.id
                    );

            for (const id of ids) {
                this.dismiss(
                    id,
                    "clear"
                );
            }

            return ids.length;
        }

        clearHistory() {
            const count =
                this.items.length;

            this.items =
                [];

            this.persist();

            this.emit(
                "history-clear",
                {
                    count
                }
            );

            return count;
        }

        enforceVisibleLimit() {
            const items =
                [
                    ...this.visible.values()
                ];

            if (
                items.length <=
                this.options.maximumVisible
            ) {
                return;
            }

            const ranked =
                items.sort(
                    (
                        left,
                        right
                    ) => {
                        const priorityDifference =
                            PRIORITIES.indexOf(
                                right.priority
                            ) -
                            PRIORITIES.indexOf(
                                left.priority
                            );

                        if (
                            priorityDifference
                        ) {
                            return priorityDifference;
                        }

                        return Date.parse(
                            left.createdAt
                        ) -
                        Date.parse(
                            right.createdAt
                        );
                    }
                );

            const keep =
                new Set(
                    ranked
                        .slice(
                            0,
                            this.options.maximumVisible
                        )
                        .map(
                            item =>
                                item.id
                        )
                );

            for (const item of items) {
                if (
                    !keep.has(
                        item.id
                    ) &&
                    !item.persistent
                ) {
                    this.dismiss(
                        item.id,
                        "overflow"
                    );
                }
            }
        }

        /*
        ======================================================================
        History and Filtering
        ======================================================================
        */

        list(
            options = {}
        ) {
            const type =
                options.type
                    ? normalizeType(
                        options.type
                    )
                    : null;

            const priority =
                options.priority
                    ? normalizePriority(
                        options.priority
                    )
                    : null;

            const contains =
                String(
                    options.contains ||
                    options.text ||
                    ""
                )
                    .trim()
                    .toLowerCase();

            const visibleOnly =
                options.visible ===
                true;

            const limit =
                clampInteger(
                    options.limit,
                    100,
                    1,
                    this.options.maximumHistory
                );

            const records =
                this.items.filter(
                    item =>
                        (
                            !type ||
                            item.type ===
                            type
                        ) &&
                        (
                            !priority ||
                            item.priority ===
                            priority
                        ) &&
                        (
                            !contains ||
                            [
                                item.message,
                                item.title,
                                JSON.stringify(
                                    item.metadata
                                )
                            ]
                                .join(" ")
                                .toLowerCase()
                                .includes(
                                    contains
                                )
                        ) &&
                        (
                            !visibleOnly ||
                            this.visible.has(
                                item.id
                            )
                        )
                );

            const sliced =
                records.slice(
                    -limit
                );

            return options.newestFirst
                ? sliced.reverse()
                : sliced;
        }

        counts() {
            const byType =
                Object.fromEntries(
                    LEVELS.map(
                        type => [
                            type,
                            0
                        ]
                    )
                );

            const byPriority =
                Object.fromEntries(
                    PRIORITIES.map(
                        priority => [
                            priority,
                            0
                        ]
                    )
                );

            for (const item of this.items) {
                byType[
                    item.type
                ] =
                    (
                        byType[
                            item.type
                        ] ||
                        0
                    ) +
                    1;

                byPriority[
                    item.priority
                ] =
                    (
                        byPriority[
                            item.priority
                        ] ||
                        0
                    ) +
                    1;
            }

            return {
                total:
                    this.items.length,

                visible:
                    this.visible.size,

                persistent:
                    [
                        ...this.visible.values()
                    ].filter(
                        item =>
                            item.persistent
                    ).length,

                byType,
                byPriority
            };
        }

        status() {
            return {
                version:
                    VERSION,

                position:
                    this.options.position,

                timeout:
                    this.options.timeout,

                maximumVisible:
                    this.options.maximumVisible,

                maximumHistory:
                    this.options.maximumHistory,

                pauseOnHover:
                    this.options.pauseOnHover,

                pauseOnFocus:
                    this.options.pauseOnFocus,

                dismissible:
                    this.options.dismissible,

                showProgress:
                    this.options.showProgress,

                deduplicate:
                    this.options.deduplicate,

                persist:
                    this.options.persist,

                counts:
                    this.counts()
            };
        }

        /*
        ======================================================================
        Persistence
        ======================================================================
        */

        persist() {
            if (
                !this.options.persist ||
                !this.storage
            ) {
                return false;
            }

            try {
                this.storage.setItem(
                    this.storageKey,
                    JSON.stringify(
                        this.items.map(
                            serializeItem
                        )
                    )
                );

                return true;
            } catch (error) {
                return false;
            }
        }

        restore() {
            if (!this.storage) {
                return [];
            }

            try {
                const stored =
                    JSON.parse(
                        this.storage.getItem(
                            this.storageKey
                        ) ||
                        "[]"
                    );

                if (!Array.isArray(stored)) {
                    return [];
                }

                this.items =
                    stored
                        .slice(
                            -this.options.maximumHistory
                        )
                        .map(
                            item => ({
                                ...item,
                                actions:
                                    normalizeActions(
                                        item.actions
                                    ),
                                paused:
                                    false,
                                remaining:
                                    item.timeout ||
                                    0,
                                timerStartedAt:
                                    null
                            })
                        );

                return this.items;
            } catch (error) {
                return [];
            }
        }

        export() {
            return {
                version:
                    VERSION,

                generatedAt:
                    new Date().toISOString(),

                status:
                    this.status(),

                notifications:
                    this.items.map(
                        serializeItem
                    )
            };
        }

        /*
        ======================================================================
        Events and Teardown
        ======================================================================
        */

        emit(
            type,
            detail
        ) {
            const payload =
                detail?.item
                    ? detail
                    : serializeItem(
                        detail
                    );

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
                `notifications:${type}`,
                payload
            );

            this.context.root?.
                dispatchEvent?.(
                    new CustomEvent(
                        `speciedex:terminal-notification-${type}`,
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
                    `speciedex:terminal-notification-${type}`,
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

            for (
                const timer of
                this.timers.values()
            ) {
                window.clearTimeout(
                    timer
                );
            }

            this.timers.clear();
            this.visible.clear();
            this.nodes.clear();

            this.container?.
                remove();

            this.container =
                null;

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
    Initialization
    ==========================================================================
    */

    function initialize(
        context
    ) {
        if (
            context.notifications instanceof
            NotificationCenter
        ) {
            return context.notifications;
        }

        const root =
            context.root;

        const center =
            new NotificationCenter(
                context,
                {
                    timeout:
                        root?.
                            dataset.
                            terminalNotificationTimeout,

                    maximumVisible:
                        root?.
                            dataset.
                            terminalNotificationMaximumVisible,

                    maximumHistory:
                        root?.
                            dataset.
                            terminalNotificationMaximumHistory,

                    position:
                        root?.
                            dataset.
                            terminalNotificationPosition ||
                        DEFAULT_OPTIONS.position,

                    pauseOnHover:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationPauseHover,
                            true
                        ),

                    pauseOnFocus:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationPauseFocus,
                            true
                        ),

                    dismissible:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationDismissible,
                            true
                        ),

                    showProgress:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationProgress,
                            true
                        ),

                    deduplicate:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationDeduplicate,
                            true
                        ),

                    persist:
                        parseBoolean(
                            root?.
                                dataset.
                                terminalNotificationPersist,
                            false
                        )
                }
            );

        context.notifications =
            center;

        context.registerService?.(
            "notifications",
            center
        );

        return center;
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
                    "notify",

                category:
                    "interface",

                description:
                    "Create a terminal notification.",

                usage:
                    "notify <message> [--type info|success|warning|error|critical|system] [--timeout MS]",

                handler: ({
                    args,
                    parsed,
                    context,
                    writeJSON
                }) => {
                    const message =
                        args.join(
                            " "
                        );

                    if (!message) {
                        throw new Error(
                            "A notification message is required."
                        );
                    }

                    const item =
                        context.notifications.notify(
                            message,
                            parsed.options.type ||
                            "info",
                            parsed.options.timeout ??
                            context.notifications.options.timeout,
                            {
                                title:
                                    parsed.options.title ||
                                    "",

                                priority:
                                    parsed.options.priority ||
                                    "normal",

                                persistent:
                                    parsed.flags.persistent ===
                                    true
                            }
                        );

                    return writeJSON(
                        serializeItem(
                            item
                        )
                    );
                }
            },

            {
                name:
                    "notifications",

                category:
                    "interface",

                description:
                    "Display recent notifications.",

                usage:
                    "notifications [count] [type] [contains]",

                handler: ({
                    args,
                    context,
                    writeJSON
                }) => {
                    const limit =
                        clampInteger(
                            args[0],
                            25,
                            1,
                            500
                        );

                    const type =
                        args[1] &&
                        LEVELS.includes(
                            normalizeType(
                                args[1]
                            )
                        )
                            ? normalizeType(
                                args[1]
                            )
                            : null;

                    const contains =
                        type
                            ? args.slice(2).join(
                                " "
                            )
                            : args.slice(1).join(
                                " "
                            );

                    return writeJSON(
                        context.notifications
                            .list({
                                limit,
                                type,
                                contains,
                                newestFirst:
                                    true
                            })
                            .map(
                                serializeItem
                            )
                    );
                }
            },

            {
                name:
                    "notifications-status",

                category:
                    "interface",

                description:
                    "Display notification-center status.",

                usage:
                    "notifications-status",

                handler: ({
                    context,
                    writeJSON
                }) =>
                    writeJSON(
                        context.notifications.status()
                    )
            },

            {
                name:
                    "notifications-clear",

                category:
                    "interface",

                description:
                    "Dismiss visible notifications.",

                usage:
                    "notifications-clear [--all]",

                handler: ({
                    parsed,
                    context,
                    write
                }) => {
                    const count =
                        context.notifications.clear({
                            includePersistent:
                                parsed.flags.all ===
                                true
                        });

                    return write(
                        `Dismissed ${count} notification${count === 1 ? "" : "s"}.`,
                        "success"
                    );
                }
            },

            {
                name:
                    "notifications-history-clear",

                category:
                    "interface",

                description:
                    "Clear stored notification history.",

                usage:
                    "notifications-history-clear",

                handler: ({
                    context,
                    write
                }) => {
                    const count =
                        context.notifications.clearHistory();

                    return write(
                        `Cleared ${count} historical notification${count === 1 ? "" : "s"}.`,
                        "success"
                    );
                }
            },

            {
                name:
                    "notifications-export",

                category:
                    "interface",

                description:
                    "Export notification history as JSON.",

                usage:
                    "notifications-export [filename]",

                handler: ({
                    args,
                    context,
                    write
                }) => {
                    const filename =
                        args[0] ||
                        "speciedex-terminal-notifications.json";

                    const payload =
                        JSON.stringify(
                            context.notifications.export(),
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
                        `Notifications exported to ${filename}.`,
                        "success"
                    );
                }
            },

            {
                name:
                    "notifications-test",

                category:
                    "interface",

                description:
                    "Create one notification at every supported level.",

                usage:
                    "notifications-test",

                handler: ({
                    context,
                    write
                }) => {
                    for (const type of LEVELS) {
                        context.notifications.notify(
                            `SpeciedexTerminal ${type} notification test.`,
                            type,
                            type ===
                                "critical"
                                ? 0
                                : 5000,
                            {
                                title:
                                    type,

                                priority:
                                    type ===
                                        "critical"
                                        ? "urgent"
                                        : "normal"
                            }
                        );
                    }

                    return write(
                        "Notification test sequence created.",
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

            LEVELS,
            PRIORITIES,
            DEFAULT_OPTIONS,
            NotificationCenter,

            normalizeType,
            normalizePriority,
            normalizeActions,
            serializeItem,
            parseBoolean,
            clampInteger,
            injectNotificationStyles,

            initialize,
            mount:
                initialize,
            init:
                initialize,
            setup:
                initialize,

            commands
        });

    window.SpeciedexTerminalNotifications =
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
