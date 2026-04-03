import uuid

import pytest

from models.HiDockDevice import HiDockDevice
from models.HiDockDevicePacket import HiDockDevicePacket


class TestHiDockDevicePacket:
    def test_it_can_encode_and_decode(self):
        body = uuid.uuid4().bytes
        encoded = HiDockDevicePacket(cmd=HiDockDevice.CMD_GET_DEVICE_INFO, seq=1, body=body).encode()

        assert encoded == b"".join([b"\x124\x00\x01\x00\x00\x00\x01\x00\x00\x00\x10", body])

        decoded = HiDockDevicePacket.decode(encoded)
        assert decoded.cmd == HiDockDevice.CMD_GET_DEVICE_INFO
        assert decoded.seq == 1
        assert decoded.body == body

    def test_encode_empty_body(self):
        pkt = HiDockDevicePacket(cmd=0x06, seq=42, body=b"")
        encoded = pkt.encode()
        # SYNC(2) + cmd(2) + seq(4) + body_len(4) = 12 bytes, body_len = 0
        assert len(encoded) == 12
        decoded = HiDockDevicePacket.decode(encoded)
        assert decoded.cmd == 0x06
        assert decoded.seq == 42
        assert decoded.body == b""

    def test_decode_too_short_raises(self):
        with pytest.raises(ValueError, match="Packet too short"):
            HiDockDevicePacket.decode(b"\x00\x01\x02")

    def test_decode_no_sync_raises(self):
        with pytest.raises(ValueError, match="No sync bytes found"):
            HiDockDevicePacket.decode(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")

    def test_decode_sync_too_late_short_after(self):
        # Sync bytes at offset 10, not enough data after
        data = b"\x00" * 10 + b"\x124" + b"\x00" * 2
        with pytest.raises(ValueError, match="Packet too short after sync"):
            HiDockDevicePacket.decode(data)

    def test_decode_with_leading_garbage(self):
        """Packet with garbage bytes before sync should still decode."""
        body = b"\xaa\xbb"
        pkt = HiDockDevicePacket(cmd=0x04, seq=7, body=body)
        raw = b"\xff\xff\xff" + pkt.encode()
        decoded = HiDockDevicePacket.decode(raw)
        assert decoded.cmd == 0x04
        assert decoded.seq == 7
        assert decoded.body == body

    def test_roundtrip_large_body(self):
        body = bytes(range(256)) * 4  # 1024 bytes
        pkt = HiDockDevicePacket(cmd=0x05, seq=100, body=body)
        decoded = HiDockDevicePacket.decode(pkt.encode())
        assert decoded.body == body
        assert decoded.cmd == 0x05
        assert decoded.seq == 100

    def test_constants(self):
        assert HiDockDevicePacket.SYNC == bytes([0x12, 0x34])
        assert HiDockDevicePacket.HEADER_SIZE == 12
        assert HiDockDevicePacket.MAX_BUF == 51200
