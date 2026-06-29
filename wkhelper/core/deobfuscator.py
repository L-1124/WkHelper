"""字体反混淆模块。"""

import hashlib
import json
import logging
import os
import re
import tempfile
from typing import Any

import niquests
from fontTools.ttLib import TTFont

logger = logging.getLogger(__name__)

# 加载预计算好的映射表 (glyph_hash -> real_unicode)
_MAPPING_FILE = os.path.join(os.path.dirname(__file__), "font_mapping.json")
_glyph_hash_to_uni: dict[str, int] = {}


def load_mapping() -> None:
    """懒加载映射表。"""
    global _glyph_hash_to_uni
    if not _glyph_hash_to_uni:
        try:
            with open(_MAPPING_FILE, encoding="utf-8") as f:
                mapping = json.load(f)
                # JSON 的 key 是 string，我们需要把它当成 string 用，值是 int
                _glyph_hash_to_uni = {k: int(v) for k, v in mapping.items()}
            logger.debug(f"已加载字体映射表，共 {len(_glyph_hash_to_uni)} 个字形映射")
        except Exception as e:
            logger.error(f"加载字体映射表失败: {e}")


def _hash_glyph(glyph, glyph_set) -> str:
    """计算字形的唯一标识。
    使用 DecomposingRecordingPen 展平所有组件，并序列化坐标点，确保与反混淆字体的结构一致。
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen

    pen = DecomposingRecordingPen(glyph_set)
    glyph.draw(pen)

    # 序列化所有路径指令
    # 格式类似: moveTo(10, 20)|lineTo(30, 40)...
    parts = []
    for cmd, args in pen.value:
        if args:
            if args and isinstance(args[0], tuple):
                args_str = ",".join([f"({x:g},{y:g})" if isinstance(x, (int, float)) else str(x) for (x, y) in args])
            else:
                args_str = ",".join([str(a) for a in args])
            parts.append(f"{cmd}:{args_str}")
        else:
            parts.append(cmd)

    data = "|".join(parts)
    return hashlib.sha512(data.encode("ascii")).hexdigest()


def _deobfuscate_text(text: str, obfuscated_to_real: dict[int, int]) -> str:
    """使用建立好的映射表将乱码文本还原为真实文本。"""
    if not text:
        return text

    result = []
    for char in text:
        codepoint = ord(char)
        # 仅处理中文字符区间的替换（通常混淆的是这部分）
        if 0x4E00 <= codepoint <= 0x9FFF or 0x3400 <= codepoint <= 0x4DBF or 0x20000 <= codepoint <= 0x2A6DF:
            if codepoint in obfuscated_to_real:
                result.append(chr(obfuscated_to_real[codepoint]))
            else:
                result.append(char)
        else:
            result.append(char)

    return "".join(result)


def _deobfuscate_html_str(html_str: Any, obfuscated_to_real: dict[int, int]) -> Any:
    """递归处理数据结构，仅对 xuetangx-com-encrypted-font 标签内的文本进行反混淆。"""
    if isinstance(html_str, str):
        # 只提取加密字体标签内的文本进行反混淆，其他文本原样保留
        _ENCRYPTED_RE = re.compile(
            r'<(span|div|p)\b[^>]*?class=["\'][^"\']*(?:xuetangx-com-encrypted-font|custom_ueditor_cn_body)[^"\']*["\'][^>]*>(.*?)</\1>',
            re.IGNORECASE | re.DOTALL,
        )

        def _replace(m: re.Match[str]) -> str:
            inner = m.group(2)
            return _deobfuscate_text(inner, obfuscated_to_real)

        return _ENCRYPTED_RE.sub(_replace, html_str)
    elif isinstance(html_str, list):
        return [_deobfuscate_html_str(item, obfuscated_to_real) for item in html_str]
    elif isinstance(html_str, dict):
        return {k: _deobfuscate_html_str(v, obfuscated_to_real) for k, v in html_str.items()}
    return html_str


async def deobfuscate_questions(questions: list[dict[str, Any]], font_url: str, client: niquests.AsyncSession) -> None:
    """下载混淆字体并对题目列表进行就地（in-place）反混淆处理。"""
    if not questions or not font_url:
        return

    load_mapping()
    if not _glyph_hash_to_uni:
        logger.warning("未能加载映射表，跳过反混淆步骤")
        return

    logger.debug(f"正在下载混淆字体用于解密: {font_url}")

    try:
        resp = await client.get(font_url)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"下载混淆字体失败，无法完成反混淆: {e}")
        return

    # 保存临时字体文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tmp:
        if resp.content is not None:
            tmp.write(resp.content)
        tmp_path = tmp.name

    obfuscated_to_real: dict[int, int] = {}

    try:
        font = TTFont(tmp_path)
        glyph_set = font.getGlyphSet()
        cmap = font.getBestCmap()
        if not cmap:
            logger.warning("未能获取字体 cmap 映射表")
            return

        # 建立 混淆码点 -> 真实码点 的映射
        for codepoint, glyph_name in cmap.items():
            if not (0x4E00 <= codepoint <= 0x9FFF or 0x3400 <= codepoint <= 0x4DBF or 0x20000 <= codepoint <= 0x2A6DF):
                continue

            glyph = glyph_set[glyph_name]
            glyph_hash = _hash_glyph(glyph, glyph_set)

            if glyph_hash in _glyph_hash_to_uni:
                real_unicode = _glyph_hash_to_uni[glyph_hash]
                obfuscated_to_real[codepoint] = real_unicode
            else:
                logger.debug(f"未能匹配字形 hash: {glyph_hash} (混淆码点: {hex(codepoint)})")

        logger.debug(f"成功建立了 {len(obfuscated_to_real)} 个字符的反混淆映射")

        # 就地修改 questions
        for q in questions:
            content = q.get("content", {})

            # 反混淆题干
            if "Body" in content:
                content["Body"] = _deobfuscate_html_str(content["Body"], obfuscated_to_real)
            elif "body" in content:
                content["body"] = _deobfuscate_html_str(content["body"], obfuscated_to_real)

            # 反混淆选项
            if "Options" in content:
                content["Options"] = _deobfuscate_html_str(content["Options"], obfuscated_to_real)
            elif "options" in content:
                content["options"] = _deobfuscate_html_str(content["options"], obfuscated_to_real)

    except Exception as e:
        logger.error(f"解析混淆字体进行还原时出错: {e}")
    finally:
        # 清理临时文件
        try:
            os.remove(tmp_path)
        except OSError:
            pass
