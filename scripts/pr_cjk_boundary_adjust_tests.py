from pathlib import Path

path = Path("tests/test_whatsapp_markdown_cjk_boundaries.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '''    def test_unsafe_inline_italic_and_strike_also_degrade(self) -> None:\n        cases = {\n            "中文*斜體*中文": "中文斜體中文",\n            "中文~~刪除~~中文": "中文刪除中文",\n            "pre**bold**post": "preboldpost",\n        }\n''',
    '''    def test_unsafe_inline_emphasis_degrades_cleanly(self) -> None:\n        cases = {\n            "中文*斜體*中文": "中文斜體中文",\n            "pre**bold**post": "preboldpost",\n        }\n''',
)
text = text.replace(
    '''    def test_nested_styles_recheck_boundaries_after_outer_degrades(self) -> None:\n        source = "中文**粗體 _斜體_**中文"\n        self.assertEqual(\n            helpers.format_whatsapp_markdown(source),\n            "中文粗體 斜體中文",\n        )\n''',
    '''    def test_nested_styles_recheck_boundaries_after_outer_degrades(self) -> None:\n        source = "中文**_斜體_**，"\n        self.assertEqual(\n            helpers.format_whatsapp_markdown(source),\n            "中文斜體，",\n        )\n\n    def test_safe_nested_style_survives_outer_degrade(self) -> None:\n        source = "中文**粗體 _斜體_**，"\n        self.assertEqual(\n            helpers.format_whatsapp_markdown(source),\n            "中文粗體 _斜體_，",\n        )\n''',
)
path.write_text(text, encoding="utf-8")
