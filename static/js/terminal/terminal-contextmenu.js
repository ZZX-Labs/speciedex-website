/*
========================================================================
Speciedex.org
Terminal Context Menu
========================================================================

Accessible context-menu service for SpeciedexTerminal.

Provides:

    • Viewport-safe context-menu positioning
    • Copy, select, clear, focus, and paste actions
    • Keyboard navigation and escape handling
    • Clipboard API fallback support
    • Safe lifecycle cleanup
    • Terminal command integration

Copyright (c) 2026 Speciedex.org & ZZX-Labs R&D
Licensed under the MIT License.
========================================================================
*/

(function (window, document) {
    "use strict";

    const MODULE_NAME = "Contextmenu";
    const VERSION = "2.0.0";

    const SELECTORS = Object.freeze({
        menu: "[data-terminal-context-menu]",
        output:
            "[data-terminal-output], .terminal-output",
        input:
            "[data-terminal-input], input[type='text'], textarea"
    });

    function dispatch(target, name, detail, options = {}) {
        if (
            !target ||
            typeof target.dispatchEvent !== "function"
        ) {
            return false;
        }

        try {
            return target.dispatchEvent(
                new CustomEvent(
                    name,
                    {
                        bubbles:
                            options.bubbles === true,
                        cancelable:
                            options.cancelable === true,
                        detail
                    }
                )
            );
        } catch (_error) {
            return false;
        }
    }

    function getTextSelection() {
        try {
            return String(
                window.getSelection?.() || ""
            );
        } catch (_error) {
            return "";
        }
    }

    async function copyText(text) {
        const value =
            String(text ?? "");

        if (!value) {
            return false;
        }

        try {
            if (
                navigator.clipboard &&
                typeof navigator.clipboard.writeText ===
                "function"
            ) {
                await navigator.clipboard.writeText(
                    value
                );

                return true;
            }
        } catch (_error) {
            /*
            ------------------------------------------------------------------
            Fall through to the legacy copy path.
            ------------------------------------------------------------------
            */
        }

        const textarea =
            document.createElement("textarea");

        textarea.value = value;
        textarea.readOnly = true;
        textarea.setAttribute(
            "aria-hidden",
            "true"
        );

        Object.assign(
            textarea.style,
            {
                position: "fixed",
                opacity: "0",
                pointerEvents: "none",
                left: "-9999px",
                top: "0"
            }
        );

        document.body.appendChild(textarea);
        textarea.select();

        let copied = false;

        try {
            copied =
                document.execCommand("copy");
        } catch (_error) {
            copied = false;
        }

        textarea.remove();
        return copied;
    }

    async function readClipboardText() {
        try {
            if (
                navigator.clipboard &&
                typeof navigator.clipboard.readText ===
                "function"
            ) {
                return await navigator.clipboard.readText();
            }
        } catch (_error) {
            return "";
        }

        return "";
    }

    function writeStatus(context, message, type = "info") {
        if (
            context &&
            typeof context.write === "function"
        ) {
            return context.write(
                message,
                type
            );
        }

        return message;
    }

    class ContextMenu extends EventTarget {
        constructor(context, options = {}) {
            super();

            if (!context?.root) {
                throw new TypeError(
                    "A terminal context with a root element is required."
                );
            }

            this.context = context;
            this.root = context.root;
            this.options = {
                enabled:
                    options.enabled !== false,
                includePaste:
                    options.includePaste !== false
            };

            this.menu =
                this.root.querySelector(
                    SELECTORS.menu
                ) || null;

            this.previousFocus = null;
            this.opened = false;
            this.destroyed = false;

            this.boundContextMenu =
                event => this.handleContextMenu(event);

            this.boundDocumentPointer =
                event => this.handleDocumentPointer(event);

            this.boundDocumentKeydown =
                event => this.handleDocumentKeydown(event);

            this.boundWindowBlur =
                () => this.close("window-blur");

            this.boundWindowResize =
                () => this.close("window-resize");

            this.ensureMenu();
            this.bind();
        }

        ensureMenu() {
            if (this.menu) {
                this.configureMenu();
                return this.menu;
            }

            const menu =
                document.createElement("div");

            menu.dataset.terminalContextMenu = "";
            menu.className =
                "terminal-context-menu";
            menu.hidden = true;

            this.root.appendChild(menu);
            this.menu = menu;

            this.configureMenu();
            return menu;
        }

        configureMenu() {
            if (!this.menu) {
                return;
            }

            this.menu.hidden = true;
            this.menu.setAttribute(
                "role",
                "menu"
            );
            this.menu.setAttribute(
                "aria-label",
                "Terminal context menu"
            );
            this.menu.tabIndex = -1;

            Object.assign(
                this.menu.style,
                {
                    position: "fixed",
                    zIndex: "2147483647"
                }
            );
        }

        bind() {
            this.root.addEventListener(
                "contextmenu",
                this.boundContextMenu
            );

            document.addEventListener(
                "pointerdown",
                this.boundDocumentPointer,
                true
            );

            document.addEventListener(
                "keydown",
                this.boundDocumentKeydown,
                true
            );

            window.addEventListener(
                "blur",
                this.boundWindowBlur
            );

            window.addEventListener(
                "resize",
                this.boundWindowResize
            );
        }

        unbind() {
            this.root.removeEventListener(
                "contextmenu",
                this.boundContextMenu
            );

            document.removeEventListener(
                "pointerdown",
                this.boundDocumentPointer,
                true
            );

            document.removeEventListener(
                "keydown",
                this.boundDocumentKeydown,
                true
            );

            window.removeEventListener(
                "blur",
                this.boundWindowBlur
            );

            window.removeEventListener(
                "resize",
                this.boundWindowResize
            );
        }

        isEnabled() {
            return (
                this.options.enabled &&
                !this.destroyed
            );
        }

        setEnabled(enabled) {
            this.options.enabled =
                Boolean(enabled);

            if (!this.options.enabled) {
                this.close("disabled");
            }

            return this.options.enabled;
        }

        getOutputElement() {
            return (
                this.context.elements?.output ||
                this.root.querySelector(
                    SELECTORS.output
                ) ||
                null
            );
        }

        getInputElement() {
            return (
                this.context.elements?.input ||
                this.root.querySelector(
                    SELECTORS.input
                ) ||
                null
            );
        }

        getOutputText() {
            const output =
                this.getOutputElement();

            return (
                output?.innerText ||
                output?.textContent ||
                ""
            );
        }

        getActions(event) {
            const input =
                this.getInputElement();

            const selection =
                getTextSelection();

            const actions = [
                {
                    id: "copy-selection",
                    label: "Copy selection",
                    disabled:
                        !selection.trim(),
                    run: async () => {
                        const copied =
                            await copyText(
                                selection
                            );

                        writeStatus(
                            this.context,
                            copied
                                ? "Selection copied."
                                : "Unable to copy selection.",
                            copied
                                ? "success"
                                : "warning"
                        );
                    }
                },
                {
                    id: "copy-output",
                    label: "Copy output",
                    disabled:
                        !this.getOutputText().trim(),
                    run: async () => {
                        const copied =
                            await copyText(
                                this.getOutputText()
                            );

                        writeStatus(
                            this.context,
                            copied
                                ? "Terminal output copied."
                                : "Unable to copy terminal output.",
                            copied
                                ? "success"
                                : "warning"
                        );
                    }
                },
                {
                    id: "select-output",
                    label: "Select output",
                    disabled:
                        !this.getOutputElement(),
                    run: () => {
                        const output =
                            this.getOutputElement();

                        if (!output) {
                            return;
                        }

                        const range =
                            document.createRange();

                        range.selectNodeContents(
                            output
                        );

                        const selectionObject =
                            window.getSelection?.();

                        selectionObject?.removeAllRanges();
                        selectionObject?.addRange(range);
                    }
                },
                {
                    id: "clear-output",
                    label: "Clear output",
                    disabled:
                        typeof this.context.clear !==
                        "function",
                    run: () => {
                        this.context.clear?.();

                        writeStatus(
                            this.context,
                            "Terminal output cleared.",
                            "success"
                        );
                    }
                },
                {
                    id: "focus-input",
                    label: "Focus input",
                    disabled:
                        !input &&
                        typeof this.context.focus !==
                        "function",
                    run: () => {
                        if (
                            typeof this.context.focus ===
                            "function"
                        ) {
                            this.context.focus();
                        } else {
                            input?.focus();
                        }
                    }
                }
            ];

            if (this.options.includePaste) {
                actions.splice(
                    3,
                    0,
                    {
                        id: "paste-input",
                        label: "Paste into input",
                        disabled:
                            !input ||
                            !(
                                "value" in input
                            ),
                        run: async () => {
                            const text =
                                await readClipboardText();

                            if (!text) {
                                writeStatus(
                                    this.context,
                                    "Clipboard text is unavailable.",
                                    "warning"
                                );

                                return;
                            }

                            const start =
                                input.selectionStart ??
                                input.value.length;

                            const end =
                                input.selectionEnd ??
                                start;

                            input.value =
                                input.value.slice(0, start) +
                                text +
                                input.value.slice(end);

                            const cursor =
                                start + text.length;

                            input.setSelectionRange?.(
                                cursor,
                                cursor
                            );

                            input.dispatchEvent(
                                new Event(
                                    "input",
                                    {
                                        bubbles: true
                                    }
                                )
                            );

                            input.focus();
                        }
                    }
                );
            }

            return actions.map(action => ({
                ...action,
                event
            }));
        }

        createButton(action) {
            const button =
                document.createElement("button");

            button.type = "button";
            button.className =
                "terminal-context-menu-item";
            button.dataset.contextAction =
                action.id;
            button.setAttribute(
                "role",
                "menuitem"
            );
            button.tabIndex = -1;
            button.textContent =
                action.label;
            button.disabled =
                Boolean(action.disabled);

            button.addEventListener(
                "click",
                async event => {
                    event.preventDefault();
                    event.stopPropagation();

                    if (button.disabled) {
                        return;
                    }

                    try {
                        await action.run();

                        dispatch(
                            this,
                            "action",
                            {
                                id: action.id,
                                label: action.label
                            }
                        );

                        dispatch(
                            this.root,
                            "speciedex:terminal-context-action",
                            {
                                id: action.id,
                                label: action.label
                            },
                            {
                                bubbles: true
                            }
                        );
                    } catch (error) {
                        writeStatus(
                            this.context,
                            `Context-menu action failed: ${error?.message || error}`,
                            "error"
                        );
                    } finally {
                        this.close("action");
                    }
                }
            );

            return button;
        }

        render(event) {
            if (!this.menu) {
                return;
            }

            this.menu.replaceChildren();

            for (
                const action of
                this.getActions(event)
            ) {
                this.menu.appendChild(
                    this.createButton(action)
                );
            }
        }

        position(clientX, clientY) {
            if (!this.menu) {
                return;
            }

            this.menu.style.left =
                "0px";
            this.menu.style.top =
                "0px";
            this.menu.hidden = false;

            const rect =
                this.menu.getBoundingClientRect();

            const padding = 8;

            const left =
                Math.max(
                    padding,
                    Math.min(
                        Number(clientX) || 0,
                        window.innerWidth -
                        rect.width -
                        padding
                    )
                );

            const top =
                Math.max(
                    padding,
                    Math.min(
                        Number(clientY) || 0,
                        window.innerHeight -
                        rect.height -
                        padding
                    )
                );

            this.menu.style.left =
                `${Math.round(left)}px`;
            this.menu.style.top =
                `${Math.round(top)}px`;
        }

        handleContextMenu(event) {
            if (!this.isEnabled()) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            this.open(
                event.clientX,
                event.clientY,
                event
            );
        }

        open(clientX, clientY, event = null) {
            if (
                !this.isEnabled() ||
                !this.menu
            ) {
                return false;
            }

            this.previousFocus =
                document.activeElement instanceof
                HTMLElement
                    ? document.activeElement
                    : null;

            this.render(event);
            this.position(
                clientX,
                clientY
            );

            this.opened = true;

            const firstEnabled =
                this.menu.querySelector(
                    '[role="menuitem"]:not(:disabled)'
                );

            firstEnabled?.focus();

            const detail = {
                x: clientX,
                y: clientY,
                event
            };

            dispatch(
                this,
                "open",
                detail
            );

            dispatch(
                this.root,
                "speciedex:terminal-context-open",
                detail,
                {
                    bubbles: true
                }
            );

            return true;
        }

        close(reason = "manual") {
            if (
                !this.menu ||
                this.menu.hidden
            ) {
                return false;
            }

            this.menu.hidden = true;
            this.opened = false;

            const previousFocus =
                this.previousFocus;

            this.previousFocus = null;

            if (
                previousFocus &&
                previousFocus.isConnected &&
                typeof previousFocus.focus ===
                "function"
            ) {
                previousFocus.focus({
                    preventScroll: true
                });
            }

            const detail = {
                reason
            };

            dispatch(
                this,
                "close",
                detail
            );

            dispatch(
                this.root,
                "speciedex:terminal-context-close",
                detail,
                {
                    bubbles: true
                }
            );

            return true;
        }

        handleDocumentPointer(event) {
            if (
                !this.opened ||
                !this.menu
            ) {
                return;
            }

            if (
                !this.menu.contains(
                    event.target
                )
            ) {
                this.close(
                    "outside-pointer"
                );
            }
        }

        getMenuItems() {
            if (!this.menu) {
                return [];
            }

            return [
                ...this.menu.querySelectorAll(
                    '[role="menuitem"]:not(:disabled)'
                )
            ];
        }

        moveFocus(direction) {
            const items =
                this.getMenuItems();

            if (!items.length) {
                return;
            }

            const current =
                items.indexOf(
                    document.activeElement
                );

            const next =
                current < 0
                    ? 0
                    : (
                        current +
                        direction +
                        items.length
                    ) % items.length;

            items[next].focus();
        }

        handleDocumentKeydown(event) {
            if (!this.opened) {
                return;
            }

            if (event.key === "Escape") {
                event.preventDefault();
                this.close("escape");
                return;
            }

            if (
                event.key === "ArrowDown"
            ) {
                event.preventDefault();
                this.moveFocus(1);
                return;
            }

            if (
                event.key === "ArrowUp"
            ) {
                event.preventDefault();
                this.moveFocus(-1);
                return;
            }

            if (
                event.key === "Home"
            ) {
                event.preventDefault();
                this.getMenuItems()[0]?.focus();
                return;
            }

            if (
                event.key === "End"
            ) {
                event.preventDefault();

                const items =
                    this.getMenuItems();

                items[
                    items.length - 1
                ]?.focus();
            }
        }

        status() {
            return {
                version: VERSION,
                enabled:
                    this.options.enabled,
                opened:
                    this.opened,
                includePaste:
                    this.options.includePaste,
                menuPresent:
                    Boolean(this.menu),
                destroyed:
                    this.destroyed
            };
        }

        destroy() {
            if (this.destroyed) {
                return false;
            }

            this.close("destroy");
            this.unbind();

            this.destroyed = true;
            this.options.enabled = false;

            dispatch(
                this,
                "destroy",
                {
                    timestamp:
                        new Date().toISOString()
                }
            );

            return true;
        }
    }

    function initialize(context) {
        if (
            context.contextMenu instanceof
            ContextMenu &&
            !context.contextMenu.destroyed
        ) {
            return context.contextMenu;
        }

        const dataset =
            context.root?.dataset || {};

        const menu =
            new ContextMenu(
                context,
                {
                    enabled:
                        dataset.
                            terminalContextMenu !==
                        "false",
                    includePaste:
                        dataset.
                            terminalContextMenuPaste !==
                        "false"
                }
            );

        context.contextMenu = menu;

        context.registerService?.(
            "contextmenu",
            menu
        );

        context.registerService?.(
            "contextMenu",
            menu
        );

        dispatch(
            document,
            "speciedex:terminal-contextmenu-ready",
            {
                context,
                contextMenu: menu
            }
        );

        return menu;
    }

    function requireMenu(context) {
        if (
            !(
                context?.contextMenu instanceof
                ContextMenu
            )
        ) {
            throw new Error(
                "Terminal context-menu service is unavailable."
            );
        }

        return context.contextMenu;
    }

    const commands = [
        {
            name: "contextmenu",
            aliases: [
                "context-menu",
                "ctxmenu"
            ],
            category: "system",
            description:
                "Inspect or configure the terminal context menu.",
            usage:
                "contextmenu [status|enable|disable|open|close]",
            handler: ({
                args = [],
                context,
                writeJSON,
                write
            }) => {
                const menu =
                    requireMenu(context);

                const action =
                    String(args[0] || "status")
                        .toLowerCase();

                if (action === "enable") {
                    menu.setEnabled(true);

                    return write?.(
                        "Context menu enabled.",
                        "success"
                    );
                }

                if (action === "disable") {
                    menu.setEnabled(false);

                    return write?.(
                        "Context menu disabled.",
                        "success"
                    );
                }

                if (action === "open") {
                    const rect =
                        context.root.
                            getBoundingClientRect();

                    menu.open(
                        rect.left +
                        Math.min(
                            rect.width / 2,
                            240
                        ),
                        rect.top +
                        Math.min(
                            rect.height / 2,
                            160
                        )
                    );

                    return write?.(
                        "Context menu opened.",
                        "success"
                    );
                }

                if (action === "close") {
                    menu.close("command");

                    return write?.(
                        "Context menu closed.",
                        "success"
                    );
                }

                if (action !== "status") {
                    throw new Error(
                        `Unknown context-menu action: ${action}`
                    );
                }

                return typeof writeJSON ===
                    "function"
                        ? writeJSON(
                            menu.status()
                        )
                        : menu.status();
            }
        }
    ];

    const api = Object.freeze({
        name: MODULE_NAME,
        version: VERSION,
        ContextMenu,
        copyText,
        readClipboardText,
        initialize,
        mount: initialize,
        init: initialize,
        setup: initialize,
        commands
    });

    window.SpeciedexTerminalContextmenu =
        api;

    window.SpeciedexTerminalModules =
        window.SpeciedexTerminalModules || {};

    window.SpeciedexTerminalModules[
        MODULE_NAME
    ] = api;

    dispatch(
        document,
        "speciedex:terminal-module-available",
        {
            name: MODULE_NAME,
            module: api
        }
    );
})(window, document);
