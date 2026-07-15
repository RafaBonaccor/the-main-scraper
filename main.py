import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from scraper_app.bootstrap import ensure_project_venv


ensure_project_venv(__file__)

from scraper_app.contact_history import annotate_rows_with_contact_history, summarize_contact_status
from scraper_app.contact_runner import run_contact_action
from scraper_app.browser_launcher import open_browser_session
from scraper_app.exporters import export_outcome, write_outcome_json
from scraper_app.models import ExportOptions
from scraper_app.openai_screening import DEFAULT_REASONING_EFFORT, DEFAULT_SCREENING_MODEL
from scraper_app.runner import run_scraper
from scraper_app.ui import launch_gui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-source scraper with CSV/XLSX export.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("gui", help="Open the minimal desktop UI.")

    status_parser = subparsers.add_parser("status", help="Print machine-friendly project status.")
    _add_orchestrator_arguments(status_parser)

    browser_parser = subparsers.add_parser("browser", help="Open a browser manually using the selected profile.")
    browser_parser.add_argument("--url", default="https://www.google.com/maps", help="Initial URL to open.")
    browser_parser.add_argument(
        "--keep-open-seconds",
        default=0,
        type=int,
        help="Seconds to keep the browser open. Use 0 to wait until it is closed manually.",
    )
    browser_parser.add_argument(
        "--refresh-browser-profile",
        action="store_true",
        help="Refresh the persistent browser profile from the source Chrome data before opening.",
    )
    _add_orchestrator_arguments(browser_parser)
    _add_browser_arguments(browser_parser)

    run_parser = subparsers.add_parser("run", help="Run a scraper from the CLI.")
    contact_parser = subparsers.add_parser("contact", help="Run a contact/browser action for a supported source.")
    _add_orchestrator_arguments(run_parser)
    _add_orchestrator_arguments(contact_parser)

    run_subparsers = run_parser.add_subparsers(dest="source", required=True)
    contact_subparsers = contact_parser.add_subparsers(dest="contact_source", required=True)

    google_parser = run_subparsers.add_parser("google_maps", help="Use the Google Maps-specific scraper.")
    google_parser.add_argument("--search", required=True, help="Google Maps query, comma-separated categories, or full URL.")
    google_parser.add_argument("--city", default="", help="Optional city or comma-separated cities to search.")
    google_parser.add_argument("--province", default="", help="Optional province to append to the Google Maps query.")
    google_parser.add_argument("--country", default="", help="Optional country to append to the Google Maps query.")
    google_parser.add_argument("--max-results", default=25, type=int, help="Maximum number of results to keep for each category/city search.")
    google_parser.add_argument(
        "--exclude-sponsored",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exclude sponsored Maps results, which may be outside the requested area.",
    )
    google_parser.add_argument(
        "--include-details",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Open each Maps business page to extract website, phone, category, rating and reviews.",
    )
    google_parser.add_argument(
        "--audit-websites",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Inspect public business websites for email, social links and basic technical quality.",
    )
    google_parser.add_argument("--website-timeout-seconds", default=10.0, type=float, help="Timeout for each website page audit.")
    _add_orchestrator_arguments(google_parser)
    _add_browser_arguments(google_parser)
    _add_export_arguments(google_parser)

    vinted_parser = run_subparsers.add_parser("vinted", help="Extract Vinted search results and save them to SQLite.")
    vinted_parser.add_argument("--search", default="", help="Vinted search term or full catalog URL.")
    vinted_parser.add_argument(
        "--searches-file",
        default="",
        help="JSON file containing one or more Vinted searches with per-search max-results and max-price.",
    )
    vinted_parser.add_argument(
        "--max-results",
        default=100,
        type=int,
        help="Maximum results to keep. Use 0 to scroll until no new items are found.",
    )
    vinted_parser.add_argument(
        "--max-price",
        default="",
        help="Maximum item price to keep for this Vinted search. Leave empty for no price cap.",
    )
    vinted_parser.add_argument("--db-path", default="data/scraper.db", help="SQLite database path.")
    vinted_parser.add_argument(
        "--keep-browser-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Leave the Vinted browser open after extraction without waiting for a timer.",
    )
    vinted_parser.add_argument(
        "--refresh-browser-profile",
        action="store_true",
        help="Refresh the persistent profile from the source Chrome data before scraping Vinted.",
    )
    vinted_parser.add_argument(
        "--keep-open-seconds",
        default=0,
        type=int,
        help="Blocking fallback: seconds to keep Vinted open before closing. Use 0 to close immediately.",
    )
    _add_orchestrator_arguments(vinted_parser)
    _add_browser_arguments(vinted_parser)
    _add_export_arguments(vinted_parser)

    vinted_details_parser = run_subparsers.add_parser(
        "vinted_descriptions",
        help="Extract descriptions for selected Vinted links and update the database.",
    )
    vinted_details_parser.add_argument("--links-file", default="", help="UTF-8 text or JSON file containing one or more Vinted listing URLs.")
    vinted_details_parser.add_argument("--db-path", default="data/scraper.db", help="SQLite database path.")
    vinted_details_parser.add_argument(
        "--keep-browser-open",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Leave the Vinted browser open after extracting descriptions.",
    )
    vinted_details_parser.add_argument(
        "--refresh-browser-profile",
        action="store_true",
        help="Refresh the persistent profile from the source Chrome data before extracting descriptions.",
    )
    vinted_details_parser.add_argument(
        "--keep-open-seconds",
        default=0,
        type=int,
        help="Blocking fallback: seconds to keep Vinted open before closing. Use 0 to close immediately.",
    )
    _add_orchestrator_arguments(vinted_details_parser)
    _add_browser_arguments(vinted_details_parser)
    _add_export_arguments(vinted_details_parser)

    subito_parser = run_subparsers.add_parser("subito", help="Use the dedicated Subito scraper for listings.")
    subito_parser.add_argument(
        "--query",
        default="",
        help="Optional keyword query. You can also pass a full Subito URL here.",
    )
    subito_parser.add_argument("--region", default="lazio", help="Subito region slug or label.")
    subito_parser.add_argument("--city", default="roma", help="One or more Subito city slugs or labels, comma-separated.")
    subito_parser.add_argument("--category", default="offerte-lavoro", help="Subito category slug or label.")
    subito_parser.add_argument(
        "--job-keywords",
        default="",
        help="Comma-separated job keywords to run separately and merge, e.g. 'pulizie,colf,badante'.",
    )
    subito_parser.add_argument("--include-details", action="store_true", help="Visit each kept listing and extract the full description from the detail page.")
    subito_parser.add_argument("--llm-screening", action="store_true", help="Use OpenAI to rank Subito listings after geo filtering.")
    subito_parser.add_argument("--openai-model", default=DEFAULT_SCREENING_MODEL, help="OpenAI model used for listing screening.")
    subito_parser.add_argument(
        "--openai-reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default=DEFAULT_REASONING_EFFORT,
        help="Reasoning effort for the OpenAI screening step.",
    )
    subito_parser.add_argument("--max-results", default=25, type=int, help="Maximum number of listings to keep.")
    subito_parser.add_argument("--max-age-hours", default=0, type=int, help="Keep only listings from the last N hours. Use 0 to disable.")
    subito_parser.add_argument("--max-age-days", default=14, type=int, help="Skip listings older than this many days. Use 0 to disable.")
    subito_parser.add_argument(
        "--exact-age-days",
        default=-1,
        type=int,
        help="Keep only listings published exactly this many days ago. Example: 1 = yesterday. Use -1 to disable.",
    )
    subito_parser.add_argument("--anchor-place", default="Morlupo", help="Reference place used for distance sorting.")
    subito_parser.add_argument("--max-distance-km", default=30.0, type=float, help="Maximum distance in km from the anchor place.")
    subito_parser.add_argument("--nearby-only", action="store_true", help="Keep only rows accepted by the distance filter.")
    _add_orchestrator_arguments(subito_parser)
    _add_browser_arguments(subito_parser)
    _add_export_arguments(subito_parser)

    custom_parser = run_subparsers.add_parser("custom_site", help="Use the generic custom-site scraper.")
    custom_parser.add_argument("--url", required=True, help="Target URL.")
    custom_parser.add_argument("--item-selector", required=True, help="CSS selector for each scraped item.")
    custom_parser.add_argument("--name-selector", default="", help="CSS selector for the item name.")
    custom_parser.add_argument("--phone-selector", default="", help="CSS selector for phone nodes.")
    custom_parser.add_argument("--link-selector", default="", help="CSS selector for item links.")
    custom_parser.add_argument(
        "--cookie-reject-texts",
        default="",
        help="Comma-separated button texts to try for rejecting cookie banners.",
    )
    _add_orchestrator_arguments(custom_parser)
    _add_browser_arguments(custom_parser)
    _add_export_arguments(custom_parser)

    contact_subito_parser = contact_subparsers.add_parser("subito", help="Open the Subito contact flow for a listing.")
    contact_subito_parser.add_argument("--link", default="", help="Full Subito listing URL.")
    contact_subito_parser.add_argument("--links-file", default="", help="UTF-8 text or JSON file containing one or more listing URLs.")
    contact_subito_parser.add_argument("--attachment", default="", help="Optional file path to upload as attachment.")
    contact_subito_parser.add_argument("--message", default="", help="Optional message to fill before sending.")
    contact_subito_parser.add_argument("--submit", action="store_true", help="Actually click the final send button.")
    contact_subito_parser.add_argument(
        "--delay-between-seconds",
        default=2,
        type=int,
        help="Delay between listings when using links-file.",
    )
    contact_subito_parser.add_argument(
        "--keep-open-seconds",
        default=120,
        type=int,
        help="Keep the browser open for this many seconds after the contact flow.",
    )
    contact_subito_parser.add_argument(
        "--login-wait-seconds",
        default=240,
        type=int,
        help="How long to wait for a manual Subito login before failing the contact flow.",
    )
    _add_orchestrator_arguments(contact_subito_parser)
    _add_browser_arguments(contact_subito_parser)
    contact_subito_parser.set_defaults(browser_mode="sessione_persistente")

    return parser


def _add_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "csv", "xlsx", "all", "none"), default="all")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--filename", default="")
    parser.add_argument("--ui-result-json", default="")


def _add_orchestrator_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print a stable JSON payload to stdout for orchestrators.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable stdout summary.",
    )


def _add_browser_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--browser-mode",
        choices=("isolated", "chrome_normale", "profilo_personalizzato", "sessione_persistente", "saved_profile", "custom_profile", "persistent_profile", "persistent_session"),
        default="chrome_normale",
    )
    parser.add_argument("--browser-user-data-dir", default="")
    parser.add_argument("--browser-profile-directory", default="Default")
    parser.add_argument("--slow-mode", action="store_true", help="Use slower, more conservative browser pacing for unstable connections.")
    parser.add_argument("--action-delay-seconds", default=1.5, type=float, help="Pause between interactive browser actions.")
    parser.add_argument("--page-settle-seconds", default=3.0, type=float, help="Extra wait after page navigation before interacting.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    stdout_json = bool(getattr(args, "stdout_json", False))
    quiet = bool(getattr(args, "quiet", False))

    if args.command in (None, "gui"):
        launch_gui(Path(__file__).resolve())
        return 0

    if args.command == "status":
        payload = _build_status_payload()
        _emit_payload(payload, stdout_json=stdout_json, quiet=quiet)
        return 0

    if args.command == "browser":
        try:
            result = open_browser_session(
                url=args.url,
                keep_open_seconds=args.keep_open_seconds,
                browser_mode=args.browser_mode,
                browser_user_data_dir=args.browser_user_data_dir,
                browser_profile_directory=args.browser_profile_directory,
                refresh_browser_profile=args.refresh_browser_profile,
            )
            payload = {
                "ok": bool(result.get("ok")),
                "schema_version": "1.1",
                "command": "browser",
                "generated_at": _now_iso(),
                "result": result,
                "normalized": _normalize_browser_result(result),
            }
            _emit_payload(payload, stdout_json=stdout_json, quiet=quiet)
            return 0 if result.get("ok") else 1
        except Exception as exc:
            return _handle_runtime_error(exc, command="browser", stdout_json=stdout_json, quiet=quiet)

    if args.command == "contact":
        payload = vars(args).copy()
        source = payload.pop("contact_source")
        payload.pop("command", None)
        payload.pop("stdout_json", None)
        payload.pop("quiet", None)
        try:
            result = run_contact_action(source=source, **payload) or {
                "ok": False,
                "error": "Contact action returned no result.",
            }
            response_payload = {
                "ok": bool(result.get("ok")),
                "schema_version": "1.1",
                "command": "contact",
                "source": source,
                "generated_at": _now_iso(),
                "result": result,
                "normalized": _normalize_contact_result(source, result),
            }
            _emit_payload(response_payload, stdout_json=stdout_json, quiet=quiet)
            return 0 if result.get("ok") else 1
        except Exception as exc:
            return _handle_runtime_error(exc, command="contact", source=source, stdout_json=stdout_json, quiet=quiet)

    payload = vars(args).copy()
    source = payload.pop("source")
    payload.pop("command", None)
    payload.pop("stdout_json", None)
    payload.pop("quiet", None)
    try:
        outcome = run_scraper(source=source, **payload)
        outcome.rows = annotate_rows_with_contact_history(outcome.rows)
        outcome.meta["contact_counts"] = summarize_contact_status(outcome.rows)
        export_paths = export_outcome(
            outcome,
            ExportOptions(
                output_dir=Path(args.output_dir),
                output_format=args.format,
                base_name=args.filename,
            ),
        )
        if args.ui_result_json:
            write_outcome_json(Path(args.ui_result_json), outcome)

        response_payload = {
            "ok": True,
            "schema_version": "1.1",
            "command": "run",
            "source": outcome.source,
            "generated_at": _now_iso(),
            "row_count": len(outcome.rows),
            "meta": outcome.meta,
            "rows": outcome.rows,
            "files": [str(path) for path in export_paths],
            "normalized": _normalize_run_result(outcome.source, outcome.rows, outcome.meta, export_paths),
        }
        _emit_payload(response_payload, stdout_json=stdout_json, quiet=quiet)
        return 0
    except Exception as exc:
        return _handle_runtime_error(exc, command="run", source=source, stdout_json=stdout_json, quiet=quiet)


def _emit_payload(payload: dict, *, stdout_json: bool, quiet: bool) -> None:
    if stdout_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    if quiet:
        return
    _print_human_payload(payload)


def _print_human_payload(payload: dict) -> None:
    command = str(payload.get("command", "") or "").strip()
    if command == "status":
        print("Status: ok")
        print(f"Project root: {payload.get('project_root', '')}")
        print(f"Python: {payload.get('python_executable', '')}")
        print(f"Recommended browser mode: {payload.get('recommended_browser_mode', '')}")
        print(f"GUI available: {payload.get('features', {}).get('gui', False)}")
        return
    if command == "browser":
        print(f"Browser result: {payload.get('result', {})}")
        return
    if command == "contact":
        source = str(payload.get("source", "") or "")
        result = payload.get("result", {}) or {}
        print(f"Action source: {source}")
        for key, value in result.items():
            print(f"{key}: {value}")
        return
    if command == "run":
        print(f"Source: {payload.get('source', '')}")
        print(f"Rows: {payload.get('row_count', 0)}")
        meta = payload.get("meta", {}) or {}
        if meta:
            print("Meta:")
            for key, value in meta.items():
                print(f"  - {key}: {value}")
        print("Files:")
        for path in payload.get("files", []) or []:
            print(f"  - {path}")
        return
    print(payload)


def _handle_runtime_error(
    exc: Exception,
    *,
    command: str,
    source: str = "",
    stdout_json: bool,
    quiet: bool,
) -> int:
    payload = {
        "ok": False,
        "schema_version": "1.1",
        "command": command,
        "source": source,
        "generated_at": _now_iso(),
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
    }
    _emit_payload(payload, stdout_json=stdout_json, quiet=quiet)
    return 1


def _build_status_payload() -> dict:
    project_root = Path(__file__).resolve().parent
    venv_candidates = [
        project_root / ".venv" / "Scripts" / "python.exe",
        project_root.parent / ".venv" / "Scripts" / "python.exe",
    ]
    active_venv = next((str(path) for path in venv_candidates if path.exists()), "")
    return {
        "ok": True,
        "schema_version": "1.1",
        "command": "status",
        "generated_at": _now_iso(),
        "project_root": str(project_root),
        "cwd": str(Path.cwd()),
        "python_executable": sys.executable,
        "platform": os.name,
        "active_venv_candidate": active_venv,
        "recommended_browser_mode": "sessione_persistente",
        "supported_sources": ["google_maps", "vinted", "vinted_descriptions", "subito", "custom_site"],
        "supported_contact_sources": ["subito"],
        "features": {
            "gui": True,
            "browser_command": True,
            "stdout_json": True,
            "format_none": True,
            "openai_screening": True,
            "vinted_database": True,
        },
        "paths": {
            "output_dir": str((project_root / "output").resolve()),
            "data_dir": str((project_root / "data").resolve()),
            "error_logs_dir": str((project_root / "error_logs").resolve()),
            "vinted_db_default": str((project_root / "data" / "scraper.db").resolve()),
            "readme": str((project_root / "README").resolve()),
            "agents_doc": str((project_root / "AGENTS.md").resolve()),
        },
    }


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_run_result(source: str, rows: list[dict], meta: dict, export_paths: list[Path]) -> dict:
    normalized_rows = [_normalize_row(source, row) for row in rows]
    return {
        "source": source,
        "row_count": len(rows),
        "exported_files": [str(path) for path in export_paths],
        "meta_summary": _normalize_meta_summary(source, meta),
        "rows": normalized_rows,
    }


def _normalize_meta_summary(source: str, meta: dict) -> dict:
    if source == "vinted":
        return {
            "search_term": str(meta.get("search_term", "") or meta.get("search", "") or ""),
            "row_count": int(meta.get("row_count", 0) or 0),
            "db_path": str(meta.get("db_path", "") or ""),
        }
    if source == "subito":
        return {
            "query": str(meta.get("query", "") or ""),
            "cities": list(meta.get("cities", []) or []),
            "row_count": int(meta.get("row_count", 0) or 0),
            "query_errors_count": len(list(meta.get("query_errors", []) or [])),
            "geo_counts": dict(meta.get("geo_counts", {}) or {}),
            "screening_counts": dict(meta.get("screening_counts", {}) or {}),
        }
    if source == "google_maps":
        return {
            "search": str(meta.get("search", "") or ""),
            "cities": list(meta.get("cities", []) or []),
            "row_count": int(meta.get("row_count", 0) or 0),
            "lead_priority_counts": dict(meta.get("lead_priority_counts", {}) or {}),
        }
    return {
        "row_count": int(meta.get("row_count", 0) or 0),
    }


def _normalize_row(source: str, row: dict) -> dict:
    if source == "vinted":
        return {
            "id": str(row.get("item_id", "") or ""),
            "title": str(row.get("name", "") or ""),
            "link": str(row.get("link", "") or ""),
            "search_term": str(row.get("search_term", "") or ""),
            "tag": str(row.get("tag", "") or ""),
            "secondary_badge_text": str(row.get("secondary_badge_text", "") or ""),
            "has_ricercato_badge": bool(row.get("has_ricercato_badge", False)),
            "price_text": str(row.get("price", "") or ""),
            "price_value": _safe_float_or_none(row.get("price_value")),
            "shipping_text": str(row.get("shipping_price", "") or ""),
            "shipping_value": _safe_float_or_none(row.get("shipping_price_value")),
            "total_text": str(row.get("total_price", "") or ""),
            "total_value": _safe_float_or_none(row.get("total_price_value")),
            "description": str(row.get("description", "") or ""),
            "currency": str(row.get("currency", "") or ""),
            "first_seen_at": str(row.get("first_seen_at", "") or ""),
            "last_seen_at": str(row.get("last_seen_at", row.get("extracted_at", "")) or ""),
        }
    if source == "subito":
        return {
            "title": str(row.get("title", "") or ""),
            "company": str(row.get("company", "") or ""),
            "location": str(row.get("location", "") or ""),
            "link": str(row.get("link", "") or ""),
            "published_at": str(row.get("published_at", "") or ""),
            "published_datetime_iso": str(row.get("published_datetime_iso", "") or row.get("published_date_iso", "") or ""),
            "distance_km": _safe_float_or_none(row.get("distance_km")),
            "geo_decision": str(row.get("geo_decision", "") or ""),
            "screening_decision": str(row.get("screening_decision", "") or ""),
            "screening_score": _safe_float_or_none(row.get("screening_score")),
            "contact_status": str(row.get("contact_status", "") or ""),
            "price_text": str(row.get("price", "") or ""),
            "description": str(row.get("description", "") or ""),
        }
    if source == "google_maps":
        return {
            "name": str(row.get("name", "") or ""),
            "category": str(row.get("category", "") or ""),
            "city": str(row.get("city", row.get("location", "")) or ""),
            "address": str(row.get("address", "") or ""),
            "link": str(row.get("link", "") or ""),
            "website": str(row.get("website_final_url", row.get("website", "")) or ""),
            "phone": str(row.get("phone", "") or ""),
            "email": str(row.get("email", row.get("website_emails", "")) or ""),
            "rating": _safe_float_or_none(row.get("rating")),
            "reviews_count": _safe_int_or_none(row.get("reviews_count")),
            "opportunity_score": _safe_float_or_none(row.get("opportunity_score")),
            "lead_priority": str(row.get("lead_priority", "") or ""),
            "website_status": str(row.get("website_status", "") or ""),
        }
    return {
        "link": str(row.get("link", "") or ""),
        "title": str(row.get("title", row.get("name", "")) or ""),
    }


def _normalize_contact_result(source: str, result: dict) -> dict:
    if source == "subito":
        normalized_results = []
        for item in list(result.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            normalized_results.append(
                {
                    "link": str(item.get("link", "") or ""),
                    "ok": bool(item.get("ok")),
                    "prepared": bool(item.get("prepared")),
                    "submitted": bool(item.get("submitted")),
                    "attachment_uploaded": bool(item.get("attachment_uploaded")),
                    "message_filled": bool(item.get("message_filled")),
                    "login_required": bool(item.get("login_required")),
                    "current_url": str(item.get("current_url", "") or ""),
                }
            )
        return {
            "source": source,
            "links_count": _safe_int_or_none(result.get("links_count")),
            "prepared_count": _safe_int_or_none(result.get("prepared_count")),
            "sent_count": _safe_int_or_none(result.get("sent_count")),
            "failed_count": _safe_int_or_none(result.get("failed_count")),
            "submit": bool(result.get("submit")),
            "results": normalized_results,
        }
    return {"source": source}


def _normalize_browser_result(result: dict) -> dict:
    return {
        "ok": bool(result.get("ok")),
        "url": str(result.get("url", "") or ""),
        "browser_mode": str(result.get("browser_mode", "") or ""),
        "keep_open_seconds": _safe_int_or_none(result.get("keep_open_seconds")),
    }


def _safe_float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
