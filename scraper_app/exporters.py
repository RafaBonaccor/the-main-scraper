import csv
import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from .models import ExportOptions, ScrapeOutcome
from .utils import slugify_filename


def export_outcome(outcome: ScrapeOutcome, options: ExportOptions) -> list[Path]:
    output_dir = options.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = options.base_name.strip() if options.base_name else f"{outcome.source}_{timestamp}"
    safe_name = slugify_filename(base_name)

    exported_files: list[Path] = []
    formats = _normalize_formats(options.output_format)

    for fmt in formats:
        if fmt == "json":
            path = output_dir / f"{safe_name}.json"
            _write_json(path, outcome)
        elif fmt == "csv":
            path = output_dir / f"{safe_name}.csv"
            _write_csv(path, outcome.rows)
        elif fmt == "xlsx":
            path = output_dir / f"{safe_name}.xlsx"
            _write_xlsx(path, outcome.rows)
        else:
            continue

        exported_files.append(path)

    return exported_files


def _normalize_formats(raw_format: str) -> list[str]:
    normalized = (raw_format or "json").strip().lower()
    if normalized == "none":
        return []
    if normalized == "all":
        return ["json", "csv", "xlsx"]
    return [normalized]


def _write_json(path: Path, outcome: ScrapeOutcome) -> None:
    write_outcome_json(path, outcome)


def _write_csv(path: Path, rows: list[dict]) -> None:
    headers = _collect_headers(rows)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _write_xlsx(path: Path, rows: list[dict]) -> None:
    headers = _collect_headers(rows)
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Scrape"
    worksheet.append(headers)

    for row in rows:
        worksheet.append([row.get(header, "") for header in headers])

    for column in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        worksheet.column_dimensions[column[0].column_letter].width = min(max(max_length + 2, 12), 60)

    workbook.save(path)


def _collect_headers(rows: list[dict]) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()

    for row in rows:
        for key in row:
            if key in seen:
                continue
            seen.add(key)
            headers.append(key)

    return headers or ["value"]


def build_outcome_payload(outcome: ScrapeOutcome) -> dict:
    return {
        "source": outcome.source,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(outcome.rows),
        "meta": outcome.meta,
        "rows": outcome.rows,
    }


def write_outcome_json(path: Path, outcome: ScrapeOutcome) -> None:
    path.write_text(json.dumps(build_outcome_payload(outcome), ensure_ascii=False, indent=2), encoding="utf-8")
