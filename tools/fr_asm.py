"""Tiny two-pass assembler for the handful of Fujitsu FR instructions the patches use.

Source is a list of tuples; a bare string is a label. No delay-slot forms are used.
    ("ldi32", 0x80101dd8, 12)      ldi:32 #imm,r12
    ("ld_r14", 8, 4)               ld @(r14,8),r4
    ("beq", "label")
    ("bytes", b"...")              raw data
Every encoding was checked against binutils objdump (fr30, big-endian).
"""
import struct

BR = {"bra": 0x0, "beq": 0x2, "bne": 0x3, "bc": 0x4, "bnc": 0x5, "blt": 0xa, "bge": 0xb, "ble": 0xc,
      "bgt": 0xd, "bls": 0xe, "bhi": 0xf}
RP = 16   # pseudo register number for rp in push/pop


def _h(v):
    return struct.pack(">H", v & 0xffff)


def _enc(ins, pc, labels):
    op, *a = ins
    if op == "bytes":
        return bytes(a[0])
    if op in BR:
        t = labels[a[0]] if isinstance(a[0], str) else a[0]
        d = t - (pc + 2)
        assert d % 2 == 0 and -256 <= d < 256, (ins, hex(pc), d)
        return bytes([0xe0 | BR[op], (d // 2) & 0xff])
    if op == "ldi32":
        imm = labels[a[0]] if isinstance(a[0], str) else a[0]
        return bytes([0x9f, 0x80 | a[1]]) + struct.pack(">I", imm & 0xffffffff)
    if op == "ldi20":
        assert 0 <= a[0] < 1 << 20
        return bytes([0x9b, (a[0] >> 16) << 4 | a[1]]) + _h(a[0])
    if op == "ldi8":
        return bytes([0xc0 | (a[0] & 0xff) >> 4, (a[0] & 0xf) << 4 | a[1]])
    two = {  # op rj, ri  -> byte0, (rj<<4)|ri
        "mov": 0x8b, "add": 0xa6, "sub": 0xac, "cmp": 0xaa,
        "ld": 0x04, "lduh": 0x05, "ldub": 0x06,          # ld @rj,ri
        "ld_r13": 0x00, "lduh_r13": 0x01, "ldub_r13": 0x02,   # ld @(r13,rj),ri
    }
    if op in two:
        return bytes([two[op], a[0] << 4 | a[1]])
    if op in ("st", "sth", "stb"):                       # st ri,@rj  (args: ri, rj)
        return bytes([{"st": 0x14, "sth": 0x15, "stb": 0x16}[op], a[1] << 4 | a[0]])
    if op == "cmpi":                                     # cmp #u4,ri
        assert 0 <= a[0] < 16
        return bytes([0xa8, a[0] << 4 | a[1]])
    if op == "addi":                                     # add #u4,ri / add2 #-16..-1,ri
        if 0 <= a[0] < 16:
            return bytes([0xa4, a[0] << 4 | a[1]])
        assert -16 <= a[0] < 0
        return bytes([0xa5, (a[0] + 16) << 4 | a[1]])
    if op in ("lsl", "lsr"):                             # lsl/lsr #u4,ri
        assert 0 <= a[0] < 16
        return bytes([{"lsl": 0xb4, "lsr": 0xb0}[op], a[0] << 4 | a[1]])
    if op == "extsb":
        return bytes([0x97, 0x80 | a[0]])
    r14 = {"ld_r14": (0x2, 4), "st_r14": (0x3, 4), "lduh_r14": (0x4, 2),
           "sth_r14": (0x5, 2), "ldub_r14": (0x6, 1), "stb_r14": (0x7, 1)}
    if op in r14:                                        # (disp, reg)
        hi, scale = r14[op]
        assert a[0] % scale == 0 and -128 <= a[0] // scale < 128
        return _h(hi << 12 | ((a[0] // scale) & 0xff) << 4 | a[1])
    if op == "push":
        return b"\x17\x81" if a[0] == RP else bytes([0x17, a[0]])
    if op == "pop":
        return b"\x07\x81" if a[0] == RP else bytes([0x07, a[0]])
    if op == "enter":
        return bytes([0x0f, a[0] // 4])
    fixed = {"leave": b"\x9f\x90", "ret": b"\x97\x20", "nop": b"\x9f\xa0"}
    if op in fixed:
        return fixed[op]
    if op == "call_r":
        return bytes([0x97, 0x10 | a[0]])
    if op == "jmp_r":
        return bytes([0x97, 0x00 | a[0]])
    if op == "raw":
        return bytes.fromhex(a[0])
    raise ValueError(ins)


def assemble(src, base, extern=None):
    """Return (code bytes, labels). Labels are absolute addresses."""
    labels = dict(extern or {})
    for _ in range(2):
        pc, out = base, b""
        for ins in src:
            if isinstance(ins, str):
                labels[ins] = pc
                continue
            if ins[0] == "align":
                while pc % ins[1]:
                    out += b"\0"; pc += 1
                continue
            try:
                b = _enc(ins, pc, labels)
            except KeyError:           # forward label on first pass
                b = b"\0" * _size(ins)
            out += b; pc += len(b)
    return out, labels


def _size(ins):
    op = ins[0]
    if op == "bytes": return len(ins[1])
    if op == "ldi32": return 6
    if op == "ldi20": return 4
    return 2
