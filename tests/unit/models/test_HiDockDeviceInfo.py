from models.HiDockDeviceInfo import HiDockDeviceInfo


class TestHiDockDeviceInfo:
    def test_init(self):
        info = HiDockDeviceInfo(model="HiDock H1", version="1.2.3", serial="ABC123")
        assert info.model == "HiDock H1"
        assert info.version == "1.2.3"
        assert info.serial == "ABC123"

    def test_str(self):
        info = HiDockDeviceInfo(model="HiDock P1", version="2.0.1", serial="XYZ789")
        assert str(info) == "HiDock P1 (v2.0.1, S/N: XYZ789)"

    def test_invalid_default(self):
        info = HiDockDeviceInfo.invalid()
        assert info.model == "?"
        assert info.version == "?"
        assert info.serial == "?"

    def test_invalid_with_model(self):
        info = HiDockDeviceInfo.invalid(model="HiDock H1E")
        assert info.model == "HiDock H1E"
        assert info.version == "?"
        assert info.serial == "?"
