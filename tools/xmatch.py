"""Match functions between two disassembled firmwares (work dirs made by tools/disasm.sh).
   xmatch.py SRC_DIR DST_DIR ADDR...   -> best DST twin of each SRC function (exact normalized match, else fuzzy)
Operands >= 0x100 (addresses, big constants, branch targets) are masked before comparing."""
import pickle, re, sys, bisect, collections, difflib, os
R = re.compile(r'0x[0-9a-f]+')
def norm(t): return R.sub(lambda m: m.group(0) if int(m.group(0), 16) < 0x100 else 'N', t)
def mn(t): return t.split()[0] if t else ''
class FW:
    def __init__(s, d):
        s.dir = d
        s.ins, s.starts, _, _ = pickle.load(open(os.path.join(d, 'cg.pkl'), 'rb'))
        s.addrs = [a for a, _, _ in s.ins]
        _, s.calls, s.callers = pickle.load(open(os.path.join(d, 'cg2.pkl'), 'rb'))
        s._g = None
    def fstart(s, a): return s.starts[bisect.bisect_right(s.starts, a) - 1]
    def fend(s, a):
        k = bisect.bisect_right(s.starts, a); return s.starts[k] if k < len(s.starts) else a + 0x1000
    def func(s, a):
        end = s.fend(a); i = bisect.bisect_left(s.addrs, a); out = []
        while i < len(s.ins) and s.ins[i][0] < end:
            if s.ins[i][2]: out.append(s.ins[i])
            i += 1
        return out
    def grams(s):
        if s._g is None:
            c = os.path.join(s.dir, 'grams.pkl')
            if os.path.exists(c): s._g = pickle.load(open(c, 'rb'))
            else:
                s._g = {}
                for st in s.starts:
                    m = [mn(t) for _, _, t in s.func(st)]
                    s._g[st] = set(tuple(m[i:i+4]) for i in range(max(1, len(m) - 3)))
                pickle.dump(s._g, open(c, 'wb'))
            s.inv = collections.defaultdict(list)
            for st, g in s._g.items():
                for x in g: s.inv[x].append(st)
        return s._g
def best(src, dst, a, top=3):
    fx = src.func(a); nx = [norm(t) for _, _, t in fx]
    m = [mn(t) for _, _, t in fx]; gx = set(tuple(m[i:i+4]) for i in range(max(1, len(m) - 3)))
    dg = dst.grams(); cnt = collections.Counter()
    for x in gx:
        l = dst.inv.get(x, ())
        if len(l) < 300:
            for st in l: cnt[st] += 1
    res = []
    for st, c in cnt.most_common(20):
        r = difflib.SequenceMatcher(None, nx, [norm(t) for _, _, t in dst.func(st)], autojunk=False).ratio()
        res.append((r, st))
    res.sort(reverse=True)
    return len(fx), res[:top]
if __name__ == '__main__':
    src, dst = FW(sys.argv[1]), FW(sys.argv[2])
    for a in sys.argv[3:]:
        n, res = best(src, dst, int(a, 16))
        print(f"{int(a,16):#x} ({n} ins): " + "  ".join(f"{st:#x} {r:.2f}" for r, st in res))
