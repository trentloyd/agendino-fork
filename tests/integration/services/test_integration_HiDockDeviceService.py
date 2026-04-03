import pytest

from services.HiDockDeviceService import HiDockDeviceService


class TestIntegrationHiDockDeviceService:
    @pytest.fixture
    def hidock_device_service(self):
        return HiDockDeviceService()

    @pytest.fixture
    def device(self, hidock_device_service):
        device = hidock_device_service.get_device_from_pid(45070)
        if not device:
            pytest.skip("No HiDock device found for PID 45070")
        return device

    def test_it_can_list_devices(self, hidock_device_service):
        result = hidock_device_service.list_devices()
        print(result)

    def test_it_can_get_device_from_pid(self, hidock_device_service):
        result = hidock_device_service.get_device_from_pid(45070)
        print(result)
