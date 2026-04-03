from models.HiDockRecording import HiDockRecording


class TestHiDockRecording:
    def test_default_values(self):
        rec = HiDockRecording()
        assert rec.name == ""
        assert rec.create_date == ""
        assert rec.create_time == ""
        assert rec.length == 0
        assert rec.recording_type == 0
        assert rec.duration == 0.0
        assert rec.signature == ""

    def test_set_attributes(self):
        rec = HiDockRecording()
        rec.name = "2026Mar27-094938-Wip01.hda"
        rec.create_date = "2026/03/27"
        rec.create_time = "09:49:38"
        rec.length = 80000
        rec.recording_type = 1
        rec.duration = 10.0
        rec.signature = "abcdef0123456789"

        assert rec.name == "2026Mar27-094938-Wip01.hda"
        assert rec.length == 80000
        assert rec.duration == 10.0
