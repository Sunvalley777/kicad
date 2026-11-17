import json, re, sys, collections
ref=json.load(open(sys.argv[1],encoding='utf-8'))
s=open(sys.argv[2],encoding='utf-8').read()
kn={}
for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "((?:[^"\\]|\\.)*)"\)((?:\s*\(node[^\n]*\))*)',s):
    nodes=re.findall(r'\(ref "([^"]+)"\) \(pin "([^"]+)"\)',m.group(2))
    kn[m.group(1)]=set(nodes)
pin2k={}
for n,ns in kn.items():
    for x in ns: pin2k[x]=n
ok=0; bad=[]
refsets=[]
for name,grp in ref.items():
    g=set(map(tuple,grp))
    if not g: continue
    refsets.append((name,g))
    ks={pin2k.get(x) for x in g}
    if len(ks)==1 and None not in ks and kn[next(iter(ks))]==g: ok+=1
    else: bad.append((name,len(g),[(k,len(kn.get(k,()))) for k in ks][:4], [x for x in g if pin2k.get(x) is None][:4]))
# extra: kicad nets mixing multiple ref nets
allref=set().union(*[g for _,g in refsets])
print('EDIF nets with pins:',len(refsets),' exact match:',ok,' mismatch:',len(bad))
for b in bad[:25]: print(' ',b)
missing=[x for x in allref if x not in pin2k]
print('pins missing in KiCad netlist:',len(missing),missing[:10])
