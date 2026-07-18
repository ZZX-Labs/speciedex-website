"use strict";

/*
==============================================================================
Speciedex.org
Navigation Module
==============================================================================

Loaded by:

    /static/js/script.js

Responsibilities:

    • Initialize primary navigation
    • Toggle mobile navigation
    • Open and close dropdown menus
    • Close navigation with outside clicks or Escape
    • Reset mobile state when leaving the mobile breakpoint
    • Highlight the current page and active dropdown branch
    • Maintain ARIA state
    • Respond safely to dynamically loaded navigation partials

==============================================================================
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (Speciedex.navigationModuleLoaded) {
        return;
    }

    Speciedex.navigationModuleLoaded = true;

    /*
    ==========================================================================
    Selectors / Classes
    ==========================================================================
    */

    const NAV_SELECTOR =
        "[data-site-nav], .site-nav, .header nav";

    const MENU_TOGGLE_SELECTOR =
        "[data-nav-toggle], .menu-toggle, .nav-toggle";

    const MENU_SELECTOR =
        "[data-nav-menu], .nav-menu, .nav-links";

    const DROPDOWN_SELECTOR =
        ".dropdown";

    const DROPDOWN_TOGGLE_SELECTOR =
        "[data-dropdown-toggle], .dropdown-toggle";

    const OPEN_CLASS =
        "open";

    const ACTIVE_CLASS =
        "active";

    const ACTIVE_BRANCH_CLASS =
        "active-branch";

    const MOBILE_MEDIA_QUERY =
        "(max-width: 860px)";

    /*
    ==========================================================================
    Internal State
    ==========================================================================
    */

    let nav = null;
    let menu = null;
    let menuToggle = null;
    let mobileMediaQuery = null;
    let initialized = false;

    /*
    ==========================================================================
    Resolve Navigation Elements
    ==========================================================================
    */

    function findNavigation() {
        return document.querySelector(
            NAV_SELECTOR
        );
    }

    function resolveNavigationElements() {
        nav =
            findNavigation();

        if (!nav) {
            menu = null;
            menuToggle = null;
            return false;
        }

        menuToggle =
            nav.querySelector(
                MENU_TOGGLE_SELECTOR
            );

        menu =
            nav.querySelector(
                MENU_SELECTOR
            );

        return true;
    }

    /*
    ==========================================================================
    Initialize Navigation
    ==========================================================================
    */

    function initializeNavigation() {
        const nextNav =
            findNavigation();

        if (!nextNav) {
            return;
        }

        if (
            initialized &&
            nextNav === nav
        ) {
            highlightCurrentPage(nav);
            syncNavigationState();
            return;
        }

        if (initialized) {
            destroyNavigation();
        }

        if (!resolveNavigationElements()) {
            return;
        }

        initializeMediaQuery();
        initializeMenuToggle();
        initializeDropdowns();
        initializeNavigationLinks();

        document.addEventListener(
            "click",
            handleDocumentClick
        );

        document.addEventListener(
            "keydown",
            handleDocumentKeydown
        );

        document.addEventListener(
            "speciedex:include-loaded",
            handleIncludeLoaded
        );

        highlightCurrentPage(nav);
        syncNavigationState();

        initialized = true;

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:navigation-ready",
                {
                    detail: {
                        nav,
                        menu,
                        menuToggle
                    }
                }
            )
        );
    }

    /*
    ==========================================================================
    Mobile Breakpoint
    ==========================================================================
    */

    function initializeMediaQuery() {
        mobileMediaQuery =
            window.matchMedia(
                MOBILE_MEDIA_QUERY
            );

        if (
            typeof mobileMediaQuery
                .addEventListener ===
                "function"
        ) {
            mobileMediaQuery.addEventListener(
                "change",
                handleBreakpointChange
            );
        } else {
            mobileMediaQuery.addListener(
                handleBreakpointChange
            );
        }
    }

    function handleBreakpointChange(event) {
        if (event.matches) {
            syncNavigationState();
            return;
        }

        closeDropdowns(nav);
        closeMenu({
            restoreFocus: false
        });

        document.body.classList.remove(
            "menu-open"
        );
    }

    function isMobileNavigation() {
        return (
            mobileMediaQuery?.matches ??
            window.matchMedia(
                MOBILE_MEDIA_QUERY
            ).matches
        );
    }

    /*
    ==========================================================================
    Mobile Menu
    ==========================================================================
    */

    function initializeMenuToggle() {
        if (!menuToggle || !menu) {
            return;
        }

        if (!menu.id) {
            menu.id =
                "site-navigation-menu";
        }

        menuToggle.setAttribute(
            "aria-controls",
            menu.id
        );

        menuToggle.setAttribute(
            "aria-expanded",
            String(
                menu.classList.contains(
                    OPEN_CLASS
                )
            )
        );

        menuToggle.removeEventListener(
            "click",
            handleMenuToggleClick
        );

        menuToggle.addEventListener(
            "click",
            handleMenuToggleClick
        );
    }

    function handleMenuToggleClick(event) {
        event.preventDefault();
        event.stopPropagation();

        if (!menu) {
            return;
        }

        const open =
            !menu.classList.contains(
                OPEN_CLASS
            );

        setMenuState(open);
    }

    function setMenuState(
        open,
        options = {}
    ) {
        if (!menu || !menuToggle) {
            return;
        }

        const shouldOpen =
            Boolean(open);

        menu.classList.toggle(
            OPEN_CLASS,
            shouldOpen
        );

        menuToggle.classList.toggle(
            OPEN_CLASS,
            shouldOpen
        );

        menuToggle.setAttribute(
            "aria-expanded",
            String(shouldOpen)
        );

        if (isMobileNavigation()) {
            document.body.classList.toggle(
                "menu-open",
                shouldOpen
            );
        } else {
            document.body.classList.remove(
                "menu-open"
            );
        }

        if (!shouldOpen) {
            closeDropdowns(nav);

            if (
                options.restoreFocus ===
                true
            ) {
                menuToggle.focus();
            }
        }

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:navigation-toggle",
                {
                    detail: {
                        open:
                            shouldOpen,
                        nav,
                        menu,
                        menuToggle
                    }
                }
            )
        );
    }

    function closeMenu(
        options = {}
    ) {
        setMenuState(
            false,
            options
        );
    }

    /*
    ==========================================================================
    Dropdown Menus
    ==========================================================================
    */

    function initializeDropdowns() {
        if (!nav) {
            return;
        }

        nav.querySelectorAll(
            DROPDOWN_TOGGLE_SELECTOR
        ).forEach(
            (toggle, index) => {
                const dropdown =
                    toggle.closest(
                        DROPDOWN_SELECTOR
                    );

                if (!dropdown) {
                    return;
                }

                const submenu =
                    getDirectSubmenu(
                        dropdown
                    );

                if (submenu) {
                    if (!submenu.id) {
                        submenu.id =
                            `site-submenu-${index + 1}`;
                    }

                    toggle.setAttribute(
                        "aria-controls",
                        submenu.id
                    );
                }

                toggle.setAttribute(
                    "aria-expanded",
                    String(
                        dropdown.classList
                            .contains(
                                OPEN_CLASS
                            )
                    )
                );

                toggle.removeEventListener(
                    "click",
                    handleDropdownToggleClick
                );

                toggle.addEventListener(
                    "click",
                    handleDropdownToggleClick
                );
            }
        );
    }

    function getDirectSubmenu(
        dropdown
    ) {
        if (!dropdown) {
            return null;
        }

        return Array.from(
            dropdown.children
        ).find(
            (child) =>
                child.matches?.(
                    ".dropdown-menu, .submenu"
                )
        ) || null;
    }

    function handleDropdownToggleClick(
        event
    ) {
        event.preventDefault();
        event.stopPropagation();

        const toggle =
            event.currentTarget;

        const dropdown =
            toggle.closest(
                DROPDOWN_SELECTOR
            );

        if (!dropdown || !nav) {
            return;
        }

        const open =
            !dropdown.classList.contains(
                OPEN_CLASS
            );

        closeDropdowns(
            nav,
            dropdown
        );

        setDropdownState(
            dropdown,
            toggle,
            open
        );
    }

    function setDropdownState(
        dropdown,
        toggle,
        open
    ) {
        if (!dropdown || !toggle) {
            return;
        }

        const shouldOpen =
            Boolean(open);

        dropdown.classList.toggle(
            OPEN_CLASS,
            shouldOpen
        );

        toggle.setAttribute(
            "aria-expanded",
            String(shouldOpen)
        );

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:dropdown-toggle",
                {
                    detail: {
                        dropdown,
                        toggle,
                        open:
                            shouldOpen
                    }
                }
            )
        );
    }

    function closeDropdowns(
        navigation = nav,
        current = null
    ) {
        if (!navigation) {
            return;
        }

        navigation.querySelectorAll(
            `${DROPDOWN_SELECTOR}.${OPEN_CLASS}`
        ).forEach(
            (dropdown) => {
                if (
                    current &&
                    dropdown === current
                ) {
                    return;
                }

                dropdown.classList.remove(
                    OPEN_CLASS
                );

                const toggle =
                    Array.from(
                        dropdown.children
                    ).find(
                        (child) =>
                            child.matches?.(
                                DROPDOWN_TOGGLE_SELECTOR
                            )
                    );

                toggle?.setAttribute(
                    "aria-expanded",
                    "false"
                );
            }
        );
    }

    /*
    ==========================================================================
    Navigation Links
    ==========================================================================
    */

    function initializeNavigationLinks() {
        if (!nav) {
            return;
        }

        nav.querySelectorAll(
            "a[href]"
        ).forEach(
            (link) => {
                link.removeEventListener(
                    "click",
                    handleNavigationLinkClick
                );

                link.addEventListener(
                    "click",
                    handleNavigationLinkClick
                );
            }
        );
    }

    function handleNavigationLinkClick(
        event
    ) {
        const link =
            event.currentTarget;

        if (
            link.matches(
                DROPDOWN_TOGGLE_SELECTOR
            )
        ) {
            return;
        }

        if (
            isMobileNavigation() &&
            menu?.classList.contains(
                OPEN_CLASS
            )
        ) {
            closeMenu({
                restoreFocus: false
            });
        }
    }

    /*
    ==========================================================================
    Global Event Handlers
    ==========================================================================
    */

    function handleDocumentClick(event) {
        if (!nav) {
            return;
        }

        if (
            nav.contains(
                event.target
            )
        ) {
            return;
        }

        closeDropdowns(nav);

        if (isMobileNavigation()) {
            closeMenu({
                restoreFocus: false
            });
        }
    }

    function handleDocumentKeydown(event) {
        if (event.key !== "Escape") {
            return;
        }

        const hadOpenMenu =
            Boolean(
                menu?.classList.contains(
                    OPEN_CLASS
                )
            );

        const hadOpenDropdown =
            Boolean(
                nav?.querySelector(
                    `${DROPDOWN_SELECTOR}.${OPEN_CLASS}`
                )
            );

        if (
            !hadOpenMenu &&
            !hadOpenDropdown
        ) {
            return;
        }

        closeDropdowns(nav);

        closeMenu({
            restoreFocus:
                hadOpenMenu
        });

        if (
            !hadOpenMenu &&
            hadOpenDropdown
        ) {
            const activeDropdownToggle =
                document.activeElement
                    ?.closest?.(
                        DROPDOWN_TOGGLE_SELECTOR
                    );

            activeDropdownToggle?.focus?.();
        }
    }

    /*
    ==========================================================================
    Include Loader Integration
    ==========================================================================
    */

    function handleIncludeLoaded(event) {
        const name =
            String(
                event.detail?.name || ""
            ).toLowerCase();

        if (
            name !== "header" &&
            name !== "nav"
        ) {
            return;
        }

        const nextNav =
            findNavigation();

        if (
            !nextNav ||
            nextNav === nav
        ) {
            highlightCurrentPage(nav);
            return;
        }

        destroyNavigation();
        initializeNavigation();
    }

    /*
    ==========================================================================
    Current Page Highlighting
    ==========================================================================
    */

    function highlightCurrentPage(
        navigation = nav
    ) {
        if (!navigation) {
            return;
        }

        const current =
            normalizePath(
                window.location.pathname
            );

        navigation
            .querySelectorAll(
                `.${ACTIVE_BRANCH_CLASS}`
            )
            .forEach(
                (branch) => {
                    branch.classList.remove(
                        ACTIVE_BRANCH_CLASS
                    );
                }
            );

        navigation
            .querySelectorAll(
                "a[href]"
            )
            .forEach(
                (link) => {
                    link.classList.remove(
                        ACTIVE_CLASS
                    );

                    if (
                        link.getAttribute(
                            "aria-current"
                        ) === "page"
                    ) {
                        link.removeAttribute(
                            "aria-current"
                        );
                    }

                    let url;

                    try {
                        url =
                            new URL(
                                link.getAttribute(
                                    "href"
                                ),
                                window.location.href
                            );
                    } catch {
                        return;
                    }

                    if (
                        url.origin !==
                        window.location.origin
                    ) {
                        return;
                    }

                    if (
                        normalizePath(
                            url.pathname
                        ) !== current
                    ) {
                        return;
                    }

                    link.classList.add(
                        ACTIVE_CLASS
                    );

                    link.setAttribute(
                        "aria-current",
                        "page"
                    );

                    let branch =
                        link.closest(
                            DROPDOWN_SELECTOR
                        );

                    while (branch) {
                        branch.classList.add(
                            ACTIVE_BRANCH_CLASS
                        );

                        branch =
                            branch.parentElement
                                ?.closest(
                                    DROPDOWN_SELECTOR
                                ) || null;
                    }
                }
            );
    }

    function normalizePath(path) {
        let normalized =
            String(path || "/")
                .replace(
                    /\/index\.html$/i,
                    "/"
                )
                .replace(
                    /\/+/g,
                    "/"
                );

        if (
            !normalized.startsWith("/")
        ) {
            normalized =
                `/${normalized}`;
        }

        if (
            normalized !== "/" &&
            !normalized.endsWith("/")
        ) {
            normalized =
                `${normalized}/`;
        }

        return normalized;
    }

    /*
    ==========================================================================
    State Synchronization
    ==========================================================================
    */

    function syncNavigationState() {
        if (!menu || !menuToggle) {
            return;
        }

        if (!isMobileNavigation()) {
            menu.classList.remove(
                OPEN_CLASS
            );

            menuToggle.classList.remove(
                OPEN_CLASS
            );

            menuToggle.setAttribute(
                "aria-expanded",
                "false"
            );

            document.body.classList.remove(
                "menu-open"
            );

            closeDropdowns(nav);

            return;
        }

        const open =
            menu.classList.contains(
                OPEN_CLASS
            );

        menuToggle.classList.toggle(
            OPEN_CLASS,
            open
        );

        menuToggle.setAttribute(
            "aria-expanded",
            String(open)
        );

        document.body.classList.toggle(
            "menu-open",
            open
        );
    }

    /*
    ==========================================================================
    Refresh Navigation
    ==========================================================================
    */

    function refreshNavigation() {
        const nextNav =
            findNavigation();

        if (!nextNav) {
            return;
        }

        if (nextNav !== nav) {
            destroyNavigation();
            initializeNavigation();
            return;
        }

        resolveNavigationElements();
        initializeMenuToggle();
        initializeDropdowns();
        initializeNavigationLinks();
        highlightCurrentPage(nav);
        syncNavigationState();
    }

    /*
    ==========================================================================
    Destroy Navigation
    ==========================================================================
    */

    function destroyNavigation() {
        if (!initialized) {
            return;
        }

        menuToggle?.removeEventListener(
            "click",
            handleMenuToggleClick
        );

        nav?.querySelectorAll(
            DROPDOWN_TOGGLE_SELECTOR
        ).forEach(
            (toggle) => {
                toggle.removeEventListener(
                    "click",
                    handleDropdownToggleClick
                );
            }
        );

        nav?.querySelectorAll(
            "a[href]"
        ).forEach(
            (link) => {
                link.removeEventListener(
                    "click",
                    handleNavigationLinkClick
                );
            }
        );

        document.removeEventListener(
            "click",
            handleDocumentClick
        );

        document.removeEventListener(
            "keydown",
            handleDocumentKeydown
        );

        document.removeEventListener(
            "speciedex:include-loaded",
            handleIncludeLoaded
        );

        if (mobileMediaQuery) {
            if (
                typeof mobileMediaQuery
                    .removeEventListener ===
                    "function"
            ) {
                mobileMediaQuery
                    .removeEventListener(
                        "change",
                        handleBreakpointChange
                    );
            } else {
                mobileMediaQuery
                    .removeListener(
                        handleBreakpointChange
                    );
            }
        }

        closeDropdowns(nav);

        if (menu) {
            menu.classList.remove(
                OPEN_CLASS
            );
        }

        if (menuToggle) {
            menuToggle.classList.remove(
                OPEN_CLASS
            );

            menuToggle.setAttribute(
                "aria-expanded",
                "false"
            );
        }

        document.body.classList.remove(
            "menu-open"
        );

        mobileMediaQuery = null;
        nav = null;
        menu = null;
        menuToggle = null;
        initialized = false;
    }

    /*
    ==========================================================================
    Public API
    ==========================================================================
    */

    Speciedex.initializeNavigation =
        initializeNavigation;

    Speciedex.refreshNavigation =
        refreshNavigation;

    Speciedex.closeDropdowns =
        closeDropdowns;

    Speciedex.closeNavigationMenu =
        closeMenu;

    Speciedex.highlightCurrentPage =
        highlightCurrentPage;

    Speciedex.normalizePath =
        normalizePath;

    Speciedex.destroyNavigation =
        destroyNavigation;
})();
