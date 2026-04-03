import re
import struct
import time
from typing import Callable

import usb

from models.HiDockDeviceInfo import HiDockDeviceInfo
from models.HiDockDevicePacket import HiDockDevicePacket
from models.HiDockRecording import HiDockRecording


class HiDockDeviceCommunicationError(RuntimeError):
    def __init__(self, action: str, error: Exception):
        super().__init__(f"Unable to communicate with HiDock device while {action}: {error}")
        self.action = action
        self.error = error


class HiDockDevice:
    VENDOR_ID = 0x10D6
    PRODUCT_IDS = {
        0xB00C: "HiDock H1",
        0xB00D: "HiDock H1E",
        0xB00E: "HiDock P1",
    }

    USB_INTERFACE = 0
    ENDPOINT_OUT = 0x01
    ENDPOINT_IN = 0x82

    CMD_GET_DEVICE_INFO = 0x01
    CMD_GET_DEVICE_TIME = 0x02
    CMD_GET_FILE_LIST = 0x04
    CMD_TRANSFER_FILE = 0x05
    CMD_GET_FILE_COUNT = 0x06
    CMD_DELETE_FILE = 0x07
    CMD_GET_CARD_INFO = 0x10

    def __init__(self, dev: usb.core.Device, debug: bool = False):
        self.dev = dev
        self.pid = dev.idProduct
        self.debug = debug
        self._seq = 0
        self._rxbuf = bytearray()
        self.model = HiDockDevice._model_name(dev.idProduct)
        self.serial = ""
        self.version = ""

    @staticmethod
    def _model_name(product_id: int) -> str:
        return HiDockDevice.PRODUCT_IDS.get(product_id, f"Unknown (0x{product_id:04X})")

    @staticmethod
    def _bcd_to_str(b: int) -> str:
        return f"{(b >> 4) & 0xF}{b & 0xF}"

    @staticmethod
    def parse_bcd_datetime(data: bytes) -> str:
        s = "".join(HiDockDevice._bcd_to_str(b) for b in data[:7])
        return f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"

    def open(self):
        try:
            if self.dev.is_kernel_driver_active(HiDockDevice.USB_INTERFACE):
                self.dev.detach_kernel_driver(HiDockDevice.USB_INTERFACE)
            self.dev.set_configuration()
            usb.util.claim_interface(self.dev, HiDockDevice.USB_INTERFACE)
        except usb.core.USBError as e:
            raise HiDockDeviceCommunicationError("opening the USB interface", e) from e

    def close(self):
        try:
            usb.util.release_interface(self.dev, HiDockDevice.USB_INTERFACE)
        except usb.core.USBError:
            pass
        finally:
            usb.util.dispose_resources(self.dev)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _send(self, pkt: HiDockDevicePacket):
        data = pkt.encode()
        print(f"SEND cmd=0x{pkt.cmd:04X} seq={pkt.seq}", data)
        try:
            self.dev.write(self.ENDPOINT_OUT, data, timeout=5000)
        except usb.core.USBError as e:
            raise HiDockDeviceCommunicationError(f"sending command 0x{pkt.cmd:04X}", e) from e

    def _recv(self, timeout_ms: int = 3000) -> HiDockDevicePacket | None:
        deadline = time.monotonic() + timeout_ms / 1000.0

        while time.monotonic() < deadline:
            sync_idx = bytes(self._rxbuf).find(HiDockDevicePacket.SYNC)
            if sync_idx >= 0:
                if sync_idx > 0:
                    if self.debug:
                        print(f"    \033[0;35m[DEBUG] Skipped {sync_idx} bytes before sync\033[0m")
                    self._rxbuf = self._rxbuf[sync_idx:]
                    sync_idx = 0
                if len(self._rxbuf) >= HiDockDevicePacket.HEADER_SIZE:
                    body_len = struct.unpack(">I", self._rxbuf[8:12])[0]
                    total = HiDockDevicePacket.HEADER_SIZE + body_len
                    if len(self._rxbuf) >= total:
                        pkt_bytes = bytes(self._rxbuf[:total])
                        self._rxbuf = self._rxbuf[total:]
                        return HiDockDevicePacket.decode(pkt_bytes)

            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                chunk = self.dev.read(self.ENDPOINT_IN, HiDockDevicePacket.MAX_BUF, timeout=min(remaining, 1000))
                self._rxbuf.extend(chunk)
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                raise HiDockDeviceCommunicationError("reading from the device", e) from e

        return None

    def _command(self, cmd: int, body: bytes = b"", timeout_ms: int = 5000) -> HiDockDevicePacket | None:
        seq = self._next_seq()
        self._send(HiDockDevicePacket(cmd=cmd, seq=seq, body=body))
        return self._recv(timeout_ms)

    def get_device_info(self) -> HiDockDeviceInfo:
        resp = self._command(self.CMD_GET_DEVICE_INFO)
        if not resp:
            return HiDockDeviceInfo.invalid(self.model)
        print("DEVICE_INFO body", resp.body)
        if len(resp.body) < 4:
            return HiDockDeviceInfo.invalid(self.model)
        ver_num = struct.unpack(">I", resp.body[0:4])[0]
        major = (ver_num >> 16) & 0xFF
        minor = (ver_num >> 8) & 0xFF
        patch = ver_num & 0xFF
        self.version = f"{major}.{minor}.{patch}"
        if len(resp.body) >= 20:
            self.serial = resp.body[4:20].decode("ascii", errors="replace").rstrip("\x00")
        else:
            self.serial = resp.body[4:].decode("ascii", errors="replace").rstrip("\x00")
        return HiDockDeviceInfo(model=self.model, version=self.version, serial=self.serial)

    def get_device_time(self) -> str:
        resp = self._command(self.CMD_GET_DEVICE_TIME)
        if not resp or len(resp.body) < 7:
            return "?"
        print("DEVICE_TIME body", resp.body)
        return self.parse_bcd_datetime(resp.body)

    def get_file_count(self) -> int:
        resp = self._command(self.CMD_GET_FILE_COUNT)
        if not resp or len(resp.body) < 4:
            return 0
        print("FILE_COUNT body", resp.body)
        return struct.unpack(">I", resp.body[0:4])[0]

    def list_files(self) -> list[HiDockRecording]:
        expected = self.get_file_count()
        if self.debug:
            print(f"  \033[0;35m[DEBUG] Expected file count: {expected}\033[0m")
        if expected == 0:
            return []

        seq = self._next_seq()
        self._send(HiDockDevicePacket(cmd=self.CMD_GET_FILE_LIST, seq=seq, body=b""))

        chunks: list[bytes] = []
        consecutive_timeouts = 0
        max_timeouts = 5

        while consecutive_timeouts < max_timeouts:
            resp = self._recv(timeout_ms=2000)
            if resp is None:
                consecutive_timeouts += 1
                continue
            consecutive_timeouts = 0

            if resp.cmd != self.CMD_GET_FILE_LIST:
                if self.debug:
                    print(f"  \033[0;35m[DEBUG] Ignoring response cmd=0x{resp.cmd:04X}\033[0m")
                continue

            if len(resp.body) == 0:
                if self.debug:
                    print("  \033[0;35m[DEBUG] Empty body = end of file list\033[0m")
                break

            if self.debug:
                print(f"FILE_LIST chunk #{len(chunks) + 1}", resp.body)
            chunks.append(bytes(resp.body))

            files = self._parse_file_list_chunks(chunks)
            if self.debug:
                print(f"  \033[0;35m[DEBUG] Parsed {len(files)}/{expected} files so far\033[0m")
            if expected > 0 and len(files) >= expected:
                break

        return self._parse_file_list_chunks(chunks)

    def _parse_file_list_chunks(self, chunks: list[bytes]) -> list[HiDockRecording]:
        if not chunks:
            return []

        data = bytearray()
        for c in chunks:
            data.extend(c)

        offset = 0
        if len(data) >= 6 and data[0] == 0xFF and data[1] == 0xFF:
            offset = 6

        files: list[HiDockRecording] = []
        while offset + 31 <= len(data):
            try:
                f = HiDockRecording()
                f.recording_type = data[offset]
                offset += 1
                offset += 2
                name_len = data[offset]
                offset += 1

                if offset + name_len + 26 > len(data):
                    break

                f.name = data[offset : offset + name_len].decode("ascii", errors="replace").rstrip("\x00")
                offset += name_len
                f.length = struct.unpack(">I", data[offset : offset + 4])[0]
                offset += 4
                offset += 6
                f.signature = data[offset : offset + 16].hex()
                offset += 16

                f.create_date, f.create_time = self._parse_filename_datetime(f.name)
                f.duration = self._calc_duration(f.length)

                if self.debug:
                    print(
                        f"  \033[0;35m[DEBUG] Entry: '{f.name}' type={f.recording_type} "
                        f"size={f.length} sig={f.signature[:8]}…\033[0m"
                    )

                files.append(f)
            except (ValueError, struct.error) as e:
                if self.debug:
                    print(f"  \033[0;35m[DEBUG] Parse error at offset {offset}: {e}\033[0m")
                break

        return files

    @staticmethod
    def _parse_filename_datetime(name: str) -> tuple[str, str]:
        m = re.match(r"(\d{4})(\w{3})(\d{2})-(\d{2})(\d{2})(\d{2})-", name)
        if m:
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
            year, mon, day, h, mi, s = m.groups()
            mon_num = months.get(mon, "??")
            return f"{year}/{mon_num}/{day}", f"{h}:{mi}:{s}"
        return "", ""

    @staticmethod
    def _calc_duration(length: int) -> float:
        return length / 8000

    def delete_file(self, filename: str, timeout_ms: int = 10000) -> dict:
        """Delete a file from the device.

        Returns: {"result": "success" | "not-exists" | "failed"}
        """
        body = filename.encode("ascii")
        resp = self._command(self.CMD_DELETE_FILE, body=body, timeout_ms=timeout_ms)
        if not resp or len(resp.body) < 1:
            return {"result": "failed"}
        code = resp.body[0]
        if code == 0x00:
            return {"result": "success"}
        elif code == 0x01:
            return {"result": "not-exists"}
        else:
            return {"result": "failed"}

    def get_card_info(self, timeout_ms: int = 5000) -> dict | None:
        resp = self._command(self.CMD_GET_CARD_INFO, timeout_ms=timeout_ms)
        if not resp or len(resp.body) < 12:
            return None
        free_mib = struct.unpack(">I", resp.body[0:4])[0]
        capacity_mib = struct.unpack(">I", resp.body[4:8])[0]
        status = struct.unpack(">I", resp.body[8:12])[0]
        mib = 1024 * 1024  # 1 MiB in bytes
        capacity = capacity_mib * mib
        used = capacity - (free_mib * mib)
        return {"used": used, "capacity": capacity, "status": f"{status:X}"}

    def download_file(
        self,
        filename: str,
        file_length: int,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> bytes:
        seq = self._next_seq()
        body = filename.encode("ascii")
        self._send(HiDockDevicePacket(cmd=self.CMD_TRANSFER_FILE, seq=seq, body=body))

        chunks: list[bytes] = []
        received = 0
        consecutive_timeouts = 0
        max_timeouts = 10

        while received < file_length and consecutive_timeouts < max_timeouts:
            resp = self._recv(timeout_ms=5000)
            if resp is None:
                consecutive_timeouts += 1
                if self.debug:
                    print(f"  \033[0;35m[DEBUG] Download timeout #{consecutive_timeouts}\033[0m")
                continue
            consecutive_timeouts = 0

            if self.debug and len(chunks) == 0:
                print(f"TRANSFER first response cmd=0x{resp.cmd:04X}", resp.body)

            if resp.cmd != self.CMD_TRANSFER_FILE:
                if self.debug:
                    print(f"  \033[0;35m[DEBUG] Download: ignoring cmd=0x{resp.cmd:04X}\033[0m")
                continue

            if len(resp.body) == 0:
                if self.debug:
                    print("  \033[0;35m[DEBUG] Download: empty body = end\033[0m")
                break

            if resp.body == b"fail":
                raise RuntimeError(f"Device reported download failure for '{filename}'")

            chunks.append(bytes(resp.body))
            received += len(resp.body)

            if on_progress:
                on_progress(received, file_length)

        if self.debug:
            print(f"  \033[0;35m[DEBUG] Download complete: {len(chunks)} chunks, {received} bytes\033[0m")

        return b"".join(chunks)
