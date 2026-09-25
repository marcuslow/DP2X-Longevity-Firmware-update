import pickle,sys,bisect
ins,starts,calls,callers=pickle.load(open('cg.pkl','rb'))
addrs=[a for a,_,_ in ins]
s=int(sys.argv[1],16);e=int(sys.argv[2],16)
i=bisect.bisect_left(addrs,s)
ss=set(starts)
while i<len(ins) and ins[i][0]<e:
    a,b,t=ins[i]
    if t:
        mark = ('\n--- '+hex(a)+' callers='+str([hex(c) for c in sorted(x for x in callers.get(a,()) if x)][:10])) if (a in ss or callers.get(a)) else ''
        if mark: print(mark)
        print(f"{a:8x}: {b:12s} {t}")
    i+=1
