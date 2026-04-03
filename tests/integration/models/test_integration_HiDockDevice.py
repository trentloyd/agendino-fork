import pytest

from models.HiDockDevice import HiDockDeviceCommunicationError
from services.HiDockDeviceService import HiDockDeviceService


def run_or_skip(action, callback):
    try:
        return callback()
    except HiDockDeviceCommunicationError as exc:
        pytest.skip(f"HiDock device is busy or unavailable while {action}: {exc}")


class TestIntegrationHiDockDevice:
    @pytest.fixture
    def hidock_device_service(self):
        return HiDockDeviceService()

    @pytest.fixture
    def device(self, hidock_device_service):
        device = hidock_device_service.get_device_from_pid(45070)
        if not device:
            pytest.skip("No HiDock device found for PID 45070")
        run_or_skip("opening the device", device.open)
        try:
            yield device
        finally:
            device.close()

    def test_it_returns_device_info(self, device):
        info = run_or_skip("getting device info", device.get_device_info)
        print(info)

    def test_get_file_count(self, hidock_device_service, device):
        count = run_or_skip("getting the file count", device.get_file_count)
        print(count)

    def test_it_can_list_files(self, hidock_device_service, device):
        files = run_or_skip("listing files", device.list_files)
        for file in files:
            print(
                f"File: {file.name}, recording_type: {file.recording_type}, length: {file.length}, duration: {file.duration}"
            )

    def test_it_can_download_a_file(self, hidock_device_service, device, tmp_path):
        files = run_or_skip("listing files before download", device.list_files)
        if not files:
            pytest.skip("No files available on the HiDock device")

        print(f"Downloading {files[0].name} ({files[0].length} bytes) …")
        data = run_or_skip(
            f"downloading file {files[0].name}",
            lambda: device.download_file(files[0].name, files[0].length),
        )

        with open(tmp_path / "test_download.hda", "wb") as fp:
            fp.write(data)
