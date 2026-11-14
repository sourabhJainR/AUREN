from __future__ import annotations

import struct
from pathlib import Path


def make_icon() -> bytes:
    size = 64
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            # Deep navy tile with a simple blue A mark.
            color = (18, 16, 11, 255)  # BGRA background
            if 8 <= y <= 55:
                left = 13 + (y - 8) * 0.45
                right = 51 - (y - 8) * 0.45
                diagonal = abs(x - left) <= 3 or abs(x - right) <= 3
                crossbar = 34 <= y <= 39 and 19 <= x <= 45
                if diagonal or crossbar:
                    color = (247, 189, 96, 255)
            pixels.extend(color)

    xor_bitmap = bytes(pixels)
    mask_stride = ((size + 31) // 32) * 4
    and_bitmap = bytes(mask_stride * size)
    image = xor_bitmap + and_bitmap

    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0, len(image), 0, 0, 0, 0,
    )
    directory = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size, size, 0, 0, 1, 32, len(header) + len(image), 22,
    )
    return directory + entry + header + image


if __name__ == "__main__":
    icon_path = Path(__file__).resolve().parents[1] / "src-tauri" / "icons" / "icon.ico"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(make_icon())
    print(f"Generated {icon_path}")
