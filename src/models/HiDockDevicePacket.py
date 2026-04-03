import struct


class HiDockDevicePacket:
    SYNC = bytes([0x12, 0x34])
    HEADER_SIZE = 12
    MAX_BUF = 51200

    def __init__(self, cmd: int, seq: int, body: bytes):
        self.cmd = cmd
        self.seq = seq
        self.body = body

    def encode(self) -> bytes:
        return (
            self.SYNC
            + struct.pack(">H", self.cmd)
            + struct.pack(">I", self.seq)
            + struct.pack(">I", len(self.body))
            + self.body
        )

    @staticmethod
    def decode(raw: bytes) -> "HiDockDevicePacket":
        if len(raw) < HiDockDevicePacket.HEADER_SIZE:
            raise ValueError(f"Packet too short ({len(raw)} bytes)")

        idx = raw.find(HiDockDevicePacket.SYNC)
        if idx < 0:
            raise ValueError(f"No sync bytes found in {len(raw)} bytes")

        raw = raw[idx:]
        if len(raw) < HiDockDevicePacket.HEADER_SIZE:
            raise ValueError(f"Packet too short after sync ({len(raw)} bytes)")

        cmd = struct.unpack(">H", raw[2:4])[0]
        seq = struct.unpack(">I", raw[4:8])[0]
        body_len = struct.unpack(">I", raw[8:12])[0]
        body = raw[12 : 12 + body_len]
        return HiDockDevicePacket(cmd=cmd, seq=seq, body=body)
