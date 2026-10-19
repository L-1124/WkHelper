"""字体反混淆标签匹配测试。"""

from wkhelper.core.deobfuscator import _deobfuscate_html_str


def test_encrypted_tags_with_attributes_are_replaced() -> None:
    """加密标记可能带行内样式/额外 class，也可能出现在 pre 代码块中。"""
    mapping = {ord("甲"): ord("乙")}
    html = '<span style="color:red" class="xuetangx-com-encrypted-font math">甲</span><pre class="xuetangx-com-encrypted-font">甲</pre>'
    result = _deobfuscate_html_str(html, mapping)
    assert result.count("乙") == 2
    assert "甲" not in result
    assert '<pre class="xuetangx-com-encrypted-font">乙</pre>' in result
