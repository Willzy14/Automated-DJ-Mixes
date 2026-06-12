"""Tiny dependency-free OSC client for AbletonOSC (ideoforms remote script).

AbletonOSC listens on UDP 11000 and replies on 11001 (localhost). This gives
the pipeline programmatic two-way control of Live — the "hands and ears" to
complement screenshot-driven "eyes" (ableton_ui.py):
  - read ground-truth state (tempo, track names, clip start_times/lengths,
    selected track) instead of parsing screenshots
  - navigate exactly (set current_song_time to a beat, jump to a cue)
  - solo/play transition pairs for ear-check preview bounces
  - write locators at every swap so the set opens pre-navigated for Sam

Security: AbletonOSC binds 0.0.0.0 by default (LAN-reachable). Installed
stock on Carillon AC-1 per Sam 2026-06-12; localhost-bind patch is queued
for the studio machine. This client only ever talks to 127.0.0.1.

Rendering/export is NOT in Live's API — that stays pixel-driven (ableton_ui).
"""
from __future__ import annotations

import socket
import struct
import time


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (4 - len(b) % 4)


def _encode(address: str, args: tuple) -> bytes:
    dgram = _pad(address.encode("utf-8"))
    tags = ","
    for a in args:
        tags += "i" if isinstance(a, bool) or isinstance(a, int) else \
                "f" if isinstance(a, float) else "s"
    dgram += _pad(tags.encode("utf-8"))
    for a in args:
        if isinstance(a, bool):
            dgram += struct.pack(">i", int(a))
        elif isinstance(a, int):
            dgram += struct.pack(">i", a)
        elif isinstance(a, float):
            dgram += struct.pack(">f", a)
        else:
            dgram += _pad(str(a).encode("utf-8"))
    return dgram


def _decode(dgram: bytes) -> tuple[str, list]:
    i = dgram.index(b"\x00")
    address = dgram[:i].decode("utf-8", "replace")
    j = (i + 4) & ~3
    if j >= len(dgram) or dgram[j:j + 1] != b",":
        return address, []
    k = dgram.index(b"\x00", j)
    tags = dgram[j + 1:k].decode("ascii", "replace")
    p = (k + 4) & ~3
    out: list = []
    for t in tags:
        if t == "i":
            out.append(struct.unpack(">i", dgram[p:p + 4])[0]); p += 4
        elif t == "f":
            out.append(struct.unpack(">f", dgram[p:p + 4])[0]); p += 4
        elif t == "s":
            e = dgram.index(b"\x00", p)
            out.append(dgram[p:e].decode("utf-8", "replace"))
            p = (e + 4) & ~3
        elif t in ("T", "F", "N"):
            out.append({"T": True, "F": False, "N": None}[t])
    return address, out


class AbletonOSC:
    def __init__(self, host: str = "127.0.0.1",
                 send_port: int = 11000, recv_port: int = 11001):
        self.addr = (host, send_port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", recv_port))
        self.sock.settimeout(2.0)

    def send(self, address: str, *args) -> None:
        self.sock.sendto(_encode(address, args), self.addr)

    def query(self, address: str, *args, timeout: float = 2.0) -> list:
        """Send and wait for the reply on the same address. Returns the
        decoded params (raises TimeoutError if no reply)."""
        self.sock.settimeout(timeout)
        self.send(address, *args)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data, _ = self.sock.recvfrom(65536)
            addr, params = _decode(data)
            if addr == address:
                return params
        raise TimeoutError(f"no reply to {address}")

    def close(self) -> None:
        self.sock.close()


def _selftest() -> int:
    """Read-only smoke test against a running Live + AbletonOSC."""
    c = AbletonOSC()
    try:
        print("test:", c.query("/live/test"))
        print("version:", c.query("/live/application/get/version"))
        print("tempo:", c.query("/live/song/get/tempo"))
        n = c.query("/live/song/get/num_tracks")[0]
        print("num_tracks:", n)
        names = c.query("/live/song/get/track_names")
        for i, nm in enumerate(names):
            print(f"  track {i}: {nm}")
        # arrangement clip start_times for the first mix track (index 1)
        st = c.query("/live/track/get/arrangement_clips/start_time", 1)
        print("track 1 arrangement clip start_times (beats):", st[1:])
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
