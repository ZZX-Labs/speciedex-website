"use strict";

/*
==============================================================================
Speciedex.org
Splash Module
==============================================================================

Loaded by:

    /static/js/script.js

Responsibilities:

    • Initialize splash / hero sections
    • Support scroll-down controls
    • Track splash visibility
    • Respect reduced-motion preferences
    • Keep splash behavior isolated from other modules

==============================================================================
*/

(() => {
    const Speciedex =
        window.Speciedex =
        window.Speciedex || {};

    if (Speciedex.splashModuleLoaded) {
        return;
    }

    Speciedex.splashModuleLoaded = true;

    /*
    ==========================================================================
    Selectors / Classes
    ==========================================================================
    */

    const SPLASH_SELECTOR =
        "[data-site-splash], .site-splash, .splash";

    const SCROLL_BUTTON_SELECTOR =
        "[data-scroll-down]";

    const VISIBLE_CLASS =
        "is-visible";

    const SCROLLED_CLASS =
        "is-scrolled";

    /*
    ==========================================================================
    Internal State
    ==========================================================================
    */

    let splash = null;
    let scrollButton = null;
    let observer = null;
    let initialized = false;

    /*
    ==========================================================================
    Reduced Motion
    ==========================================================================
    */

    function prefersReducedMotion() {
        return (
            window.matchMedia &&
            window.matchMedia(
                "(prefers-reduced-motion: reduce)"
            ).matches
        );
    }

    /*
    ==========================================================================
    Initialize Splash
    ==========================================================================
    */

    function initializeSplash() {
        if (initialized) {
            return;
        }

        splash =
            document.querySelector(
                SPLASH_SELECTOR
            );

        if (!splash) {
            return;
        }

        initialized = true;

        splash.classList.add(
            VISIBLE_CLASS
        );

        initializeScrollButton();
        initializeObserver();

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:splash-ready",
                {
                    detail: {
                        splash
                    }
                }
            )
        );
    }

    /*
    ==========================================================================
    Scroll Button
    ==========================================================================
    */

    function initializeScrollButton() {
        scrollButton =
            splash.querySelector(
                SCROLL_BUTTON_SELECTOR
            );

        if (!scrollButton) {
            return;
        }

        scrollButton.removeEventListener(
            "click",
            handleScrollButton
        );

        scrollButton.addEventListener(
            "click",
            handleScrollButton
        );
    }

    function handleScrollButton(event) {
        event.preventDefault();

        const target =
            document.querySelector(
                "#main-content"
            ) ||
            document.querySelector(
                "main"
            );

        if (!target) {
            return;
        }

        target.scrollIntoView({
            behavior:
                prefersReducedMotion()
                    ? "auto"
                    : "smooth",

            block:
                "start"
        });

        if (
            typeof target.focus ===
            "function" &&
            target.hasAttribute(
                "tabindex"
            )
        ) {
            target.focus({
                preventScroll: true
            });
        }
    }

    /*
    ==========================================================================
    Intersection Observer
    ==========================================================================
    */

    function initializeObserver() {
        observer?.disconnect();
        observer = null;

        if (
            typeof IntersectionObserver !==
            "function"
        ) {
            return;
        }

        observer =
            new IntersectionObserver(
                handleIntersection,
                {
                    threshold: 0.05
                }
            );

        observer.observe(
            splash
        );
    }

    function handleIntersection(entries) {
        const entry =
            entries[0];

        if (
            !entry ||
            !splash
        ) {
            return;
        }

        const scrolled =
            !entry.isIntersecting;

        splash.classList.toggle(
            SCROLLED_CLASS,
            scrolled
        );

        document.body.classList.toggle(
            "splash-scrolled",
            scrolled
        );

        document.dispatchEvent(
            new CustomEvent(
                "speciedex:splash-visibility",
                {
                    detail: {
                        splash,
                        visible:
                            entry.isIntersecting,
                        ratio:
                            entry.intersectionRatio
                    }
                }
            )
        );
    }

    /*
    ==========================================================================
    Destroy Splash
    ==========================================================================
    */

    function destroySplash() {
        observer?.disconnect();
        observer = null;

        if (scrollButton) {
            scrollButton.removeEventListener(
                "click",
                handleScrollButton
            );
        }

        if (splash) {
            splash.classList.remove(
                VISIBLE_CLASS,
                SCROLLED_CLASS
            );
        }

        document.body.classList.remove(
            "splash-scrolled"
        );

        scrollButton = null;
        splash = null;
        initialized = false;
    }

    /*
    ==========================================================================
    Public API
    ==========================================================================
    */

    Speciedex.initializeSplash =
        initializeSplash;

    Speciedex.destroySplash =
        destroySplash;
})();
