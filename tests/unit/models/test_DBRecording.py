from datetime import datetime

from models.DBRecording import DBRecording


class TestDBRecording:
    def test_init_required_fields(self):
        rec = DBRecording(id=1, name="test.hda", label="Test", duration=120, created_at=datetime(2026, 3, 27))
        assert rec.id == 1
        assert rec.name == "test.hda"
        assert rec.label == "Test"
        assert rec.duration == 120
        assert rec.created_at == datetime(2026, 3, 27)
        assert rec.transcript is None
        assert rec.summary is None
        assert rec.title is None
        assert rec.tags is None
        assert rec.notion_url is None

    def test_init_all_fields(self):
        rec = DBRecording(
            id=5,
            name="rec.hda",
            label="Full",
            duration=60,
            created_at=datetime(2026, 4, 1),
            transcript="Hello world",
            summary="# Summary",
            title="My Title",
            tags="tag1,tag2",
            notion_url="https://notion.so/page",
        )
        assert rec.transcript == "Hello world"
        assert rec.summary == "# Summary"
        assert rec.title == "My Title"
        assert rec.tags == "tag1,tag2"
        assert rec.notion_url == "https://notion.so/page"

    def test_from_dict_full(self):
        data = {
            "id": 10,
            "name": "2026Mar27-094938-Wip01",
            "label": "Meeting Notes",
            "duration": 300,
            "created_at": "2026-03-27T09:49:38",
            "transcript": "Some transcript",
            "summary": "Some summary",
            "title": "A Title",
            "tags": "meeting,notes",
            "notion_url": "https://notion.so/abc",
        }
        rec = DBRecording.from_dict(data)
        assert rec.id == 10
        assert rec.name == "2026Mar27-094938-Wip01"
        assert rec.label == "Meeting Notes"
        assert rec.duration == 300
        assert rec.created_at == datetime(2026, 3, 27, 9, 49, 38)
        assert rec.transcript == "Some transcript"
        assert rec.summary == "Some summary"
        assert rec.title == "A Title"
        assert rec.tags == "meeting,notes"
        assert rec.notion_url == "https://notion.so/abc"

    def test_from_dict_minimal(self):
        data = {
            "id": 1,
            "name": "test",
            "label": "Test",
            "duration": 10,
            "created_at": "2026-04-01T00:00:00",
        }
        rec = DBRecording.from_dict(data)
        assert rec.transcript is None
        assert rec.summary is None
        assert rec.title is None
        assert rec.tags is None
        assert rec.notion_url is None
