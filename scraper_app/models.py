from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScrapeOutcome:
    source: str
    rows: list[dict[str, Any]]
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportOptions:
    output_dir: Path
    output_format: str
    base_name: str = ""
