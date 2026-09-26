"""List the functions of the DP2x AF port set and every reference they make, classified.
   port_scan.py            -> summary of external code / RAM / data references"""
import sys, re, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from xmatch import FW
X = FW('work')
ENGINE = [0x283714, 0x283776, 0x283822, 0x283842, 0x283a82, 0x283afc, 0x283dc2, 0x283dd4, 0x283df6, 0x283e2c,
          0x283f52, 0x283f6a, 0x284114, 0x28416e, 0x284182, 0x2841cc, 0x2841de, 0x28425a, 0x2842f6, 0x284394,
          0x2843a2, 0x284466, 0x284490, 0x2844a8, 0x2d928e]
LENS = [0x38a084, 0x38a144, 0x38a1a0, 0x38a1c4, 0x38a64c, 0x38a7e8, 0x38ab64, 0x38abf4, 0x38ad38, 0x38ad5c,
        0x38adb4, 0x38ae04, 0x38ae34, 0x38ae4c, 0x38ae80, 0x38aee0, 0x38af04, 0x38af10, 0x38afb0, 0x38b000,
        0x38b00c, 0x38b038, 0x38b090, 0x38b188, 0x38b1d4, 0x38b21c]
PORT = ENGINE + LENS
def span(f):
    fx = X.func(f); return f, (fx[-1][0] + (4 if fx[-1][2].startswith(('ldi:20',)) else 2)) if fx else f
if __name__ == '__main__':
    code = collections.defaultdict(set); ram = collections.defaultdict(set); data = collections.defaultdict(set)
    internal_hits = set()
    inport = lambda t: any(p <= t < span(p)[1] + 6 for p in PORT)
    for f in PORT:
        for a, b, t in X.func(f):
            m = re.match(r'ldi:32 (0x[0-9a-f]+),', t)
            c = re.match(r'call(?::d)? (0x[0-9a-f]+)', t)
            v = int(m.group(1), 16) if m else (int(c.group(1), 16) if c else None)
            if v is None: continue
            if 0x1c0000 <= v < 0x3b3000:
                (internal_hits.add(v) if inport(v) else code[v].add(f))
            elif (v >> 24) in (0x6a, 0x80): ram[v].add(f)
            elif v >= 0x3b3000 and v < 0x800000: data[v].add(f)
            else: data[v].add(f)
    print("EXTERNAL CODE:"); [print(f"  {v:#x} <- {' '.join(hex(x) for x in sorted(s))}") for v, s in sorted(code.items())]
    print("RAM:"); [print(f"  {v:#x} <- {' '.join(hex(x) for x in sorted(s))}") for v, s in sorted(ram.items())]
    print("DATA/CONST:"); [print(f"  {v:#x} <- {' '.join(hex(x) for x in sorted(s))}") for v, s in sorted(data.items())]
