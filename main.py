import argparse
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
    _add_browser_arguments(browser_parser)

    run_parser = subparsers.add_parser("run", help="Run a scraper from the CLI.")
    contact_parser = subparsers.add_parser("contact", help="Run a contact/browser action for a supported source.")

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
    _add_browser_arguments(google_parser)
    _add_export_arguments(google_parser)

    vinted_parser = run_subparsers.add_parser("vinted", help="Extract Vinted search results and save them to SQLite.")
    vinted_parser.add_argument("--search", required=True, help="Vinted search term or full catalog URL.")
    vinted_parser.add_argument(
        "--max-results",
        default=100,
        type=int,
        help="Maximum results to keep. Use 0 to scroll until no new items are found.",
    )
    vinted_parser.add_argument("--db-path", default="data/scraper.db", help="SQLite database path.")
    vinted_parser.add_argument(
        "--keep-browser-open",
        action="store_true",
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
        action="store_true",
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
    _add_browser_arguments(contact_subito_parser)
    contact_subito_parser.set_defaults(browser_mode="sessione_persistente")

    return parser


def _add_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "csv", "xlsx", "all"), default="all")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--filename", default="")
    parser.add_argument("--ui-result-json", default="")


def _add_browser_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--browser-mode",
        choices=("isolated", "chrome_normale", "profilo_personalizzato", "sessione_persistente", "saved_profile", "custom_profile", "persistent_profile", "persistent_session"),
        default="isolated",
    )
    parser.add_argument("--browser-user-data-dir", default="")
    parser.add_argument("--browser-profile-directory", default="Default")
    parser.add_argument("--slow-mode", action="store_true", help="Use slower, more conservative browser pacing for unstable connections.")
    parser.add_argument("--action-delay-seconds", default=1.5, type=float, help="Pause between interactive browser actions.")
    parser.add_argument("--page-settle-seconds", default=3.0, type=float, help="Extra wait after page navigation before interacting.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command in (None, "gui"):
        launch_gui(Path(__file__).resolve())
        return 0

    if args.command == "browser":
        result = open_browser_session(
            url=args.url,
            keep_open_seconds=args.keep_open_seconds,
            browser_mode=args.browser_mode,
            browser_user_data_dir=args.browser_user_data_dir,
            browser_profile_directory=args.browser_profile_directory,
            refresh_browser_profile=args.refresh_browser_profile,
        )
        print(f"Browser result: {result}")
        return 0 if result.get("ok") else 1

    if args.command == "contact":
        payload = vars(args).copy()
        source = payload.pop("contact_source")
        payload.pop("command", None)
        result = run_contact_action(source=source, **payload) or {
            "ok": False,
            "error": "Contact action returned no result.",
        }
        print(f"Action source: {source}")
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0 if result.get("ok") else 1

    payload = vars(args).copy()
    source = payload.pop("source")
    payload.pop("command", None)
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

    print(f"Source: {outcome.source}")
    print(f"Rows: {len(outcome.rows)}")
    if outcome.meta:
        print("Meta:")
        for key, value in outcome.meta.items():
            print(f"  - {key}: {value}")
    print("Files:")
    for path in export_paths:
        print(f"  - {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
