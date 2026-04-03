class HiDockDeviceInfo:
    def __init__(self, model: str, version: str, serial: str):
        self.model = model
        self.version = version
        self.serial = serial

    def __str__(self):
        return f"{self.model} (v{self.version}, S/N: {self.serial})"

    @staticmethod
    def invalid(model="?") -> "HiDockDeviceInfo":
        return HiDockDeviceInfo(model, "?", "?")
