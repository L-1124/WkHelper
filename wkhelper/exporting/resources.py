"""导出资源下载与本地化处理。"""

import logging
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from wkhelper.domain.homework import OptionItem, QuestionDetail
from wkhelper.infra.http import Fetcher

logger = logging.getLogger(__name__)

__all__ = [
    "download_resource_file",
    "localize_question_detail",
    "localize_text_resources",
]

_IMG_PATTERN = re.compile(r"!\[(.*?)\]\((https?://[^\s\)\"\']+)\)")
_ATTACH_PATTERN = re.compile(
    r"\[(.*?)\]\((https?://[^\s\)\"\']+\.(?:csv|xlsx?|docx?|pdf|zip|rar|tar(?:\.gz)?|txt|json)[^\s\)\"\']*)\)",
    re.IGNORECASE,
)


def _extract_extension(url: str, default: str = ".png") -> str:
    path = unquote(urlparse(url).path)
    ext = Path(path).suffix.lower()
    return ext if ext and len(ext) <= 8 else default


def _safe_filename_part(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]", "_", name).strip("_")
    return cleaned[:30] if cleaned else "res"


async def download_resource_file(
    fetch: Fetcher,
    url: str,
    dest_path: Path,
) -> bool:
    """下载单个远程资源文件到本地。"""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        return True
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        content = await fetch(url)
        if content:
            dest_path.write_bytes(content)
            return True
        logger.debug(f"下载资源失败: 空内容, url={url}")
    except Exception as exc:
        logger.debug(f"下载资源异常: url={url}, err={exc}")
    return False


async def localize_text_resources(
    fetch: Fetcher,
    text: str,
    assets_dir: Path,
    filename_prefix: str,
    relative_base: str = "./assets",
) -> tuple[str, list[str]]:
    """扫描文本中的图片与附件 URL，下载并重写为规范的本地相对路径。"""
    downloaded_paths: list[str] = []
    rewritten_text = text

    img_matches = list(_IMG_PATTERN.finditer(text))
    for seq, m in enumerate(img_matches, 1):
        _alt, url = m.group(1), m.group(2)
        ext = _extract_extension(url, default=".png")
        filename = f"{filename_prefix}_{seq}{ext}"
        dest_path = assets_dir / filename
        ok = await download_resource_file(fetch, url, dest_path)
        if ok:
            rel_path = f"{relative_base}/{filename}"
            rewritten_text = rewritten_text.replace(url, rel_path)
            downloaded_paths.append(str(dest_path))

    attach_matches = list(_ATTACH_PATTERN.finditer(rewritten_text))
    for seq, m in enumerate(attach_matches, 1):
        link_title, url = m.group(1), m.group(2)
        ext = _extract_extension(url, default=".bin")
        safe_title = _safe_filename_part(link_title)
        filename = f"{filename_prefix}_attach_{seq}_{safe_title}{ext}"
        dest_path = assets_dir / filename
        ok = await download_resource_file(fetch, url, dest_path)
        if ok:
            rel_path = f"{relative_base}/{filename}"
            rewritten_text = rewritten_text.replace(url, rel_path)
            downloaded_paths.append(str(dest_path))

    return rewritten_text, downloaded_paths


async def localize_question_detail(
    fetch: Fetcher,
    detail: QuestionDetail,
    assets_dir: Path,
    relative_base: str = "./assets",
) -> QuestionDetail:
    """下载单题题干与选项中的图片与附件，并按规范命名改写路径。"""
    prefix = f"q_{detail.id}"
    new_prompt, body_res = await localize_text_resources(fetch, detail.prompt, assets_dir, f"{prefix}_body", relative_base=relative_base)
    all_res = list(body_res)

    new_options: list[OptionItem] = []
    for opt in detail.options:
        key_str = _safe_filename_part(opt.key)
        new_text, opt_res = await localize_text_resources(
            fetch, opt.text, assets_dir, f"{prefix}_opt_{key_str}", relative_base=relative_base
        )
        new_options.append(OptionItem(key=opt.key, text=new_text))
        all_res.extend(opt_res)

    detail.prompt = new_prompt
    detail.options = new_options
    detail.resources = all_res
    return detail
