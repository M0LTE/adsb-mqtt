# Vendored from https://github.com/acarsGuy/binCraft-decoder (MIT-style, no LICENSE in upstream).
# Refactored so the parsing loop can be driven from an in-memory bytes buffer
# (decode_bytes) as well as a file path (binCraftReader), and to use the
# `zstandard` package instead of the unmaintained `zstd` package.
import io
import math
import struct

import zstandard as _zstd

_ZSTD = _zstd.ZstdDecompressor()


def _zstd_decompress(buf: bytes) -> bytes:
    # stream_reader works whether or not the frame embeds the decompressed
    # size; plain ZstdDecompressor.decompress() raises if size is unknown.
    with _ZSTD.stream_reader(io.BytesIO(buf)) as r:
        return r.read()


def _unpack(fmt, buff):
    return [v[0] for v in struct.iter_unpack(fmt, buff)]


def _hex(i):
    return hex(i).split("x")[-1]


def _str(data, start, end):
    s = ""
    i = start
    while i < end and data[i]:
        if 32 < data[i] < 127:
            s += chr(data[i])
        i += 1
    return s.strip()


_TYPES = [
    "adsb_icao", "adsb_icao_nt", "adsr_icao", "tisb_icao", "adsc",
    "mlat", "other", "mode_s", "adsb_other", "adsr_other",
    "tisb_trackfile", "tisb_other", "mode_ac",
]


def _type_name(t):
    if 0 <= t < len(_TYPES):
        return _TYPES[t]
    return "unknown"


def _parse(data):
    r = {}
    vals = _unpack("I", data)
    r["now"] = vals[0] / 1000 + vals[1] * 4294967.296
    r["stride"] = vals[2]
    r["global_ac_count_withpos"] = vals[3]
    r["globeIndex"] = vals[4]
    stride = vals[2]

    limits = _unpack("h", data[20:])
    r["south"] = limits[0]
    r["west"] = limits[1]
    r["north"] = limits[2]
    r["east"] = limits[3]

    aircraft = []
    for off in range(stride, len(data), stride):
        chunk = data[off : off + stride]
        if len(chunk) < stride:
            break
        s32 = _unpack("i", chunk)
        u16 = _unpack("H", chunk)
        s16 = _unpack("h", chunk)
        u8 = _unpack("B", chunk)

        ac = {
            "hex": _hex(s32[0] & ((1 << 24) - 1)).zfill(6),
            "seen_pos": u16[2] / 10,
            "seen": u16[3] / 10,
            "lat": s32[2] / 1e6,
            "lon": s32[3] / 1e6,
            "alt_baro": s16[8] * 25,
            "alt_geom": s16[9] * 25,
            "baro_rate": s16[10] * 8,
            "geom_rate": s16[11] * 8,
            "nav_altitude_mcp": u16[12] * 4,
            "nav_altitude_fms": u16[13] * 4,
            "nav_qnh": s16[14] / 10,
            "nav_heading": s16[15] / 90,
            "squawk": _hex(u16[16]).zfill(4),
            "gs": s16[17] / 10,
            "mach": s16[18] / 1000,
            "roll": s16[19] / 100,
            "track": s16[20] / 90,
            "track_rate": s16[21] / 100,
            "mag_heading": s16[22] / 90,
            "true_heading": s16[23] / 90,
            "wd": s16[24],
            "ws": s16[25],
            "oat": s16[26],
            "tat": s16[27],
            "tas": u16[28],
            "ias": u16[19],
            "rc": u16[30],
            "messages": u16[31],
            "category": _hex(u8[64]).upper() if u8[64] else None,
            "nic": u8[65],
            "emergency": u8[67] & 0x0F,
            "airground": u8[68] & 0x0F,
            "nav_altitude_src": (u8[68] & 0xF0) >> 4,
            "sil_type": u8[69] & 0x0F,
            "adsb_version": (u8[69] & 0xF0) >> 4,
            "adsr_version": u8[70] & 0x0F,
            "tisb_version": (u8[70] & 0xF0) >> 4,
            "nac_p": u8[71] & 0x0F,
            "nac_v": (u8[71] & 0xF0) >> 4,
            "sil": u8[72] & 0x03,
            "gva": (u8[72] & 0x0C) >> 2,
            "sda": (u8[72] & 0x30) >> 4,
            "nic_a": (u8[72] & 0x40) >> 6,
            "nic_c": (u8[72] & 0x80) >> 7,
            "rssi": 10 * math.log10(u8[86] * u8[86] / 65025 + 1.125e-5),
            "dbFlags": u8[87],
            "flight": _str(u8, 78, 87),
            "t": _str(u8, 88, 92),
            "r": _str(u8, 92, 104),
            "receiverCount": u8[104],
            "nic_baro": u8[73] & 0x01,
            "alert": (u8[73] & 0x02) >> 1,
            "spi": (u8[73] & 0x04) >> 2,
        }
        ac["type"] = _type_name((u8[67] & 0xF0) >> 4)
        if ac["airground"] == 1:
            ac["alt_baro"] = "ground"

        nav_modes_byte = u8[66]
        modes = []
        if nav_modes_byte & 1: modes.append("autopilot")
        if nav_modes_byte & 2: modes.append("vnav")
        if nav_modes_byte & 4: modes.append("alt_hold")
        if nav_modes_byte & 8: modes.append("approach")
        if nav_modes_byte & 16: modes.append("lnav")
        if nav_modes_byte & 32: modes.append("tcas")
        ac["nav_modes"] = modes

        aircraft.append(ac)

    r["aircraft"] = aircraft
    return r


def decode_bytes(buf: bytes, zstd_compressed: bool = True) -> dict:
    if zstd_compressed:
        buf = _zstd_decompress(buf)
    return _parse(buf)


def binCraftReader(file: str, zstd_compressed: bool = False) -> dict:
    with open(file, "rb") as f:
        data = f.read()
    return decode_bytes(data, zstd_compressed=zstd_compressed)
