"""导出结果。"""

from dataclasses import dataclass


@dataclass
class OutputFileResult:
    output_file: str
    written: bool


@dataclass
class ExportResult:
    file_path: str | None
    homeworks_count: int
    questions_count: int
    resources_count: int = 0
