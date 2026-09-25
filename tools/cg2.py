import re,pickle,collections,bisect
ins,starts,_,_=pickle.load(open('cg.pkl','rb'))
addrs=[a for a,_,_ in ins]
idx={a:i for i,a in enumerate(addrs)}
def bytes_at(a):
    i=idx.get(a); return ins[i][1] if i is not None else None
calls=[]  # (site, target, argr4)
last={}
for i,(a,b,t) in enumerate(ins):
    m=re.match(r'ldi:32 (0x[0-9a-f]+),(r\d+)',t)
    if m: last[m.group(2)]=int(m.group(1),16)
    tgt=None; dl=False
    m=re.match(r'call(:d)? @(r\d+)',t)
    if m: tgt=last.get(m.group(2)); dl=bool(m.group(1))
    m=re.match(r'call(:d)? (0x[0-9a-f]+)',t)
    if m: tgt=int(m.group(2),16); dl=bool(m.group(1))
    if tgt is not None:
        if dl and i+1<len(ins):
            ds=ins[i+1][1]
            if bytes_at(tgt-2)==ds: tgt-=2
        calls.append((a,tgt))
    if re.match(r'(ret|bra|jmp)',t): last={}
callers=collections.defaultdict(list)
for s,t in calls: callers[t].append(s)
pickle.dump((ins,calls,dict(callers)),open('cg2.pkl','wb'))
