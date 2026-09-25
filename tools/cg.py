import re,pickle,collections
lines=open('dis_ann.txt').read().split('\n')
ins=[]  # (addr, bytes, text)
r=re.compile(r'^\s+([0-9a-f]+):\t([0-9a-f ]+)\t?(.*)$')
for l in lines:
    m=r.match(l)
    if m: ins.append((int(m.group(1),16),m.group(2).strip(),m.group(3)))
# function starts: 'st rp,@-r15' (possibly preceded by st rX,@-r15 pushes) or 'enter'
starts=set()
for i,(a,b,t) in enumerate(ins):
    if t.startswith('st rp,@-r15'):
        j=i
        while j>0 and re.match(r'st r\d+,@-r15|stm1|stm0',ins[j-1][2]) : j-=1
        starts.add(ins[j][0])
starts=sorted(starts)
import bisect
def func_of(a):
    k=bisect.bisect_right(starts,a)-1
    return starts[k] if k>=0 else None
calls=collections.defaultdict(set); callers=collections.defaultdict(set)
last={}
for i,(a,b,t) in enumerate(ins):
    m=re.match(r'ldi:32 (0x[0-9a-f]+),(r\d+)',t)
    if m: last[m.group(2)]=int(m.group(1),16)
    m=re.match(r'call(:d)? @(r\d+)',t)
    tgt=None
    if m: tgt=last.get(m.group(2))
    m=re.match(r'call(:d)? (0x[0-9a-f]+)',t)
    if m: tgt=int(m.group(2),16)
    if tgt:
        f=func_of(a); calls[f].add(tgt); callers[tgt].add(f)
    if t.startswith('ret') or t.startswith('bra') or t.startswith('jmp'): last={}
pickle.dump((ins,starts,dict(calls),dict(callers)),open('cg.pkl','wb'))
print(len(ins),len(starts))
