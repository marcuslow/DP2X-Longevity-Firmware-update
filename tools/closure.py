"""Call closure of a set of root functions in SRC, with DST twins.
   closure.py SRC_DIR DST_DIR STOP_BELOW STOP_ABOVE ROOT...
Functions outside [STOP_BELOW, STOP_ABOVE) are listed as external and not followed."""
import sys, re
sys.path.insert(0, __import__('os').path.dirname(__file__))
from xmatch import FW, best
src, dst = FW(sys.argv[1]), FW(sys.argv[2])
lo, hi = int(sys.argv[3], 16), int(sys.argv[4], 16)
roots = [int(a, 16) for a in sys.argv[5:]]
bysite = {}
for s, t in src.calls: bysite.setdefault(src.fstart(s), set()).add(t)
seen, todo, ext = set(), list(roots), set()
while todo:
    f = todo.pop()
    if f in seen: continue
    seen.add(f)
    for t in bysite.get(f, ()):
        if t in seen: continue
        (todo if lo <= t < hi and t not in roots or t in roots else ext.add(t)) if False else None
        if 0x1c0000 <= t < 0x3b3000 and (lo <= t < hi): todo.append(t)
        else: ext.add(t)
RAM = re.compile(r'ldi:32 (0x(?:6a|80)[0-9a-f]{6}),')
tot = 0
for f in sorted(seen):
    fx = src.func(f); size = (fx[-1][0] + 2 - f) if fx else 0; tot += size
    rams = sorted({m.group(1) for _, _, t in fx for m in [RAM.search(t)] if m})
    n, res = best(src, dst, f, 1)
    tw = f"{res[0][1]:#x} {res[0][0]:.2f}" if res else "-"
    print(f"{f:#x} {size:5d}B  twin {tw:16s} ram {' '.join(rams)}")
print(f"total {len(seen)} funcs, {tot} bytes; external callees: {len(ext)}")
print("ext:", ' '.join(hex(e) for e in sorted(ext)))
