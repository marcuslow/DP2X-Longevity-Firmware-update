#!/usr/bin/env python3
"""Patch Sigma DP2x firmware 1.02 so the lens barrel is not retracted at power-off
and is not re-homed (retract + extend) at power-on when it is already out.

Usage: patch_lens.py IN.bin OUT.bin [--also-playback] [--af-refine N] [--shutter-count] [--iso-max-200] [--eval-bias EV] [--af-25 | --af-5] [--af-fallback]
See NOTES.md for the analysis behind each patch.
"""
import argparse, hashlib, struct, sys
from fr_asm import assemble, RP

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

# ISO choices limited to Auto/50/100/200 (setting values 0..3; 4..7 = 400..3200).
# Both option tables live in the .data image (flash 0xc0000 -> RAM 0x6a018184 at boot).
# QS menu:   item @0xc7140 (count u16 at +0x14), 16-byte option records @0xc73f0.
# MENU grid: item @0xc9308 (count u16 at +0x18), 0x2c-byte option records @0xca3cc whose
#            +0x20 = (up, down) and +0x24 = (left, right) link to other option indices.
#            Rewired so Auto/50/100/200 form a closed 2x2 grid. See NOTES.md 9.8.
ISO_MAX_200 = [
    ("iso-qs-count",   0xc7154, "0008", "0004", "QS ISO item: 8 -> 4 options (Auto, 50, 100, 200)"),
    ("iso-menu-count", 0xc9320, "0008", "0004", "MENU ISO item: 8 -> 4 options"),
    ("iso-menu-auto",  0xca3ec, "0007000200070001", "0003000200030001", "MENU Auto: up 3200->200, left 3200->200"),
    ("iso-menu-50",    0xca418, "0006000300000002", "0002000300000002", "MENU 50: up 1600->100"),
    ("iso-menu-100",   0xca444, "0000000400010003", "0000000100010003", "MENU 100: down 400->50"),
    ("iso-menu-200",   0xca470, "0001000500020004", "0001000000020000", "MENU 200: down 800->Auto, right 400->Auto"),
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

# Code cave: the factory AFE-gain service block 0x27ea2e-0x27f8e3 (NOTES.md 9.5) is only reachable through
# 0x27f6b2, which is made to return at once. Allocation: 0x27eb4e eval-bias (56 B), 0x27eb86 af-25.
CAVE_LO, CAVE_HI = 0x27ea2e, 0x27f8e4
CAVE_STOCK_SHA = "374a41b805ce8ac7c0e79223b09545d9483f630e56ad1cb582187a589bbcd09e"
CAVE_ENTRY_OFF = ("cave-entry-off", 0x27f6b2, "1781", "9720",
                  "factory AFE-gain adjust (service USB cmd): return at once, its body is reused as a code cave")

# Automatic exposure bias when metering is Evaluative (AE mode 0 at RAM 0x6a019628), see NOTES.md 9.10/9.11.
# The two AE target functions 0x389724/0x389a7c call the meter 0x38968c and then apply EV comp; that
# call + EV-comp block is moved into a routine in the factory AFE-gain cave (NOTES.md 9.5), which then
# adds the bias. 16.16 fixed point, 65536 = 1 EV; adding makes the picture darker (like negative EV comp).
EVAL_CAVE = 0x27eb4e
EVAL_CAVE_OLD = ("8e0f17810f309f80ffffff44a6e09f810042479cc18da5cd001cfdfd"
                 "100c9f80ffffff5ca6e09f8100424784c18da5cd001cfdfd100cc000")

def eval_bias_patches(ev):
    step = round(abs(ev) * 65536)
    if not 0 < step <= 2 * 65536:
        sys.exit("--eval-bias must be non-zero and within +-2 EV")
    op = "a604" if ev < 0 else "ac04"   # add r0,r4 (darker) / sub r0,r4 (brighter)
    cave = ("1781"            # st rp,@-r15
            "9f8c0038968c"    # ldi:32 0x38968c,r12
            "971c"            # call @r12              ; r4 = metered value
            "c40d0185"        # ldi:8 0x40,r13 ; lduh @(r13,r8),r5  ; EV comp index (9 = 0 EV)
            "a895e208"        # cmp 9,r5 ; beq bias
            "8b5db42d"        # mov r5,r13 ; lsl 2,r13
            "a895fb030090"    # cmp 9,r5 ; bge:d minus ; ld @(r13,r9),r0   ; r9 = EV table 0x3c0198
            "f002a604"        # bra:d bias ; add r0,r4
            "ac04"            # minus: sub r0,r4
            "9f8c6a019628"    # bias: ldi:32 0x6a019628,r12
            "04c0a800e303"    # ld @r12,r0 ; cmp 0,r0 ; bne done  ; AE mode 0 = Evaluative
            f"9b{step >> 16:x}0{step & 0xffff:04x}"   # ldi:20 step,r0
            + op +
            "07819720")       # done: ld @r15+,rp ; ret
    assert len(cave) <= len(EVAL_CAVE_OLD)
    call = "9f8c0027eb4e971ce009"   # ldi:32 cave,r12 ; call @r12 ; bra <function epilogue>
    return [
        ("eval-bias-cave", EVAL_CAVE, EVAL_CAVE_OLD[:len(cave)], cave,
         f"cave: meter + EV comp, then {ev:+g} EV when metering = Evaluative"),
        ("eval-bias-call1", 0x38976c, "d78fc40d0185a895e209", call, "AE target 0x389724: meter+EV comp -> cave"),
        ("eval-bias-call2", 0x389ac0, "d5e5c40d0185a895e209", call, "AE target 0x389a7c: meter+EV comp -> cave"),
    ]

# 25 AF points (5x5) in the "9-point" AF-point mode: centre box stock (normal) size, the other 24 the stock
# small size. The point is stored as grid coordinates (RAM 0x80101dd0/dd4, centre (18,16)); mode at 0x80101ddc
# (1 = point grid, 2 = free move); frame-size flag 0x80101dd8 (0 normal, 1 small). See NOTES.md 9.12.
AF25_BASE = 0x27eb86
AF25_X = [1, 8, 18, 28, 35]     # 1 unit = 4 LCD px; small box 6.5 units wide, normal 10.5; 6 px beside the centre, 2 px elsewhere
AF25_Y = [0, 7, 16, 25, 32]     # 1 unit = 3 LCD px; small box 6.7 units tall, normal 10.7
PX, PY, FLAG, MODE = 0x80101dd0, 0x80101dd4, 0x80101dd8, 0x80101ddc
CURX, CURY = 0x6a02580c, 0x6a025808    # AF-point screen cursor

CENTRE_KEY_SRC = [
    # key 0x10 on the AF-point screen (raw key 8, DISPLAY; stock: grid <-> free move), jumped to from 0x2d73ba
    # inside 0x2d730c's frame (mode @(r14,-28)). In grid mode, off-centre: move to the centre by reusing the
    # stock arrow-key tail at 0x2d779c (erase old rect @(r14,-48), draw new, redraw old, store the point).
    # At the centre, or in free move: stock mode switch 0x2d75ec.
    "centre_key",
    ("ld_r14", -28, 0), ("cmpi", 1, 0), ("bne", "ck_stock"),
    ("ldi32", CURX, 12), ("ld", 12, 4), ("ldi8", 18, 1), ("cmp", 1, 4), ("bne", "ck_move"),
    ("ldi32", CURY, 12), ("ld", 12, 0), ("ldi8", 16, 1), ("cmp", 1, 0), ("beq", "ck_stock"),
    "ck_move",
    ("ldi32", CURX, 12), ("ld", 12, 4), ("ldi32", 0x25b564, 12), ("call_r", 12), ("sth_r14", -48, 4),
    ("ldi32", CURY, 12), ("ld", 12, 4), ("ldi32", 0x25b5a2, 12), ("call_r", 12), ("sth_r14", -46, 4),
    ("ldi8", 0, 4), ("ldi32", 0x25ce1c, 12), ("call_r", 12), ("sth_r14", -44, 4),
    ("ldi8", 0, 4), ("ldi32", 0x25ce78, 12), ("call_r", 12), ("sth_r14", -42, 4),
    ("ldi8", 18, 0), ("ldi32", 0x6a025804, 12), ("st", 0, 12),     # new cursor x
    ("ldi8", 16, 0), ("ldi32", 0x6a025800, 12), ("st", 0, 12),     # new cursor y
    ("ldi32", 0x2d779c, 12), ("jmp_r", 12),
    "ck_stock", ("ldi32", 0x2d75ec, 12), ("jmp_r", 12),
]

AF25_SRC = [
    # frame-size getter 0x21312c: in point-grid mode every point except the centre uses the small frame.
    # AF window, sensor readout, LCD box and sprite all follow this one getter.
    "size_hook",
    ("ldi32", FLAG, 12), ("ld", 12, 4),
    ("ldi32", MODE, 12), ("ld", 12, 0), ("cmpi", 1, 0), ("bne", "sh_ret"),
    ("ldi32", PX, 12), ("ld", 12, 0), ("ldi8", 18, 1), ("cmp", 1, 0), ("bne", "sh_small"),
    ("ldi32", PY, 12), ("ld", 12, 0), ("ldi8", 16, 1), ("cmp", 1, 0), ("beq", "sh_ret"),
    "sh_small", ("ldi8", 1, 4),
    "sh_ret", ("ret",),

    # nearest(r4 = value, r5 = 5-byte table) -> r4 = index of the closest entry
    "nearest",
    ("ldi8", 0, 6), ("ldi8", 0, 7), ("ldi20", 0xfffff, 3), ("mov", 5, 13),
    "nr_loop",
    ("ldub_r13", 6, 0), ("sub", 4, 0),
    ("cmpi", 0, 0), ("bge", "nr_pos"),
    ("ldi8", 0, 1), ("sub", 0, 1), ("mov", 1, 0),
    "nr_pos",
    ("cmp", 3, 0), ("bge", "nr_next"),
    ("mov", 0, 3), ("mov", 6, 7),
    "nr_next",
    ("addi", 1, 6), ("cmpi", 5, 6), ("blt", "nr_loop"),
    ("mov", 7, 4), ("ret",),

    # nav5(r4 = position, r5 = -1/+1, r6 = table) -> r4 = neighbouring table value (clamped)
    "nav5",
    ("push", RP), ("push", 8), ("push", 9),
    ("mov", 5, 8), ("mov", 6, 9), ("mov", 6, 5),
    ("ldi32", "nearest", 12), ("call_r", 12),
    ("add", 8, 4),
    ("cmpi", 0, 4), ("bge", "nv_a"), ("ldi8", 0, 4),
    "nv_a", ("cmpi", 4, 4), ("ble", "nv_b"), ("ldi8", 4, 4),
    "nv_b", ("mov", 9, 13), ("ldub_r13", 4, 4),
    ("pop", 9), ("pop", 8), ("pop", RP), ("ret",),

    # x step (jumped to from 0x2d7164 inside 0x2d7144's frame: x @(r14,8), key @(r14,12)); keys 11 left, 12 right
    "navx",
    ("ld_r14", 8, 4), ("ld_r14", 12, 0),
    ("cmpi", 11, 0), ("beq", "nx_l"), ("cmpi", 12, 0), ("beq", "nx_r"), ("bra", "nx_done"),
    "nx_l", ("ldi8", 0xff, 5), ("extsb", 5), ("bra", "nx_go"),
    "nx_r", ("ldi8", 1, 5),
    "nx_go", ("ldi32", "TX", 6), ("ldi32", "nav5", 12), ("call_r", 12),
    "nx_done", ("ldi32", 0x2d71e4, 12), ("jmp_r", 12),      # stock epilogue, returns r4

    # y step (from 0x2d720c inside 0x2d71ec's frame); keys 9 up, 10 down
    "navy",
    ("ld_r14", 8, 4), ("ld_r14", 12, 0),
    ("cmpi", 9, 0), ("beq", "ny_u"), ("cmpi", 10, 0), ("beq", "ny_d"), ("bra", "ny_done"),
    "ny_u", ("ldi8", 0xff, 5), ("extsb", 5), ("bra", "ny_go"),
    "ny_d", ("ldi8", 1, 5),
    "ny_go", ("ldi32", "TY", 6), ("ldi32", "nav5", 12), ("call_r", 12),
    "ny_done", ("ldi32", 0x2d728c, 12), ("jmp_r", 12),

    # replaces 0x2d7090 (snap to grid on switching free -> point mode): cursor and stored point = nearest grid point
    "snap",
    ("push", RP), ("push", 8),
    ("ldi32", PX, 12), ("ld", 12, 4), ("ldi32", "TX", 5), ("ldi32", "nearest", 12), ("call_r", 12),
    ("ldi32", "TX", 13), ("ldub_r13", 4, 8),
    ("ldi32", PY, 12), ("ld", 12, 4), ("ldi32", "TY", 5), ("ldi32", "nearest", 12), ("call_r", 12),
    ("ldi32", "TY", 13), ("ldub_r13", 4, 0),
    ("ldi32", CURX, 12), ("st", 8, 12), ("ldi32", PX, 12), ("st", 8, 12),
    ("ldi32", CURY, 12), ("st", 0, 12), ("ldi32", PY, 12), ("st", 0, 12),
    ("pop", 8), ("pop", RP), ("ret",),

    # wrapper on the frame draw 0x2d6e80(r4 = highlight, r5 = x, r6 = y): the stored point is set to (x, y)
    # while drawing, so the size getter gives this frame's own size; restored afterwards.
    "draww",
    ("push", RP), ("push", 8), ("push", 9),
    ("ldi32", PX, 12), ("ld", 12, 8), ("st", 5, 12),
    ("ldi32", PY, 12), ("ld", 12, 9), ("st", 6, 12),
    ("ldi32", "draw_orig", 12), ("call_r", 12),
    ("ldi32", PX, 12), ("st", 8, 12), ("ldi32", PY, 12), ("st", 9, 12),
    ("pop", 9), ("pop", 8), ("pop", RP), ("ret",),
    "draw_orig",                                            # the 4 displaced stock instructions, then resume
    ("raw", "8e0e"), ("push", RP), ("enter", 8), ("ld_r14", 12, 4),
    ("ldi32", 0x2d6e88, 12), ("jmp_r", 12),

    # replaces 0x2d7010(r4 = selected x, r5 = selected y): draw all 25 frames, the selected one highlighted
    "ovl5",
    ("push", RP), ("push", 8), ("push", 9), ("push", 10), ("push", 11),
    ("mov", 4, 10), ("mov", 5, 11), ("ldi8", 0, 8),
    "ov_i", ("ldi8", 0, 9),
    "ov_j",
    ("ldi32", "TX", 13), ("ldub_r13", 8, 5),
    ("ldi32", "TY", 13), ("ldub_r13", 9, 6),
    ("ldi8", 0, 4), ("cmp", 10, 5), ("bne", "ov_d"), ("cmp", 11, 6), ("bne", "ov_d"), ("ldi8", 1, 4),
    "ov_d", ("ldi32", "draww", 12), ("call_r", 12),
    ("addi", 1, 9), ("cmpi", 5, 9), ("blt", "ov_j"),
    ("addi", 1, 8), ("cmpi", 5, 8), ("blt", "ov_i"),
    ("pop", 11), ("pop", 10), ("pop", 9), ("pop", 8), ("pop", RP), ("ret",),

    # called from 0x2d6f22 (point-mode branch of the frame eraser 0x2d6f16): erase all 25 frames, each with
    # its own size (stored point set per frame as in draww)
    "erase5",
    ("push", RP), ("push", 8), ("push", 9), ("push", 10), ("push", 11), ("enter", 16),
    ("ldi32", PX, 12), ("ld", 12, 10), ("ldi32", PY, 12), ("ld", 12, 11),
    ("ldi8", 0, 8),
    "er_i", ("ldi8", 0, 9),
    "er_j",
    ("ldi32", "TY", 13), ("ldub_r13", 9, 0), ("ldi32", PY, 12), ("st", 0, 12),
    ("ldi32", "TX", 13), ("ldub_r13", 8, 4), ("ldi32", PX, 12), ("st", 4, 12),
    ("ldi32", 0x25b564, 12), ("call_r", 12), ("sth_r14", -8, 4),          # LCD x
    ("ldi32", PY, 12), ("ld", 12, 4),
    ("ldi32", 0x25b5a2, 12), ("call_r", 12), ("sth_r14", -6, 4),          # LCD y
    ("ldi8", 0, 4), ("ldi32", 0x25ce1c, 12), ("call_r", 12), ("sth_r14", -4, 4),   # width
    ("ldi8", 0, 4), ("ldi32", 0x25ce78, 12), ("call_r", 12), ("sth_r14", -2, 4),   # height
    ("ldi8", 1, 4), ("ldi8", 1, 5), ("mov", 14, 6), ("addi", -8, 6),
    ("ldi32", 0x3a7d60, 12), ("call_r", 12),                               # clear rect
    ("addi", 1, 9), ("cmpi", 5, 9), ("blt", "er_j"),
    ("addi", 1, 8), ("cmpi", 5, 8), ("blt", "er_i"),
    ("ldi32", PX, 12), ("st", 10, 12), ("ldi32", PY, 12), ("st", 11, 12),
    ("leave",), ("pop", 11), ("pop", 10), ("pop", 9), ("pop", 8), ("pop", RP), ("ret",),

] + CENTRE_KEY_SRC + [
    "TX", ("bytes", bytes(AF25_X)),
    "TY", ("bytes", bytes(AF25_Y)),
]

def af25_patches():
    code, L = assemble(AF25_SRC, AF25_BASE)
    assert AF25_BASE + len(code) <= 0x27f6b2, "af-25 code overflows the cave"
    def jmp(label):
        return assemble([("ldi32", L[label], 12), ("jmp_r", 12)], 0)[0].hex()
    erase_call = assemble([("ldi32", L["erase5"], 12), ("call_r", 12), ("bra", 0x2d7008)], 0x2d6f22)[0].hex()
    return [
        ("af25-cave",     AF25_BASE, None, code.hex(),
         f"cave: 25-point grid code + tables ({len(code)} B, ends {AF25_BASE + len(code):#x})"),
        ("af25-size",     0x21312c, "17810f019f8c8010", jmp("size_hook"),
         "frame-size getter -> point grid: centre = stored flag, others small"),
        ("af25-overlay",  0x2d7010, "8e0c17810f05c000", jmp("ovl5"), "3x3 frame overlay -> 5x5"),
        ("af25-snap",     0x2d7090, "17810f06cec49784", jmp("snap"), "snap to 3x3 grid -> 5x5 (also stores it)"),
        ("af25-draw",     0x2d6e80, "8e0e17810f022034", jmp("draww"), "frame draw: size follows the drawn frame"),
        ("af25-erase",    0x2d6f22, "c0007fe06fe0a830eb3c", erase_call, "erase 3x3 frames -> 5x5"),
        ("af25-nav-x",    0x2d7164, "c1005fe0c0203fd0", jmp("navx"), "left/right: step 16 -> next grid column"),
        ("af25-nav-y",    0x2d720c, "c0e05fe0c0203fd0", jmp("navy"), "up/down: step 14 -> next grid row"),
        ("af25-centre-key", 0x2d73bc, "002d75ec", f"{L['centre_key']:08x}",
         "AF-point screen DISPLAY (key 0x10): grid mode off-centre -> jump to centre, else stock grid/free switch"),
        ("af25-to-free",  0x2d7622, "e32e", "9fa0",
         "grid -> free move: always clamp to the normal-frame range (outer rows are outside it)"),
    ]

# 5 AF points in the point-grid AF mode: the centre plus the four rule-of-thirds intersections, all the stock
# (normal) box. The image is 2640 x 1760 and 1 grid unit = 32 px across / 24 lines down, so the thirds lines sit
# 440/32 = 13.75 -> 14 units and 293/24 = 12.2 -> 12 units from the centre. All inside the stock normal-frame
# range (x 2..34, y 2..30), so frame size, AF window and boot validation stay stock. See NOTES.md 9.14.
# Arrows: between corners ◀/▶ pick the left/right column and ▲/▼ the top/bottom row; from the centre
# ▲ top-left, ▶ top-right, ▼ bottom-right, ◀ bottom-left. DISPLAY returns to the centre (centre_key).
AF5_BASE = AF25_BASE
AF5_PTS = [(18, 16), (4, 4), (32, 4), (4, 28), (32, 28)]

AF5_SRC = [
    # next5(r4 = x, r5 = y, r6 = key) -> r4, r5 = new point. Leaf; uses r1.
    "next5",
    ("ldi8", 18, 1), ("cmp", 1, 4), ("bne", "n5_corner"),
    ("ldi8", 16, 1), ("cmp", 1, 5), ("bne", "n5_corner"),
    ("cmpi", 9, 6), ("beq", "n5_up"), ("cmpi", 12, 6), ("beq", "n5_right"),
    ("cmpi", 10, 6), ("beq", "n5_down"), ("cmpi", 11, 6), ("beq", "n5_left"),
    ("ret",),
    "n5_up",    ("ldi8", 4, 4), ("ldi8", 4, 5), ("ret",),
    "n5_right", ("ldi8", 32, 4), ("ldi8", 4, 5), ("ret",),
    "n5_down",  ("ldi8", 32, 4), ("ldi8", 28, 5), ("ret",),
    "n5_left",  ("ldi8", 4, 4), ("ldi8", 28, 5), ("ret",),
    "n5_corner",                                   # snap to the nearest corner, then apply the key
    ("ldi8", 18, 1), ("cmp", 1, 4), ("blt", "n5_xlo"), ("ldi8", 32, 4), ("bra", "n5_xok"),
    "n5_xlo", ("ldi8", 4, 4),
    "n5_xok",
    ("ldi8", 16, 1), ("cmp", 1, 5), ("blt", "n5_ylo"), ("ldi8", 28, 5), ("bra", "n5_yok"),
    "n5_ylo", ("ldi8", 4, 5),
    "n5_yok",
    ("cmpi", 11, 6), ("bne", "n5_k1"), ("ldi8", 4, 4),
    "n5_k1", ("cmpi", 12, 6), ("bne", "n5_k2"), ("ldi8", 32, 4),
    "n5_k2", ("cmpi", 9, 6), ("bne", "n5_k3"), ("ldi8", 4, 5),
    "n5_k3", ("cmpi", 10, 6), ("bne", "n5_k4"), ("ldi8", 28, 5),
    "n5_k4", ("ret",),

    # x step (from 0x2d7164 in 0x2d7144's frame: x @(r14,8), key @(r14,12)); y from the cursor
    "navx5",
    ("ld_r14", 8, 4), ("ldi32", CURY, 12), ("ld", 12, 5), ("ld_r14", 12, 6),
    ("ldi32", "next5", 12), ("call_r", 12),
    ("ldi32", 0x2d71e4, 12), ("jmp_r", 12),
    # y step (from 0x2d720c in 0x2d71ec's frame: y @(r14,8)); x from the cursor
    "navy5",
    ("ldi32", CURX, 12), ("ld", 12, 4), ("ld_r14", 8, 5), ("ld_r14", 12, 6),
    ("ldi32", "next5", 12), ("call_r", 12), ("mov", 5, 4),
    ("ldi32", 0x2d728c, 12), ("jmp_r", 12),

    # near5(r4 = x, r5 = y) -> r4 = index of the closest point (city-block distance)
    "near5",
    ("ldi8", 0, 6), ("ldi8", 0, 7), ("ldi20", 0xfffff, 3),
    "p5_loop",
    ("ldi32", "PTX", 13), ("ldub_r13", 6, 0), ("sub", 4, 0),
    ("cmpi", 0, 0), ("bge", "p5_a"), ("ldi8", 0, 1), ("sub", 0, 1), ("mov", 1, 0),
    "p5_a",
    ("ldi32", "PTY", 13), ("ldub_r13", 6, 2), ("sub", 5, 2),
    ("cmpi", 0, 2), ("bge", "p5_b"), ("ldi8", 0, 1), ("sub", 2, 1), ("mov", 1, 2),
    "p5_b",
    ("add", 2, 0), ("cmp", 3, 0), ("bge", "p5_next"), ("mov", 0, 3), ("mov", 6, 7),
    "p5_next",
    ("addi", 1, 6), ("cmpi", 5, 6), ("blt", "p5_loop"),
    ("mov", 7, 4), ("ret",),

    # replaces 0x2d7090 (snap on switching free -> point mode): cursor and stored point = nearest point
    "snap5",
    ("push", RP), ("push", 8),
    ("ldi32", PX, 12), ("ld", 12, 4), ("ldi32", PY, 12), ("ld", 12, 5),
    ("ldi32", "near5", 12), ("call_r", 12), ("mov", 4, 8),
    ("ldi32", "PTX", 13), ("ldub_r13", 8, 0),
    ("ldi32", CURX, 12), ("st", 0, 12), ("ldi32", PX, 12), ("st", 0, 12),
    ("ldi32", "PTY", 13), ("ldub_r13", 8, 0),
    ("ldi32", CURY, 12), ("st", 0, 12), ("ldi32", PY, 12), ("st", 0, 12),
    ("pop", 8), ("pop", RP), ("ret",),

    # replaces 0x2d7010(r4 = selected x, r5 = selected y): draw the 5 frames, the selected one highlighted
    "ovl5p",
    ("push", RP), ("push", 8), ("push", 10), ("push", 11),
    ("mov", 4, 10), ("mov", 5, 11), ("ldi8", 0, 8),
    "o5_loop",
    ("ldi32", "PTX", 13), ("ldub_r13", 8, 5), ("ldi32", "PTY", 13), ("ldub_r13", 8, 6),
    ("ldi8", 0, 4), ("cmp", 10, 5), ("bne", "o5_d"), ("cmp", 11, 6), ("bne", "o5_d"), ("ldi8", 1, 4),
    "o5_d", ("ldi32", 0x2d6e80, 12), ("call_r", 12),
    ("addi", 1, 8), ("cmpi", 5, 8), ("blt", "o5_loop"),
    ("pop", 11), ("pop", 10), ("pop", 8), ("pop", RP), ("ret",),

    # called from 0x2d6f22 (point-mode branch of the eraser 0x2d6f16): erase the 5 frames
    "erase5p",
    ("push", RP), ("push", 8), ("enter", 16), ("ldi8", 0, 8),
    "e5_loop",
    ("ldi32", "PTX", 13), ("ldub_r13", 8, 4), ("ldi32", 0x25b564, 12), ("call_r", 12), ("sth_r14", -8, 4),
    ("ldi32", "PTY", 13), ("ldub_r13", 8, 4), ("ldi32", 0x25b5a2, 12), ("call_r", 12), ("sth_r14", -6, 4),
    ("ldi8", 0, 4), ("ldi32", 0x25ce1c, 12), ("call_r", 12), ("sth_r14", -4, 4),
    ("ldi8", 0, 4), ("ldi32", 0x25ce78, 12), ("call_r", 12), ("sth_r14", -2, 4),
    ("ldi8", 1, 4), ("ldi8", 1, 5), ("mov", 14, 6), ("addi", -8, 6),
    ("ldi32", 0x3a7d60, 12), ("call_r", 12),
    ("addi", 1, 8), ("cmpi", 5, 8), ("blt", "e5_loop"),
    ("leave",), ("pop", 8), ("pop", RP), ("ret",),
] + CENTRE_KEY_SRC + [
    "PTX", ("bytes", bytes(x for x, _ in AF5_PTS)),
    "PTY", ("bytes", bytes(y for _, y in AF5_PTS)),
]

def af5_patches():
    code, L = assemble(AF5_SRC, AF5_BASE)
    assert AF5_BASE + len(code) <= 0x27f6b2, "af-5 code overflows the cave"
    def jmp(label):
        return assemble([("ldi32", L[label], 12), ("jmp_r", 12)], 0)[0].hex()
    erase_call = assemble([("ldi32", L["erase5p"], 12), ("call_r", 12), ("bra", 0x2d7008)], 0x2d6f22)[0].hex()
    return [
        ("af5-cave",      AF5_BASE, None, code.hex(),
         f"cave: 5-point (centre + thirds) code + tables ({len(code)} B, ends {AF5_BASE + len(code):#x})"),
        ("af5-overlay",   0x2d7010, "8e0c17810f05c000", jmp("ovl5p"), "3x3 frame overlay -> 5 points"),
        ("af5-snap",      0x2d7090, "17810f06cec49784", jmp("snap5"), "snap to 3x3 grid -> nearest of 5 (also stores it)"),
        ("af5-erase",     0x2d6f22, "c0007fe06fe0a830eb3c", erase_call, "erase 3x3 frames -> 5"),
        ("af5-nav-x",     0x2d7164, "c1005fe0c0203fd0", jmp("navx5"), "arrows: centre/thirds navigation (x)"),
        ("af5-nav-y",     0x2d720c, "c0e05fe0c0203fd0", jmp("navy5"), "arrows: centre/thirds navigation (y)"),
        ("af5-centre-key", 0x2d73bc, "002d75ec", f"{L['centre_key']:08x}",
         "AF-point screen DISPLAY (key 0x10): off-centre -> jump to centre, else stock grid/free switch"),
    ]

# AF fallback: when the coarse scan ends in failure, aim the final lens move at the position of the highest contrast
# seen during the scan (instead of the AF library's last estimate), if that maximum stands clearly above the floor.
# The box stays red. Hooked at the AF end function 0x38b090(r): r = 2 is failure; the stock finish state 0x2842f6
# then moves the lens to 0x8015153e (coarse state 0x80150fac = 1). History: n @0x80150fb8, contrast B (u32)
# @0x80151154, positions (negated, u16) @0x80151474. Contrast at frame i belongs to the lens position of frame
# i-1 (stock latency correction 0x283f6a). See NOTES.md 9.15.
AFFB_BASE = 0x27eec0          # after the larger AF-point variant (af-25 ends 0x27eea2, af-5 0x27edf0)

AFFB_SRC = [
    "fb",
    ("cmpi", 2, 4), ("bne", "fb_out"),                               # failure only
    ("ldi32", 0x80150fac, 12), ("ld", 12, 0), ("cmpi", 1, 0), ("bne", "fb_out"),   # coarse scan only
    ("ldi32", 0x80150fb8, 12), ("ld", 12, 5), ("cmpi", 3, 5), ("blt", "fb_out"),   # n >= 3
    ("ldi32", 0x80151154, 13),
    ("ldi8", 0, 6), ("ldi8", 0, 7), ("ldi8", 0, 1), ("ldi32", 0xffffffff, 2),    # i, argmax, max, min
    "fb_loop",
    ("mov", 6, 0), ("lsl", 2, 0), ("ld_r13", 0, 3),                  # r3 = B[i]
    ("cmp", 2, 3), ("bnc", "fb_nomin"), ("mov", 3, 2),               # min (unsigned)
    "fb_nomin",
    ("cmp", 1, 3), ("bls", "fb_nomax"), ("mov", 3, 1), ("mov", 6, 7),    # max (unsigned), argmax
    "fb_nomax",
    ("addi", 1, 6), ("cmp", 5, 6), ("blt", "fb_loop"),
    ("mov", 2, 0), ("lsr", 2, 0), ("add", 2, 0),                     # r0 = min + min/4
    ("cmp", 0, 1), ("bls", "fb_out"),                                # max must beat it
    ("cmpi", 0, 7), ("beq", "fb_i0"), ("addi", -1, 7),               # latency: position of frame argmax-1
    "fb_i0",
    ("mov", 7, 0), ("add", 7, 0),
    ("ldi32", 0x80151474, 13), ("lduh_r13", 0, 0),
    ("ldi32", 0x8015153e, 12), ("sth", 0, 12),                       # final-move target
    "fb_out",                                                        # displaced stock prologue, then resume
    ("push", 8), ("push", RP), ("enter", 12), ("mov", 4, 8),
    ("ldi32", 0x38b098, 12), ("jmp_r", 12),
]

def affb_patches():
    code, L = assemble(AFFB_SRC, AFFB_BASE)
    assert AFFB_BASE + len(code) <= 0x27f6b2, "af-fallback code overflows the cave"
    hook = assemble([("ldi32", L["fb"], 12), ("jmp_r", 12)], 0)[0].hex()
    return [
        ("affb-cave", AFFB_BASE, None, code.hex(),
         f"cave: AF fallback to highest-contrast position ({len(code)} B, ends {AFFB_BASE + len(code):#x})"),
        ("affb-hook", 0x38b090, "170817810f038b48", hook,
         "AF end: on coarse-scan failure, final move goes to the highest-contrast position seen"),
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
    ap.add_argument("--iso-max-200", action="store_true",
                    help="QS and MENU offer only ISO Auto/50/100/200 (set ISO to one of these before flashing)")
    ap.add_argument("--eval-bias", type=float, metavar="EV",
                    help="extra exposure bias in Evaluative metering only, e.g. -0.5 (uses the AFE-gain cave)")
    ap.add_argument("--af-25", action="store_true",
                    help="AF-point grid mode: 25 points (5x5), centre normal size, the rest small; DISPLAY jumps to the centre (uses the cave)")
    ap.add_argument("--af-5", action="store_true",
                    help="AF-point grid mode: 5 points (centre + rule-of-thirds), stock size; DISPLAY jumps to the centre")
    ap.add_argument("--af-fallback", action="store_true",
                    help="when AF fails, move the lens to the sharpest position seen during the scan (box stays red)")
    a = ap.parse_args()
    if a.af_25 and a.af_5:
        sys.exit("--af-25 and --af-5 are alternatives (same cave space)")
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
    if a.iso_max_200:
        todo += ISO_MAX_200
    if a.eval_bias:
        todo += eval_bias_patches(a.eval_bias)
    if a.af_25:
        todo += af25_patches()
    if a.af_5:
        todo += af5_patches()
    if a.af_fallback:
        todo += affb_patches()
    if a.eval_bias or a.af_25 or a.af_5 or a.af_fallback:
        o = off(CAVE_LO)
        if hashlib.sha256(d[o:off(CAVE_HI)]).hexdigest() != CAVE_STOCK_SHA:
            sys.exit("code cave 0x27ea2e-0x27f8e3 is not stock")
        todo.insert(len(PATCHES), CAVE_ENTRY_OFF)
    for name, addr, old, new, desc in todo:
        o = off(addr); new = bytes.fromhex(new)
        old = d[o:o+len(new)] if old is None else bytes.fromhex(old)   # None: cave, checked by hash above
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
