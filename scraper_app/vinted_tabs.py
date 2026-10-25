"""Small helpers for keeping Vinted search and item checks in separate tabs."""

from botasaurus.browser import cdp


def _minimize_if_configured(driver, tab, *, log_prefix: str) -> None:
    config = getattr(driver, "config", None)
    arguments = getattr(config, "arguments", ()) or ()
    if "--start-minimized" not in arguments:
        return
    try:
        tab.minimize()
        print(f"{log_prefix} browser-window-minimized", flush=True)
    except Exception as exc:
        print(f"{log_prefix} browser-window-minimize-failed error={exc}", flush=True)


def navigate_vinted_status_tab(driver, target_url: str, *, log_prefix: str = "[vinted-tabs]") -> bool:
    """Navigate a reusable second tab, preserving the original search tab."""
    browser_session = getattr(driver, "_browser", None)
    if browser_session is None:
        return False
    primary_tab = getattr(browser_session, "_agent_lab_vinted_primary_tab", None)
    if primary_tab is None:
        primary_tab = getattr(driver, "_tab", None)
        if primary_tab is None:
            return False
        browser_session._agent_lab_vinted_primary_tab = primary_tab

    status_tab = getattr(browser_session, "_agent_lab_vinted_status_tab", None)
    if status_tab is None:
        try:
            driver._tab = primary_tab
            status_tab = driver.open_link_in_new_tab(
                "about:blank",
                wait=None,
                timeout=1,
            )
            browser_session._agent_lab_vinted_status_tab = status_tab
            driver._tab = status_tab
            # about:blank is ready immediately. Minimize before loading the
            # listing so the page's network/render time happens in background.
            _minimize_if_configured(driver, status_tab, log_prefix=log_prefix)
            status_tab.send(cdp.page.navigate(url=target_url))
            print(f"{log_prefix} status-tab-created item_url={target_url}", flush=True)
        except Exception as exc:
            driver._tab = primary_tab
            print(f"{log_prefix} status-tab-create-failed fallback=primary error={exc}", flush=True)
            return False
    else:
        try:
            driver._tab = status_tab
            status_tab.send(cdp.page.navigate(url=target_url))
            print(f"{log_prefix} status-tab-navigate item_url={target_url}", flush=True)
        except Exception as exc:
            driver._tab = primary_tab
            print(f"{log_prefix} status-tab-navigate-failed fallback=primary error={exc}", flush=True)
            return False
    driver._tab = status_tab
    # Subsequent navigations use CDP without changing the native window state.
    return True


def restore_vinted_primary_tab(driver, *, activate: bool = True, log_prefix: str = "[vinted-tabs]") -> None:
    browser_session = getattr(driver, "_browser", None)
    primary_tab = getattr(browser_session, "_agent_lab_vinted_primary_tab", None)
    if browser_session is None or primary_tab is None:
        return
    try:
        driver._tab = primary_tab
        # Assigning the Botasaurus tab is sufficient for the next operation.
        # Activating the CDP target restores the minimized native Chrome window
        # on macOS, which caused a foreground/minimize cycle for every item.
        print(f"{log_prefix} primary-tab-selected-in-driver", flush=True)
    except Exception as exc:
        driver._tab = primary_tab
        print(f"{log_prefix} primary-tab-restore-warning error={exc}", flush=True)
