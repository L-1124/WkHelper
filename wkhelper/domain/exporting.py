"""导出结果。"""

from wkhelper.domain.model import Model


class OutputFileResult(Model):
    output_file: str
    written: bool


class ExportResult(Model):
    file_path: str | None
    homeworks_count: int
    questions_count: int
    resources_count: int = 0
