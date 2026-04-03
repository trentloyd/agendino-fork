import pytest
import usb.core
import usb.util
from typing import cast

from models.HiDockDevice import HiDockDevice
from models.HiDockDevice import HiDockDeviceCommunicationError
from models.HiDockDevicePacket import HiDockDevicePacket


class DummyUSBDevice:
    def __init__(self):
        self.idProduct = 0xB00C
        self.write_error = None
        self.read_error = None

    def is_kernel_driver_active(self, interface):
        return False

    def detach_kernel_driver(self, interface):
        return None

    def set_configuration(self):
        return None

    def write(self, endpoint, data, timeout=None):
        if self.write_error is not None:
            raise self.write_error
        return len(data)

    def read(self, endpoint, size, timeout=None):
        if self.read_error is not None:
            raise self.read_error
        return b""


class TestHiDockDeviceStatic:
    """Unit tests for HiDockDevice static/class methods that don't require USB hardware."""

    def test_model_name_h1(self):
        assert HiDockDevice._model_name(0xB00C) == "HiDock H1"

    def test_model_name_h1e(self):
        assert HiDockDevice._model_name(0xB00D) == "HiDock H1E"

    def test_model_name_p1(self):
        assert HiDockDevice._model_name(0xB00E) == "HiDock P1"

    def test_model_name_unknown(self):
        result = HiDockDevice._model_name(0x9999)
        assert "Unknown" in result
        assert "9999" in result

    def test_bcd_to_str(self):
        assert HiDockDevice._bcd_to_str(0x20) == "20"
        assert HiDockDevice._bcd_to_str(0x09) == "09"
        assert HiDockDevice._bcd_to_str(0x31) == "31"
        assert HiDockDevice._bcd_to_str(0x00) == "00"
        assert HiDockDevice._bcd_to_str(0x99) == "99"

    def test_parse_bcd_datetime(self):
        # 2026-03-27 09:49:38 in BCD: 0x20 0x26 0x03 0x27 0x09 0x49 0x38
        data = bytes([0x20, 0x26, 0x03, 0x27, 0x09, 0x49, 0x38])
        result = HiDockDevice.parse_bcd_datetime(data)
        assert result == "2026-03-27 09:49:38"

    def test_parse_bcd_datetime_midnight(self):
        data = bytes([0x20, 0x26, 0x01, 0x01, 0x00, 0x00, 0x00])
        result = HiDockDevice.parse_bcd_datetime(data)
        assert result == "2026-01-01 00:00:00"

    def test_parse_filename_datetime_valid(self):
        date, time = HiDockDevice._parse_filename_datetime("2026Mar27-094938-Wip01.hda")
        assert date == "2026/03/27"
        assert time == "09:49:38"

    def test_parse_filename_datetime_all_months(self):
        months = {
            "Jan": "01",
            "Feb": "02",
            "Mar": "03",
            "Apr": "04",
            "May": "05",
            "Jun": "06",
            "Jul": "07",
            "Aug": "08",
            "Sep": "09",
            "Oct": "10",
            "Nov": "11",
            "Dec": "12",
        }
        for abbr, num in months.items():
            date, time = HiDockDevice._parse_filename_datetime(f"2026{abbr}15-120000-Rec01.hda")
            assert date == f"2026/{num}/15"
            assert time == "12:00:00"

    def test_parse_filename_datetime_invalid(self):
        date, time = HiDockDevice._parse_filename_datetime("invalid_name")
        assert date == ""
        assert time == ""

    def test_parse_filename_datetime_unknown_month(self):
        date, time = HiDockDevice._parse_filename_datetime("2026Xyz27-094938-Wip01.hda")
        assert date == "2026/??/27"
        assert time == "09:49:38"

    def test_calc_duration(self):
        assert HiDockDevice._calc_duration(8000) == 1.0
        assert HiDockDevice._calc_duration(80000) == 10.0
        assert HiDockDevice._calc_duration(0) == 0.0

    def test_product_ids(self):
        assert 0xB00C in HiDockDevice.PRODUCT_IDS
        assert 0xB00D in HiDockDevice.PRODUCT_IDS
        assert 0xB00E in HiDockDevice.PRODUCT_IDS
        assert len(HiDockDevice.PRODUCT_IDS) == 3

    def test_vendor_id(self):
        assert HiDockDevice.VENDOR_ID == 0x10D6

    def test_command_constants(self):
        assert HiDockDevice.CMD_GET_DEVICE_INFO == 0x01
        assert HiDockDevice.CMD_GET_DEVICE_TIME == 0x02
        assert HiDockDevice.CMD_GET_FILE_LIST == 0x04
        assert HiDockDevice.CMD_TRANSFER_FILE == 0x05
        assert HiDockDevice.CMD_GET_FILE_COUNT == 0x06
        assert HiDockDevice.CMD_DELETE_FILE == 0x07
        assert HiDockDevice.CMD_GET_CARD_INFO == 0x10


class TestHiDockDeviceCommunication:
    def test_open_wraps_usb_errors(self, monkeypatch):
        dev = DummyUSBDevice()
        device = HiDockDevice(cast(usb.core.Device, cast(object, dev)))

        def raise_busy(*args, **kwargs):
            raise usb.core.USBError("Resource busy")

        monkeypatch.setattr(usb.util, "claim_interface", raise_busy)

        with pytest.raises(HiDockDeviceCommunicationError, match="opening the USB interface"):
            device.open()

    def test_send_wraps_usb_errors(self):
        dev = DummyUSBDevice()
        dev.write_error = usb.core.USBError("Resource busy")
        device = HiDockDevice(cast(usb.core.Device, cast(object, dev)))

        with pytest.raises(HiDockDeviceCommunicationError, match="sending command 0x0006"):
            device._send(HiDockDevicePacket(cmd=HiDockDevice.CMD_GET_FILE_COUNT, seq=1, body=b""))

    def test_recv_wraps_usb_errors(self):
        dev = DummyUSBDevice()
        dev.read_error = usb.core.USBError("Input/output error")
        device = HiDockDevice(cast(usb.core.Device, cast(object, dev)))

        with pytest.raises(HiDockDeviceCommunicationError, match="reading from the device"):
            device._recv(timeout_ms=1)

    def test_close_ignores_release_errors(self, monkeypatch):
        dev = DummyUSBDevice()
        device = HiDockDevice(cast(usb.core.Device, cast(object, dev)))
        disposed = []

        def raise_busy(*args, **kwargs):
            raise usb.core.USBError("Resource busy")

        monkeypatch.setattr(usb.util, "release_interface", raise_busy)
        monkeypatch.setattr(usb.util, "dispose_resources", lambda usb_device: disposed.append(usb_device))

        device.close()

        assert disposed == [dev]
