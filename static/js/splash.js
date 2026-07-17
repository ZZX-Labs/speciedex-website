"use strict";

/*
==============================================================================
Speciedex.org
Splash Module
==============================================================================

Loaded by:

    /static/js/script.js

Responsibilities

    • Initialize the splash/hero section
    • Support scroll indicator
    • Support fade/visibility effects
    • Keep implementation isolated from other modules
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

    const SPLASH_SELECTOR =
        "[data-site-splash], .site-splash";

    const SCROLL_BUTTON_SELECTOR =
        "[data-scroll-down]";

    const VISIBLE_CLASS =
        "is-visible";

    const SCROLLED_CLASS =
        "is-scrolled";

    let splash = null;
    let initialized = false;
    let observer = null;

    /*
    --------------------------------------------------------------------------
    Initialize splash.
    --------------------------------------------------------------------------
    */

    function initializeSplash() {

        splash = document.querySelector(
            SPLASH_SELECTOR
        );

        if (!splash) {
            return;
        }

        if (initialized) {
            return;
        }

        initialized = true;

        splash.classList.add(
            VISIBLE_CLASS
        );

        initializeScrollButton();

        initializeObserver();
    }

    /*
    --------------------------------------------------------------------------
    Scroll button.
    --------------------------------------------------------------------------
    */

    function initializeScrollButton() {

        const button =
            splash.querySelector(
                SCROLL_BUTTON_SELECTOR
            );

        if (!button) {
            return;
        }

        button.addEventListener(
            "click",
            handleScrollButton,
            {
                passive: false
            }
        );
    }

    function handleScrollButton(event) {

        event.preventDefault();

        const target =
            document.querySelector(
                "main"
            );

        if (!target) {
            return;
        }

        target.scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    }

    /*
    --------------------------------------------------------------------------
    Observe splash visibility.
    --------------------------------------------------------------------------
    */

    function initializeObserver() {

        if (
            typeof IntersectionObserver !==
            "function"
        ) {
            return;
        }

        observer?.disconnect();

        observer =
            new IntersectionObserver(
                handleIntersection,
                {
                    threshold: 0.05
                }
            );

        observer.observe(splash);
    }

    function handleIntersection(entries) {

        const entry = entries[0];

        if (!entry) {
            return;
        }

        splash.classList.toggle(
            SCROLLED_CLASS,
            !entry.isIntersecting
        );

        document.body.classList.toggle(
            "splash-scrolled",
            !entry.isIntersecting
        );
    }

    /*
    --------------------------------------------------------------------------
    Cleanup.
    --------------------------------------------------------------------------
    */

    function destroySplash() {

        observer?.disconnect();

        observer = null;
        splash = null;
        initialized = false;
    }

    /*
    --------------------------------------------------------------------------
    Public API.
    --------------------------------------------------------------------------
    */

    Speciedex.initializeSplash =
        initializeSplash;

    Speciedex.destroySplash =
        destroySplash;

})();
