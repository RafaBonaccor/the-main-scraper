import argparse
from pathlib import Path

from scraper_app.bootstrap import ensure_project_venv


ensure_project_venv(__file__)

from scraper_app.exporters import export_outcome
from scraper_app.models import ExportOptions
from scraper_app.runner import run_scraper
from scraper_app.ui import launch_gui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-source scraper with CSV/XLSX export.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("gui", help="Open the minimal desktop UI.")

    run_parser = subparsers.add_parser("run", help="Run a scraper from the CLI.")

    run_subparsers = run_parser.add_subparsers(dest="source", required=True)

    google_parser = run_subparsers.add_parser("google_maps", help="Use the Google Maps-specific scraper.")
    google_parser.add_argument("--search", required=True, help="Google Maps query or full URL.")
    google_parser.add_argument("--city", default="", help="Optional city to append to the Google Maps query.")
    google_parser.add_argument("--max-results", default=25, type=int, help="Maximum number of results to keep.")
    _add_export_arguments(google_parser)

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
    _add_export_arguments(custom_parser)

    return parser


def _add_export_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("json", "csv", "xlsx", "all"), default="all")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--filename", default="")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command in (None, "gui"):
        launch_gui(Path(__file__).resolve())
        return 0

    payload = vars(args).copy()
    source = payload.pop("source")
    payload.pop("command", None)
    outcome = run_scraper(source=source, **payload)
    export_paths = export_outcome(
        outcome,
        ExportOptions(
            output_dir=Path(args.output_dir),
            output_format=args.format,
            base_name=args.filename,
        ),
    )

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
