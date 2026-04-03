from typing import Optional

import usb.core

from models.HiDockDevice import HiDockDevice


class HiDockDeviceService:
    @staticmethod
    def list_devices() -> list[usb.core.Device]:
        devs = []
        for pid in HiDockDevice.PRODUCT_IDS:
            found = usb.core.find(find_all=True, idVendor=HiDockDevice.VENDOR_ID, idProduct=pid)
            devs.extend(found)
        return devs

    @staticmethod
    def get_device_from_pid(pid: int) -> Optional[HiDockDevice]:
        devs = []
        found = usb.core.find(find_all=True, idVendor=HiDockDevice.VENDOR_ID, idProduct=pid)
        devs.extend(found)
        if devs:
            return HiDockDevice(devs[0])
        return None
