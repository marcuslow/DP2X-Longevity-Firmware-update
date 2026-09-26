#!/usr/bin/env python3
"""Port the stock Sigma DP2x 1.02 AF engine into DP2 firmware 1.05. See NOTES-DP2.md sections 4-6.

Step 2, the relocator. It copies the DP2x AF code into the dead old-AF block of the DP2 image and rewrites every
address in it: ldi:32/ldi:20 constants, call and branch displacements, code-pointer tables. Anything it cannot
classify is an error. The output is re-disassembled with objdump and compared with the source.
    port_af.py --report      list every relocated reference and the layout
    port_af.py OUT.bin       write the patched DP2 image (nothing calls the new code until the hooks are in)
Needs work/ and work-dp2/ from tools/disasm.sh.
"""
import argparse, bisect, collections, hashlib, os, re, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from xmatch import FW
from fr_asm import assemble, RP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR, BASE = 0x4080, 0x40000
DP2X_FILE, DP2X_SHA = "dp2x102.bin", "5fd41b480e76a5d3332018cf83a61ef3d4ae7849e9e33955954708aa92e9933a"
DP2_FILE, DP2_SHA = "sigma DP2/dp2v105.bin", "5720810d99da03dbd2b9142172031c895ee6b6829283cade158314c3939267ad"
OBJDUMP = "/opt/homebrew/opt/binutils/bin/objdump"

# ---- what is copied (DP2x addresses) --------------------------------------------------------------------------
SRC = [(0x283714, 0x284522), (0x2d905e, 0x2d94bc), (0x38a594, 0x38a612), (0x38a7e8, 0x38a854),
       (0x38ad38, 0x38b270)]
# Entry points the DP2 side will call. Only functions reachable from these are copied.
ROOTS = [0x28425a, 0x38b038, 0x38b090, 0x38b188, 0x38b1d4, 0x38a594]
# Code-pointer tables inside the copied code's reach: address -> number of 32-bit pointers.
PTR_TABLES = {0x3c0c60: 7}           # switch table of 0x38ad5c

# ---- where it goes (DP2 addresses) ----------------------------------------------------------------------------
# The old AF engine block 0x27fe04-0x2818f9, around the 24-byte setter 0x280acc that stays live.
SPACE = [(0x27fe04, 0x280acc), (0x280ae4, 0x2818fa)]
# Freeing 0x280254 (merges the first two gaps): the NoContrast callback's pointer to it -> the DP2 'return 0'.
SPACE_PATCHES = [(0x27f6ee, 0x280254, 0x3800c8)]    # (ldi:32 site, old value, new value)
# The other DP2 references into SPACE (all checked, see NOTES-DP2.md section 7):
SPACE_OK = {
    0x27fb4a: "in 0x27fac8, whose only reference is 0x2805b4 inside the old engine",
    0x3048ea: "0x280000 is a clamp limit (cmp, then stored as a value)", 0x3048f6: "same",
}
SPACE_HOOKED = {                      # dead only once the step-3 hooks are in; no image is written before that
    0x37fe30: "AF start 0x37fdfc -> old engine start 0x2800da (replaced by the start adapter)",
    0x3817f4: "old task-6 body 0x3817a0 (task-table entry 6 is repointed)", 0x381810: "same",
}
# RAM for the engine's own variables: the old AF module's scan buffer (only reachable from the old engine start).
NEWRAM = (0x801574f4, 0x801574f4 + 0x3254)

# ---- DP2x -> DP2 maps -----------------------------------------------------------------------------------------
CODE = {                              # DP2x function -> DP2 twin
    0x30c21e: 0x30e724,               # AF contrast library
    0x307da6: 0x30c6fa,               # library per-window call
    0x30837c: 0x302e3c,               # focus-range / macro getter
    0x374dc8: 0x36a476,               # memset
    0x387520: 0x37ca00,
    0x38a084: 0x37eb90, 0x38968c: 0x37eb90,   # meter
    0x38ab64: "move_minus", 0x38abf4: "move_plus",   # async focus move -, + (wrappers, see GEN)
    0x38a1a0: "hold",                 # motor hold after a move (see GEN)
    # UI in the AF end 0x38b090: the DP2 half-press code draws the result itself. -> DP2 'return 0'
    0x23941c: 0x3800c8, 0x2130e4: 0x3800c8,   # frame-type flag, AF point x/y
    0x27007c: 0x3800c8, 0x2700a4: 0x3800c8,   # green / red AF frame
    0x238a8a: 0x3800c8, 0x238ab0: 0x3800c8,   # AF LED bits
    0x397fd8: 0x3800c8, 0x398018: 0x3800c8,   # beeps
}
RET0 = 0x3800c8
RAM = [                               # (DP2x lo, hi, DP2 lo, access width the DP2 variable needs)
    (0x6a019a78, 0x6a019a7c, 0x6a0195a4, 4),  # focus position
    (0x6a019a70, 0x6a019a74, 0x6a0195b9, 1),  # motor state: a word on the DP2x, a byte on the DP2
]
NEW = [                               # DP2x RAM moved into NEWRAM, (lo, hi)
    (0x80150f9c, 0x80151548),         # engine context
    (0x8015528c, 0x801552ec),         # library parameters
    (0x6a019a64, 0x6a019a90),         # lens/task variables (the shared ones in RAM take precedence)
    (0x6a01b78c, 0x6a01b790),
    (0x6a025980, 0x6a025b10),         # library work buffer: 0x190 B, up to the next DP2x variable
]
DATA_INIT = [(0x6a019a64, 0x6a019a90)]  # NEW ranges with nonzero .data values on the DP2x (the rest is .bss or 0)
FORBID = {0x6a019a74: "DP2x lens init flag (2 = homed; the DP2 one is 0x6a0195a0, 1 = homed)"}
KEEP = {                              # constants >= 0x40000 that are not DP2x addresses (checked by hand)
    0x40000000, 0x40000006,           # AF block I/O (same chip; the DP2 frame wait 0x382100 uses it too)
    0x50000,                          # contrast threshold (0x2842aa, compared with engine context 0x80150fb0)
    0xfffffb9c,                       # -0x464, stack frame offset in 0x2d928e
}
TUNING_PTR = 0x6a01b798               # DP2x .data: -> P, [P] = S; the engine reads [S+8] (settings) and [S+0x18]

# ---- new code and data (DP2), fr_asm source; symbols: "ram:ADDR" = DP2x RAM address as ported --------------
LASTDIR = "ram:0x6a019a8c"            # DP2x last move direction byte (0 = minus, 1 = plus)
GEN_ASM = {
    # DP2x 0x38a1a0: hold current on the motor, in the last direction. The DP2x sends 0x850a|dir<<5. Here the
    # tail of the DP2 hold sequence 0x381048/0x381228 (which send 0x8517 / 0x8537): wait for the move-done flag
    # 0x35, hold command, release motor power 0x3836bc(2,1), motor state byte = 0, dly_tsk(1).
    "hold": [
        ("push", RP), ("enter", 8),
        ("mov", 14, 4), ("addi", -4, 4), ("ldi8", 0x35, 5), ("ldi8", 1, 6), ("ldi8", 0, 7),
        ("ldi8", 0xd2, 12), ("extsb", 12), ("int", 0x40), ("nop",), ("st", 13, 4),     # wai_flg(0x35, 1)
        ("ldi32", LASTDIR, 12), ("ldub", 12, 5), ("lsl", 5, 5), ("ldi20", 0x8517, 0), ("or", 0, 5),
        ("ldi8", 8, 4), ("ldi32", 0x250c28, 12), ("call_r", 12),
        ("ldi8", 2, 4), ("ldi8", 1, 5), ("ldi32", 0x3836bc, 12), ("call_r", 12),
        ("ldi32", 0x6a0195b9, 12), ("ldi8", 0, 0), ("stb", 0, 12),
        ("ldi8", 0xab, 12), ("ldi8", 1, 4), ("extsb", 12), ("int", 0x40), ("ldi8", 0, 4),  # dly_tsk(1)
        ("leave",), ("pop", RP), ("ret",),
    ],
    # DP2x 0x38ab64 / 0x38abf4 record the direction (in 0x38a144) for the hold; the DP2 moves don't.
    "move_minus": [("ldi32", LASTDIR, 12), ("ldi8", 0, 0), ("stb", 0, 12), ("ldi32", 0x381164, 12), ("jmp_r", 12)],
    "move_plus":  [("ldi32", LASTDIR, 12), ("ldi8", 1, 0), ("stb", 0, 12), ("ldi32", 0x381350, 12), ("jmp_r", 12)],
}


class Error(Exception):
    pass


def load(path, sha):
    b = open(os.path.join(ROOT, path), "rb").read()
    if hashlib.sha256(b).hexdigest() != sha:
        raise Error(f"{path}: not the expected stock file")
    return b


def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


class Ins:
    """One source instruction: address, bytes, objdump text."""
    __slots__ = ("a", "b", "t")

    def __init__(s, a, b, t):
        s.a, s.b, s.t = a, b, t

    @property
    def n(s):
        return len(s.b)


def decode_len(b):
    if b[0] == 0x9f and b[1] & 0xf0 == 0x80: return 6      # ldi:32
    if b[0] == 0x9b: return 4                               # ldi:20
    if b[0] == 0x9f and b[1] >= 0xc0: return 4              # coprocessor ops
    return 2


def ends_flow(ins, k):
    """True if control never falls through past instruction k of the list."""
    t = ins[k].t
    if re.match(r"(ret|reti|jmp|bra)\b(?!:d)", t): return True
    return k > 0 and re.match(r"(ret|jmp|bra):d\b", ins[k - 1].t) is not None


class Port:
    def __init__(s):
        s.x = load(DP2X_FILE, DP2X_SHA)
        s.d = bytearray(load(DP2_FILE, DP2_SHA))
        s.X = FW(os.path.join(ROOT, "work"))
        s.D = FW(os.path.join(ROOT, "work-dp2"))
        s.log = collections.defaultdict(list)       # category -> [(value, mapped, site)]
        s.errors = []

    def fail(s, msg):
        s.errors.append(msg)

    def xb(s, a, n): return s.x[a - BASE + HDR:a - BASE + HDR + n]
    def db(s, a, n): return bytes(s.d[a - BASE + HDR:a - BASE + HDR + n])

    # -- source units -------------------------------------------------------------------------------------------
    # cg.py's function starts are not reliable enough to cut on (prologues after register loads, 'st rp,@-r15'
    # run in the delay slot of a call:d f+2). So cut after every unconditional exit (and its padding), then merge
    # pieces a branch spans. Each unit can then be moved on its own; calls and pointers between units are relocated.
    def units(s):
        us = []
        for lo, hi in SRC:
            ins = s.decode(lo, hi)
            cuts, k = [], 0
            while k < len(ins):
                if ends_flow(ins, k):
                    k += 1
                    while k < len(ins) and ins[k].b == b"\0\0": k += 1
                    if k < len(ins): cuts.append(ins[k].a)
                else:
                    k += 1
            if not ends_flow(ins, len(ins) - 1) and ins[-1].b != b"\0\0":
                raise Error(f"range [{lo:#x},{hi:#x}) does not end with an exit")
            bounds = [lo] + cuts + [hi]
            for i in ins:
                r = s.refs(i)
                if r and r[0] == "br":
                    if not lo <= r[1] < hi: raise Error(f"{i.a:#x}: branch out of range to {r[1]:#x}")
                    a, b = sorted((i.a, r[1]))
                    bounds = [x for x in bounds if not a < x <= b]
            us += list(zip(bounds, bounds[1:]))
        return us

    def in_delay_slot(s, a):
        k = bisect.bisect_left(s.X.addrs, a) - 1
        while k >= 0 and not s.X.ins[k][2]: k -= 1
        return k >= 0 and s.X.ins[k][0] + 2 == a and re.match(r"\S+:d\b", s.X.ins[k][2]) is not None

    def decode(s, lo, hi):
        """Decode [lo, hi) and check every instruction length against objdump."""
        text = {a: t for a, _, t in s.X.ins[bisect.bisect_left(s.X.addrs, lo):bisect.bisect_left(s.X.addrs, hi)] if t}
        out, a = [], lo
        while a < hi:
            b = s.xb(a, 6)
            n = decode_len(b)
            if a not in text: raise Error(f"{a:#x}: objdump has no instruction here")
            out.append(Ins(a, b[:n], text[a]))
            a += n
        nxt = [t for t in text if lo <= t < hi]
        if sorted(nxt) != [i.a for i in out]: raise Error(f"[{lo:#x},{hi:#x}): instruction boundaries differ from objdump")
        return out

    # -- references out of one instruction --------------------------------------------------------------------
    @staticmethod
    def refs(i):
        """(kind, value) of the address-like operand of an instruction, or None."""
        b0, b1 = i.b[0], i.b[1]
        if b0 == 0x9f and b1 & 0xf0 == 0x80: return "ldi32", struct.unpack(">I", i.b[2:6])[0]
        if b0 == 0x9b: return "ldi20", (b1 >> 4) << 16 | struct.unpack(">H", i.b[2:4])[0]
        if 0xd0 <= b0 <= 0xdf: return "call", i.a + 2 + 2 * sext((b0 & 7) << 8 | b1, 11)
        if b0 >= 0xe0: return "br", i.a + 2 + 2 * sext(b1, 8)
        return None

    # -- reachability from ROOTS -------------------------------------------------------------------------------
    def select(s):
        allu = s.units()
        s.code = {u: s.decode(*u) for u in allu}
        owner = lambda v: next((u for u in allu if u[0] <= v < u[1]), None)
        seen, todo = set(), [owner(r) for r in ROOTS]
        if None in todo: raise Error("a root is outside the copied ranges")
        while todo:
            u = todo.pop()
            if u in seen: continue
            seen.add(u)
            for i in s.code[u]:
                r = s.refs(i)
                if not r: continue
                vals = [r[1]]
                if r[0] == "ldi32" and r[1] in PTR_TABLES:
                    vals = [struct.unpack(">I", s.xb(r[1] + 4 * k, 4))[0] for k in range(PTR_TABLES[r[1]])]
                for v in vals:
                    o = owner(v)
                    if o and o != u and (r[0] != "ldi32" or v == o[0] or r[1] in PTR_TABLES):
                        todo.append(o)
        s.dropped = [u for u in allu if u not in seen]
        s.kept = [u for u in allu if u in seen]

    # -- layout -----------------------------------------------------------------------------------------------
    def layout(s, pools):
        """Place generated blocks, units (each followed by its trampoline pool) and tables; allocate new RAM."""
        s.ram_new, a = {}, NEWRAM[0]
        for lo, hi in NEW:
            s.ram_new[lo] = (hi, a)
            a = (a + hi - lo + 3) & ~3
        if a > NEWRAM[1]: raise Error("NEWRAM overflow")
        s.ram_used = a - NEWRAM[0]
        items = [("gen", g, len(s.gen(g, 0, sizing=True))) for g in s.gens()]
        items += [("code", u, (u[1] - u[0]) + 8 * len(pools.get(u, ()))) for u in s.kept]
        items += [("ptrs", t, 4 * n) for t, n in PTR_TABLES.items()]
        free = [list(r) for r in SPACE]
        s.place = {}
        for kind, key, size in items:
            for r in free:
                a = (r[0] + 3) & ~3 if kind != "code" else r[0]
                if a + size <= r[1]:
                    s.place[key] = a
                    r[0] = a + size
                    break
            else:
                raise Error(f"no room for {kind} {key if kind != 'code' else key[0]} ({size} B); "
                            f"free: {[(hex(a), b - a) for a, b in free]}")
        s.free = free

    def gens(s):
        return list(GEN_ASM) + ["tuning", "init_data"]

    def sym(s, v, sizing=False):
        """Address of a generated-code symbol: an int, a GEN block name, or 'ram:DP2x address'."""
        if isinstance(v, int): return v
        if v.startswith("ram:"):
            m = s.mram(int(v[4:], 16))
            if m is None: raise Error(f"{v}: not in NEW")
            return m
        return 0 if sizing else s.place[v]

    def gen(s, name, base, sizing=False):
        if name in GEN_ASM:
            src = [tuple(s.sym(x, sizing) if isinstance(x, str) and k == 1 and ins[0] == "ldi32" else x
                         for k, x in enumerate(ins)) for ins in GEN_ASM[name]]
            return assemble(src, base)[0]
        if name == "tuning": return s.tuning_blob(base)
        if name == "init_data":        # DP2x .data values of the DATA_INIT ranges, copied to new RAM at task start
            return b"".join(s.xdata(lo, hi - lo) for lo, hi in DATA_INIT)
        raise KeyError(name)

    def xdata(s, ram, n):
        """Initial (.data) bytes of DP2x RAM: boot copies flash 0xc0000 to RAM 0x6a018184."""
        return s.xb(ram - 0x6a018184 + 0xc0000, n)

    def tuning_blob(s, base):
        """The part of the DP2x AF-library tuning graph the engine reads (check_tuning proves which), as flash data
        with its pointers moved. F0 -> F1 -> S (0x1c B: +8 settings, +0xc O, +0x18 window count);
        settings (0x34 B, curve pointers at +0x14, +0x2c); O (0xc B, +8 -> mask table); the two curve tables
        (0x88 B each); the mask table (0xea B: 26 rows of 8x 0x55 + 0x40, identical in the DP2)."""
        rd = lambda a: struct.unpack(">I", s.xdata(a, 4))[0]
        P = rd(TUNING_PTR); S = rd(P); ST = rd(S + 8); O = rd(S + 0xc); M = rd(O + 8)
        cur = sorted({rd(ST + 0x14), rd(ST + 0x2c)})
        if cur[1] - cur[0] != 0x88: raise Error("tuning curves are not two adjacent 0x88-byte tables")
        s_, st_, o_, c_, m_ = base + 8, base + 0x24, base + 0x58, base + 0x64, base + 0x174
        S_ = bytearray(s.xdata(S, 0x1c))
        struct.pack_into(">III", S_, 8, st_, o_, 0)
        struct.pack_into(">I", S_, 0x14, 0)                # +0x10, +0x14: pointers the engine never reads
        ST_ = bytearray(s.xdata(ST, 0x34))
        for o in (0x14, 0x2c): struct.pack_into(">I", ST_, o, c_ + rd(ST + o) - cur[0])
        O_ = bytearray(s.xdata(O, 0xc)); struct.pack_into(">I", O_, 8, m_)
        C, Mt = s.xdata(cur[0], 0x110), s.xdata(M, 0xea)
        if Mt != b"".join(b"\x55" * 8 + b"\x40" for _ in range(26)): raise Error("mask table is not the known one")
        blob = struct.pack(">II", base + 4, s_) + bytes(S_) + bytes(ST_) + bytes(O_) + C + Mt + b"\0\0"
        for k in range(0, len(blob) - 2, 4):
            w = struct.unpack(">I", blob[k:k + 4])[0]
            if w >> 24 in (0x6a, 0x80):     # (code-range values like 0x71000 are settings constants here)
                raise Error(f"tuning data +{k:#x} = {w:#x} looks like a DP2x RAM pointer")
        return blob

    def mcode(s, v):
        """Ported address of DP2x code address v, or None."""
        for u in s.kept:
            if u[0] <= v < u[1]: return s.place[u] + v - u[0]
        return None

    def mram(s, v):
        """DP2 address of DP2x RAM address v (shared DP2 variables first, then new RAM), or None."""
        for lo, hi, dst, w in RAM:
            if lo <= v < hi: return dst + v - lo
        for lo, (hi, dst) in s.ram_new.items():
            if lo <= v < hi: return dst + v - lo
        return None

    def mapval(s, v, site, kind):
        """DP2 value for a DP2x constant, logged by category. Error if it looks like an address and has no map."""
        m = s.mcode(v)
        if m is not None and kind == "ldi32": cat = "code(ported)"
        elif v in PTR_TABLES: m, cat = s.place[v], "table(ported)"
        elif v in CODE: m, cat = s.sym(CODE[v]), "code(DP2)" if isinstance(CODE[v], int) else "code(new)"
        elif v == TUNING_PTR: m, cat = s.place["tuning"], "tuning(flash)"
        elif v in FORBID: m, cat = v, "UNMAPPED"; s.fail(f"{site:#x}: {v:#x}: {FORBID[v]}")
        elif s.mram(v) is not None:
            m = s.mram(v)
            cat = next((f"ram(DP2,{w}B)" for lo, hi, dst, w in RAM if lo <= v < hi), "ram(new)")
        elif v < 0x40000 or v in KEEP: m, cat = v, "const"
        else:
            m, cat = v, "UNMAPPED"
            if any(u[0] <= v < u[1] for u in s.dropped): s.fail(f"{site:#x}: {v:#x} is in a dropped function")
            else: s.fail(f"{site:#x}: unmapped {kind} {v:#x}")
        s.log[cat].append((v, m, site))
        return m

    # -- encode -----------------------------------------------------------------------------------------------
    def encode(s, pools):
        s.log.clear(); s.errors.clear()
        s.need = collections.defaultdict(set)
        out = {}
        bytevars = {dst for lo, hi, dst, w in RAM if w == 1}
        for u in s.kept:
            base, ins = s.place[u], s.code[u]
            pool = sorted(pools.get(u, ()))
            pool_at = base + (u[1] - u[0])
            code = bytearray()
            narrow = s.narrow_sites(ins, bytevars)
            for k, i in enumerate(ins):
                pc = base + i.a - u[0]
                b = bytearray(i.b)
                r = s.refs(i)
                if r and r[0] == "ldi32":
                    b[2:6] = struct.pack(">I", s.mapval(r[1], i.a, "ldi32") & 0xffffffff)
                elif r and r[0] == "ldi20":
                    v = s.mapval(r[1], i.a, "ldi20")
                    if v != r[1]: raise Error(f"{i.a:#x}: ldi:20 of a relocated value")
                elif r and r[0] == "call":
                    t = s.mcode(r[1])
                    if t is None:
                        if r[1] not in CODE: s.fail(f"{i.a:#x}: call to unmapped {r[1]:#x}")
                        t = s.sym(CODE.get(r[1], pc + 2))
                    s.log["call"].append((r[1], t, i.a))
                    d = t - (pc + 2)
                    if not -2048 <= d < 2048:
                        if b[0] & 8 and ins[k + 1].t.endswith(",r12"):
                            raise Error(f"{i.a:#x}: call:d delay slot writes r12, can't use a trampoline")
                        s.need[u].add(t)
                        if t in pool: d = pool_at + 8 * pool.index(t) - (pc + 2)
                        else: d = 0
                    b[0], b[1] = b[0] & 0xf8 | (d >> 9) & 7, (d >> 1) & 0xff
                elif r and r[0] == "br":
                    if not u[0] <= r[1] < u[1]:
                        raise Error(f"{i.a:#x}: branch out of its function to {r[1]:#x}")
                if i.a in narrow:
                    b[0] = {0x04: 0x06, 0x14: 0x16}[b[0]]
                    s.log["narrowed"].append((i.a, pc, i.a))
                code += b
            for t in pool:                 # ldi:32 #t,r12 ; jmp @r12
                code += bytes([0x9f, 0x8c]) + struct.pack(">I", t) + b"\x97\x0c"
            out[base] = bytes(code)
        for g in s.gens():
            out[s.place[g]] = s.gen(g, s.place[g])
        for t, n in PTR_TABLES.items():
            tab = b""
            for k in range(n):
                v = struct.unpack(">I", s.xb(t + 4 * k, 4))[0]
                m = s.mcode(v)
                if m is None: raise Error(f"table {t:#x}[{k}] = {v:#x} is not in ported code")
                s.log["table entry"].append((v, m, t + 4 * k))
                tab += struct.pack(">I", m)
            out[s.place[t]] = tab
        return out

    def narrow_sites(s, ins, bytevars):
        """Addresses of ld/st that access a DP2 byte variable through a register loaded with ldi:32.
        Every use of that register until it is redefined must be a plain ld @rj,ri or st ri,@rj."""
        sites = set()
        for k, i in enumerate(ins):
            r = s.refs(i)
            if not (r and r[0] == "ldi32"): continue
            if not any(lo <= r[1] < hi and dst in bytevars for lo, hi, dst, w in RAM): continue
            reg, uses = i.b[1] & 0xf, 0
            for j in ins[k + 1:]:
                m = re.match(r"(\S+) (.*)", j.t)
                if not m: break
                op, args = m.group(1), m.group(2).split(",")
                if op == "ld" and args[0] == f"@r{reg}" or op == "st" and args[-1] == f"@r{reg}":
                    sites.add(j.a); uses += 1
                    if op == "ld" and args[1] == f"r{reg}": break
                    continue
                if f"r{reg}" in re.findall(r"r\d+", j.t):
                    if args[-1] == f"r{reg}" and op not in ("st", "cmp", "btstl", "btsth"): break  # redefined
                    raise Error(f"{j.a:#x}: byte variable pointer r{reg} used by '{j.t}'")
                if re.match(r"(ret|jmp|bra|call)", j.t): break
            if not uses: raise Error(f"{i.a:#x}: byte variable address loaded but not used by ld/st")
        return sites

    def claim_space(s):
        """Patch SPACE_PATCHES, then check no DP2 code outside SPACE refers into SPACE (except the kept setter)."""
        for site, old, new in SPACE_PATCHES:
            if s.db(site, 6) != bytes([0x9f, s.db(site, 2)[1]]) + struct.pack(">I", old) or s.db(site, 2)[1] & 0xf0 != 0x80:
                raise Error(f"{site:#x}: not ldi:32 {old:#x}")
            s.d[site + 2 - BASE + HDR:site + 6 - BASE + HDR] = struct.pack(">I", new)
        inside = lambda v: any(lo <= v < hi for lo, hi in SPACE)
        patched = {site for site, _, _ in SPACE_PATCHES} | set(SPACE_OK) | set(SPACE_HOOKED)
        for a, b, t in s.D.ins:
            if not t or inside(a) or a in patched: continue
            m = re.match(r"(?:ldi:32|ldi:20|call(?::d)?|b\w+(?::d)?) (0x[0-9a-f]+)", t)
            if m and inside(int(m.group(1), 16)):
                raise Error(f"DP2 {a:#x} '{t}' refers into the reused space")

    def check_tuning(s):
        """Every use of TUNING_PTR must be a chain of loads ending in one the flash copy provides:
        [[[T]]+8]+k inside the 0x34-byte settings, [[[T]]+0xc]+k inside O (0xc B; +8 is the mask table pointer,
        handed to the library), or lduh [[T]]+0x18 (window count). No stores, no escapes."""
        n = 0
        for u in s.kept:
            ins = s.code[u]
            for k, i in enumerate(ins):
                r = s.refs(i)
                if not (r and r[0] == "ldi32" and r[1] == TUNING_PTR): continue
                path, r13, last = {i.b[1] & 0xf: ()}, None, None
                for j in ins[k + 1:k + 10]:
                    m = re.match(r"(ld|lduh) @(?:\(r13,)?r(\d+)\)?,r(\d+)$", j.t)
                    if m and int(m.group(2)) in path:
                        off = r13 if "(r13," in j.t else 0
                        if off is None: raise Error(f"{j.a:#x}: tuning load with unknown offset")
                        path = {int(m.group(3)): path[int(m.group(2))] + (off,)}
                        last = (m.group(1), path[int(m.group(3))])
                        if len(last[1]) == 4 or last[1] == (0, 0, 0x18): break   # a data value, or O's table pointer
                        continue
                    m = re.match(r"ldi:8 (0x[0-9a-f]+),r13$", j.t)
                    if m: r13 = int(m.group(1), 16); continue
                    if any(f"r{x}" in re.findall(r"r\d+", j.t) for x in path) and not re.match(r"\S+ .*,r(\d+)$", j.t):
                        raise Error(f"{j.a:#x}: tuning pointer used by '{j.t}'")
                    break
                w = {"ld": 4, "lduh": 2}[last[0]] if last else 0
                fits = lambda obj, size: (len(last[1]) == 4 and last[1][:3] == (0, 0, obj) and
                                          last[1][3] + w <= size and last[1][3] % w == 0)
                ok = last and (fits(8, 0x34) or fits(0xc, 0xc) or last == ("lduh", (0, 0, 0x18)))
                if not ok: raise Error(f"{i.a:#x}: tuning access {last} is not in the flash copy")
                n += 1
        s.tuning_uses = n

    def build(s):
        s.claim_space()
        s.select()
        s.check_tuning()
        pools = {}
        for _ in range(10):
            s.layout(pools)
            out = s.encode(pools)
            if all(s.need[u] <= set(pools.get(u, ())) for u in s.need): break
            pools = {u: s.need[u] | set(pools.get(u, ())) for u in set(pools) | set(s.need)}
        else:
            raise Error("trampoline layout does not converge")
        if s.errors: raise Error(f"{len(s.errors)} unmapped references:\n  " + "\n  ".join(s.errors))
        s.pools, s.out = pools, out
        for a, b in out.items():
            s.d[a - BASE + HDR:a - BASE + HDR + len(b)] = b
        s.verify()

    # -- verify: disassemble the output and compare with the source, instruction by instruction ---------------
    def objdump(s, blob, vma):
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(blob); f.flush()
            txt = subprocess.run([OBJDUMP, "-D", "-b", "binary", "-m", "fr30", "-EB", f"--adjust-vma={vma:#x}",
                                  f.name], capture_output=True, text=True, check=True).stdout
        return {int(m.group(1), 16): m.group(3) for m in
                re.finditer(r"^\s+([0-9a-f]+):\t([0-9a-f ]+)\t(.+)$", txt, re.M)}

    def verify(s):
        n = 0
        for u in s.kept:
            base = s.place[u]
            got = s.objdump(s.out[base], base)
            for i in s.code[u]:
                pc = base + i.a - u[0]
                exp = i.t
                r = s.refs(i)
                if r and r[0] == "ldi32": exp = exp.replace(f"{r[1]:#x}", f"{struct.unpack('>I', s.db(pc + 2, 4))[0]:#x}", 1)
                if r and r[0] in ("call", "br"):
                    t = s.mcode(r[1]) if r[0] == "call" or True else None
                    if r[0] == "call" and t is None: t = s.sym(CODE[r[1]])
                    if r[0] == "call" and not -2048 <= t - (pc + 2) < 2048:
                        tr = s.place[u] + (u[1] - u[0]) + 8 * sorted(s.pools[u]).index(t)
                        if s.db(tr, 8) != bytes([0x9f, 0x8c]) + struct.pack(">I", t) + b"\x97\x0c":
                            raise Error(f"{pc:#x}: bad trampoline")
                        t = tr
                    exp = re.sub(rf"{r[1]:#x}\b", f"{t:#x}", exp, count=1)
                if i.a in {x[0] for x in s.log["narrowed"]}: exp = exp.replace("ld ", "ldub ", 1).replace("st ", "stb ", 1)
                if i.b == b"\0\0" and got.get(pc) is None and s.db(pc, 2) == b"\0\0": continue   # padding
                if got.get(pc) != exp:
                    raise Error(f"verify {i.a:#x}->{pc:#x}: expected '{exp}', got '{got.get(pc)}'")
                n += 1
        # the checked ldi:32 values themselves must be what mapval said
        for cat, l in s.log.items():
            if cat.startswith(("code", "table(", "ram", "const")):
                for v, m, site in l:
                    pass
        s.verified = n

    def report(s):
        print(f"copied {len(s.kept)} functions, {sum(u[1] - u[0] for u in s.kept)} B; "
              f"dropped (unreachable): {' '.join(f'{u[0]:x}' for u in s.dropped)}")
        for u in s.kept:
            p = s.pools.get(u, ())
            print(f"  {u[0]:#x}+{u[1] - u[0]:<4} -> {s.place[u]:#x}" + (f"  +{len(p)} trampolines" if p else ""))
        for t in PTR_TABLES: print(f"  table {t:#x} -> {s.place[t]:#x}")
        print("free:", ", ".join(f"{a:#x}+{b - a}" for a, b in s.free), f"; new RAM used {s.ram_used:#x} B")
        for cat in sorted(s.log):
            vals = collections.defaultdict(list)
            for v, m, site in s.log[cat]: vals[(v, m)].append(site)
            print(f"{cat}: {len(vals)} distinct")
            if cat in ("const", "narrowed") or len(vals) <= 40:
                for (v, m), sites in sorted(vals.items()):
                    print(f"    {v:#x} -> {m:#x}   x{len(sites)} ({' '.join(f'{a:x}' for a in sites[:4])})")
        print(f"verified {s.verified} instructions against objdump; {s.tuning_uses} tuning reads checked")


def checksum(d):
    struct.pack_into(">I", d, 0x4c, 0)
    struct.pack_into(">I", d, 0x4c, sum(d[0x80:]) & 0xffffffff)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--unhooked", action="store_true", help="write the image without hooks (NOT flashable)")
    a = ap.parse_args()
    p = Port()
    try:
        p.build()
    except Error as e:
        sys.exit(f"port_af: {e}")
    if a.report or not a.out: p.report()
    if a.out:
        if not a.unhooked:
            sys.exit("port_af: the hooks (step 3) are not written yet, so the image would still call into the reused "
                     "space (" + ", ".join(f"{k:#x}" for k in SPACE_HOOKED) + "). Use --unhooked for a test image.")
        checksum(p.d)
        open(a.out, "wb").write(p.d)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
