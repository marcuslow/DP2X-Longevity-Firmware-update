import re
d=open('img.bin','rb').read()
BASE=0x40000
def cstr(a):
    o=a-BASE
    if not (0<=o<len(d)): return None
    e=d.find(b'\0',o,o+200)
    if e<=o: return None
    s=d[o:e]
    if len(s)<4 or not all(32<=c<127 or c in (9,10,13,27) for c in s): return None
    return s.decode('latin1').replace('\n','\\n')
out=open('dis_ann.txt','w')
r=re.compile(r'ldi:32 (0x[0-9a-f]+),')
for line in open('dis.txt'):
    m=r.search(line)
    if m:
        s=cstr(int(m.group(1),16))
        if s: line=line.rstrip('\n')+'   ; "'+s+'"\n'
    out.write(line)
