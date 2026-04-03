import pytest

from services.NotionService import NotionService


class TestNotionServiceNormalizeId:
    """Unit tests for _normalize_id (static, no API calls)."""

    def test_plain_32_hex(self):
        raw = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        assert NotionService._normalize_id(raw) == raw

    def test_uuid_with_dashes(self):
        raw = "a1b2c3d4-e5f6-a1b2-c3d4-e5f6a1b2c3d4"
        assert NotionService._normalize_id(raw) == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

    def test_notion_url(self):
        raw = "https://www.notion.so/myworkspx/My-Titlx-12345678901234567890123456789012"
        assert NotionService._normalize_id(raw) == "12345678901234567890123456789012"

    def test_url_with_query_params(self):
        raw = "https://notion.so/12345678-9012-3456-7890-123456789012?v=123"
        assert NotionService._normalize_id(raw) == "12345678901234567890123456789012"

    def test_empty_string(self):
        assert NotionService._normalize_id("") == ""

    def test_trailing_slash(self):
        raw = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4/"
        assert NotionService._normalize_id(raw) == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

    def test_uppercase_hex(self):
        raw = "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4"
        assert NotionService._normalize_id(raw) == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"


class TestNotionServiceIsConfigured:
    def test_configured(self):
        svc = NotionService(api_key="test-key", parent_page_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        assert svc.is_configured is True

    def test_not_configured_no_key(self):
        svc = NotionService(api_key="", parent_page_id="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
        assert svc.is_configured is False

    def test_not_configured_no_page_id(self):
        svc = NotionService(api_key="test-key", parent_page_id="")
        assert svc.is_configured is False

    def test_not_configured_both_empty(self):
        svc = NotionService(api_key="", parent_page_id="")
        assert svc.is_configured is False


class TestNotionServiceBlocks:
    """Test the static/class block-building methods."""

    def test_callout_block(self):
        block = NotionService._callout_block("Hello world")
        assert block["type"] == "callout"
        assert block["callout"]["rich_text"][0]["text"]["content"] == "Hello world"
        assert block["callout"]["icon"]["emoji"] == "📋"

    def test_divider_block(self):
        block = NotionService._divider_block()
        assert block["type"] == "divider"

    def test_rich_text_plain(self):
        rt = NotionService._rich_text("plain text")
        assert rt["text"]["content"] == "plain text"
        assert "annotations" not in rt

    def test_rich_text_bold(self):
        rt = NotionService._rich_text("bold text", bold=True)
        assert rt["annotations"]["bold"] is True

    def test_rich_text_italic(self):
        rt = NotionService._rich_text("italic text", italic=True)
        assert rt["annotations"]["italic"] is True

    def test_rich_text_bold_italic(self):
        rt = NotionService._rich_text("both", bold=True, italic=True)
        assert rt["annotations"]["bold"] is True
        assert rt["annotations"]["italic"] is True

    def test_headers(self):
        svc = NotionService(api_key="sk-test", parent_page_id="abc123")
        headers = svc._headers()
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Notion-Version"] == "2022-06-28"
        assert headers["Content-Type"] == "application/json"


class TestNotionServiceParseInline:
    def test_plain_text(self):
        segments = NotionService._parse_inline("Hello world")
        assert len(segments) == 1
        assert segments[0]["text"]["content"] == "Hello world"

    def test_bold(self):
        segments = NotionService._parse_inline("This is **bold** text")
        texts = [(s["text"]["content"], s.get("annotations", {})) for s in segments]
        assert ("bold", {"bold": True}) in texts

    def test_italic(self):
        segments = NotionService._parse_inline("This is *italic* text")
        texts = [(s["text"]["content"], s.get("annotations", {})) for s in segments]
        assert ("italic", {"italic": True}) in texts

    def test_mixed(self):
        segments = NotionService._parse_inline("plain **bold** and *italic*")
        assert len(segments) >= 3


class TestNotionServiceMarkdownToBlocks:
    def test_heading_1(self):
        blocks = NotionService._markdown_to_blocks("# Title")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_1"

    def test_heading_2(self):
        blocks = NotionService._markdown_to_blocks("## Subtitle")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_2"

    def test_heading_3(self):
        blocks = NotionService._markdown_to_blocks("### Section")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_3"

    def test_bullet_list_dash(self):
        blocks = NotionService._markdown_to_blocks("- Item one\n- Item two")
        assert len(blocks) == 2
        assert all(b["type"] == "bulleted_list_item" for b in blocks)

    def test_bullet_list_asterisk(self):
        blocks = NotionService._markdown_to_blocks("* Item")
        assert blocks[0]["type"] == "bulleted_list_item"

    def test_numbered_list(self):
        blocks = NotionService._markdown_to_blocks("1. First\n2. Second")
        assert len(blocks) == 2
        assert all(b["type"] == "numbered_list_item" for b in blocks)

    def test_paragraph(self):
        blocks = NotionService._markdown_to_blocks("Just a regular paragraph.")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"

    def test_empty_lines_skipped(self):
        blocks = NotionService._markdown_to_blocks("Paragraph one\n\n\nParagraph two")
        assert len(blocks) == 2
        assert all(b["type"] == "paragraph" for b in blocks)

    def test_mixed_markdown(self):
        md = "# Title\n\nSome text.\n\n- Bullet 1\n- Bullet 2\n\n## Section\n\n1. Numbered"
        blocks = NotionService._markdown_to_blocks(md)
        types = [b["type"] for b in blocks]
        assert "heading_1" in types
        assert "paragraph" in types
        assert "bulleted_list_item" in types
        assert "heading_2" in types
        assert "numbered_list_item" in types

    def test_empty_markdown(self):
        blocks = NotionService._markdown_to_blocks("")
        assert blocks == []

    def test_publish_not_configured(self):
        svc = NotionService(api_key="", parent_page_id="")
        result = svc.publish_summary(title="Test", summary_markdown="# Hello")
        assert result["ok"] is False
        assert "not configured" in result["error"]
