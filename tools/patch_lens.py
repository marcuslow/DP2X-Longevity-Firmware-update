#!/usr/bin/env python3
"""Patch Sigma DP2x firmware 1.02 so the lens barrel is not retracted at power-off
and is not re-homed (retract + extend) at power-on when it is already out.

Usage: patch_lens.py IN.bin OUT.bin [--also-playback] [--af-refine N] [--shutter-count]
See NOTES.md for the analysis behind each patch.
"""
import argparse, struct, sys

HDR = 0x4080          # file offset of the 8 MiB flash image
BASE = 0x40000        # load address of that image
def off(addr): return addr - BASE + HDR

# (name, address, original bytes, patched bytes, description)
PATCHES = [
    ("startup-skip-rehome", 0x38a440, "d87f", "e007",
     "lens_barrel_ctrl(0): barrel not at home -> skip park+retract+extend, "
     "jump to 'extend OK' tail (motor brake cmd, return 0)"),
    ("poweroff-direct",     0x2387c0, "d117", "9fa0",
     "System_Factor_Lens_Closer: NOP the call to lens_close() on power-off"),
    ("poweroff-lens-task",  0x23880a, "d0f2", "9fa0",
     "lens task msg #2 (sent only by power-off): NOP the call to lens_close()"),
]
OPTIONAL = [
    ("playback-no-retract", 0x38a47c, "d76b", "9fa0",
     "lens_barrel_ctrl(2): NOP barrel retract for ALL closes "
     "(also playback-idle auto-retract and pre-firmware-update close)"),
]

def _cstr(old, new):
    """Replace a NUL-terminated string in place, padding with NULs to the original length."""
    o, n = old.encode() + b"\0", new.encode()
    assert len(n) < len(o)
    return o.hex(), (n + b"\0" * (len(o) - len(n))).hex()

# Setup-menu version line shows the shutter counter (RAM 0x80101ff4, stored in flash
# settings sector 0x800308) instead of the serial number. See NOTES.md 9.1.
SHUTTER_COUNT = [
    ("shutter-count-src", 0x2c7224, "00808000", "80101ff4",
     "version screen: memcpy source serial (flash 0x808000) -> shutter counter (RAM 0x80101ff4)"),
    ("shutter-count-fmt", 0x45c7fc, *_cstr("Ver.%01d.%02d [SN:%08d]", "Ver.%01d.%02d Shots:%d"),
     "version format string: [SN:%08d] -> Shots:%d"),
    ("shutter-count-fmt4", 0x45c7d8, *_cstr("Ver.%01d.%02d.%01d.%03d [SN:%08d]",
                                            "Ver.%01d.%02d.%01d.%03d Shots:%d"),
     "long version format string: [SN:%08d] -> Shots:%d"),
]

def af_refine_patches(n):
    """AF refine pass restarts n focus steps past the coarse peak (stock 16), see NOTES.md 9.6."""
    if not 1 <= n <= 16:
        sys.exit("--af-refine must be 1..16")
    return [
        ("af-refine-minus", 0x283e66, "a500", f"a5{(16 - n) & 0xf:x}0",
         f"refine setup: target = peak - {n} (add2 -{n},r0)"),
        ("af-refine-plus",  0x283eb0, "c101", f"c{n >> 4:x}{n & 0xf:x}1",
         f"refine setup: target = peak + {n} (ldi:8 {n:#x},r1)"),
    ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--also-playback", action="store_true",
                    help="also stop the playback-mode idle retract (removes the built-in re-home recovery)")
    ap.add_argument("--af-refine", type=int, metavar="N",
                    help="EXPERIMENTAL: start the AF refine re-scan N steps past the peak instead of 16")
    ap.add_argument("--shutter-count", action="store_true",
                    help="show the shutter count on the setup-menu version line (replaces the serial number)")
    a = ap.parse_args()
    d = bytearray(open(a.inp, "rb").read())
    if d[:16] != b"SIGMA.CO0000DP2X" or d[0x10:0x1b] != b"1.02.0.0001" or len(d) != 0x804080:
        sys.exit("not a DP2x 1.02 firmware image")
    stored = struct.unpack(">I", d[0x4c:0x50])[0]
    if (sum(d[0x80:]) & 0xffffffff) != stored:
        sys.exit("input checksum mismatch - file already modified/corrupt?")
    todo = PATCHES + (OPTIONAL if a.also_playback else [])
    if a.af_refine is not None and a.af_refine != 16:
        todo += af_refine_patches(a.af_refine)
    if a.shutter_count:
        todo += SHUTTER_COUNT
    for name, addr, old, new, desc in todo:
        o = off(addr); old, new = bytes.fromhex(old), bytes.fromhex(new)
        if d[o:o+len(old)] != old:
            sys.exit(f"{name}: unexpected bytes at {addr:#x} (file {o:#x}): {d[o:o+len(old)].hex()}")
        d[o:o+len(new)] = new
        print(f"[+] {name:22s} @{addr:#x} (file {o:#08x}) {old.hex()[:16]} -> {new.hex()[:16]}  {desc}")
    # Main checksum: 32-bit sum of all bytes after the 0x80 header, big-endian at 0x4c.
    # (The camera's updater checks the same sum over the 8 MiB image at 0x4080.)
    cs = sum(d[0x80:]) & 0xffffffff
    d[0x4c:0x50] = struct.pack(">I", cs)
    print(f"[+] checksum {stored:#010x} -> {cs:#010x}")
    open(a.out, "wb").write(d)

if __name__ == "__main__":
    main()
