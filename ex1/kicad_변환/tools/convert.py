#!/usr/bin/env python3
"""OrCAD Capture EDIF 2.0 (+ Capture XML for no-connects/notes) -> KiCad schematic (.kicad_sch, KiCad 7 format).

Connectivity source of truth: EDIF nets (joined portRefs). Geometry: EDIF wires, instance transforms.
Usage: python3 convert.py design.edf design.xml out.kicad_sch
"""
import sys, re, uuid, math, collections, json
import xml.etree.ElementTree as ET
from sexp import parse, find, first, walk

S = 0.254  # 1 OrCAD unit = 1/100 inch = 0.254 mm
U = lambda: str(uuid.uuid4())

# ---------- EDIF helpers ----------
def dec(s):
    """EDIF string escapes: %37% -> '%'"""
    return re.sub(r'%(\d+)%', lambda m: chr(int(m.group(1))), s)

JUST = {'UPPERLEFT': 'left top', 'UPPERCENTER': 'top', 'UPPERRIGHT': 'right top',
        'CENTERLEFT': 'left', 'CENTERCENTER': '', 'CENTERRIGHT': 'right',
        'LOWERLEFT': 'left bottom', 'LOWERCENTER': 'bottom', 'LOWERRIGHT': 'right bottom'}

def text_disp(node):
    """(origin, kicad_angle, kicad_justify) from the first display in node"""
    for x in walk(node):
        if x[0] == 'display':
            org = first(x, 'origin'); j = first(x, 'justify'); o = first(x, 'orientation')
            if org:
                p = (int(org[1][1]), int(org[1][2]))
                ang = 90 if (o and o[1] in ('R90', 'R270', 'MXR90', 'MYR90')) else 0
                return p, ang, JUST.get(j[1] if j else 'LOWERLEFT', 'left bottom')
    return None, 0, 'left bottom'
def ident(x):
    """identifier of an EDIF nameDef (plain, rename, or name)."""
    if isinstance(x, list):
        if x[0] in ('rename', 'name'):
            return ident(x[1])
    return x

def display_name(x):
    if isinstance(x, list):
        if x[0] == 'rename':
            return dec(x[2].strip('"'))
        if x[0] == 'name':
            return display_name(x[1])
    return dec(str(x).strip('"'))

def sval(prop):
    s = first(prop, 'string')
    if not s:
        return None
    v = s[1]
    if isinstance(v, list):  # stringDisplay
        v = v[1]
    return dec(v.strip('"'))

def props(node):
    out = {}
    for p in find(node, 'property'):
        out[display_name(p[1]) if isinstance(p[1], list) else p[1]] = sval(p)
    return out

ORI = {'R0': lambda x, y: (x, y), 'R90': lambda x, y: (-y, x), 'R180': lambda x, y: (-x, -y),
       'R270': lambda x, y: (y, -x), 'MX': lambda x, y: (x, -y), 'MY': lambda x, y: (-x, y),
       'MXR90': lambda x, y: (y, x), 'MYR90': lambda x, y: (-y, -x)}
INV = {'R0': 'R0', 'R90': 'R270', 'R180': 'R180', 'R270': 'R90', 'MX': 'MX', 'MY': 'MY', 'MXR90': 'MXR90', 'MYR90': 'MYR90'}
# EDIF orientation -> KiCad (angle, mirror) (determined empirically with kicad-cli)
KI = {'R0': (0, None), 'R90': (90, None), 'R180': (180, None), 'R270': (270, None),
      'MX': (0, 'x'), 'MY': (0, 'y'), 'MXR90': (90, 'y'), 'MYR90': (90, 'x')}

def transform(tr):
    if not tr:
        return 'R0', (0, 0)
    o = first(tr, 'orientation'); org = first(tr, 'origin')
    ori = o[1] if o else 'R0'
    ox, oy = (int(org[1][1]), int(org[1][2])) if org else (0, 0)
    return ori, (ox, oy)

def pts(node, T=lambda x, y: (x, y)):
    return [T(int(p[1]), int(p[2])) for p in node[1:] if isinstance(p, list) and p[0] == 'pt']

PINTYPE = {'PAS': 'passive', 'IN': 'input', 'INPUT': 'input', 'OUT': 'output', 'OUTPUT': 'output',
           'BI': 'bidirectional', 'BIDIRECTIONAL': 'bidirectional', 'PWR': 'power_in', 'POWER': 'power_in',
           'OC': 'open_collector', 'OE': 'open_emitter', 'TRI': 'tri_state', '3STATE': 'tri_state',
           'HIZ': 'tri_state', 'PASSIVE': 'passive'}

def q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'

def f(v):
    v = round(v, 4)
    return ('%.4f' % v).rstrip('0').rstrip('.') if v != int(v) else str(int(v))

def kpt(x, y):  # EDIF absolute -> KiCad schematic mm (Y down)
    return x * S, -y * S

FONT = '(effects (font (size 1.27 1.27)))'

# ---------- graphics ----------
def figures_to_lib(nodes, T=lambda x, y: (x, y)):
    """EDIF figure nodes (local Y-up coords) -> KiCad lib graphic items (Y-up mm)."""
    out = []
    for fig in nodes:
        filled = False
        fgo = first(fig, 'figureGroupOverride')
        for shape in fig[1:]:
            if not isinstance(shape, list):
                continue
            k = shape[0]
            if k == 'path':
                for pl in find(shape, 'pointList'):
                    p = pts(pl, T)
                    if len(p) >= 2:
                        out.append('(polyline (pts %s) (stroke (width 0) (type default)) (fill (type none)))' %
                                   ' '.join('(xy %s %s)' % (f(x * S), f(y * S)) for x, y in p))
            elif k == 'polygon':
                for pl in find(shape, 'pointList'):
                    p = pts(pl, T)
                    if len(p) >= 2:
                        if p[0] != p[-1]:
                            p.append(p[0])
                        out.append('(polyline (pts %s) (stroke (width 0) (type default)) (fill (type outline)))' %
                                   ' '.join('(xy %s %s)' % (f(x * S), f(y * S)) for x, y in p))
            elif k == 'rectangle':
                p = pts(shape, T)
                if len(p) == 2:
                    (x1, y1), (x2, y2) = p
                    out.append('(rectangle (start %s %s) (end %s %s) (stroke (width 0.254) (type default)) (fill (type none)))' %
                               (f(x1 * S), f(y1 * S), f(x2 * S), f(y2 * S)))
            elif k == 'circle':
                p = pts(shape, T)
                if len(p) == 2:
                    (x1, y1), (x2, y2) = p
                    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                    r = math.hypot(x2 - x1, y2 - y1) / 2
                    out.append('(circle (center %s %s) (radius %s) (stroke (width 0) (type default)) (fill (type none)))' %
                               (f(cx * S), f(cy * S), f(r * S)))
            elif k == 'openShape':
                for arc in walk(shape):
                    if arc[0] == 'arc':
                        p = pts(arc, T)
                        if len(p) == 3:
                            (a, b, c) = p
                            poly = arc_points(a, b, c)
                            out.append('(polyline (pts %s) (stroke (width 0) (type default)) (fill (type none)))' %
                                       ' '.join('(xy %s %s)' % (f(x * S), f(y * S)) for x, y in poly))
                for pl in find(shape, 'pointList'):
                    p = pts(pl, T)
                    if len(p) >= 2:
                        out.append('(polyline (pts %s) (stroke (width 0) (type default)) (fill (type none)))' %
                                   ' '.join('(xy %s %s)' % (f(x * S), f(y * S)) for x, y in p))
    return out

def arc_points(a, b, c, n=10):
    """3-point arc (start, mid, end) -> polyline points (avoids arc-format differences between KiCad versions)"""
    (x1, y1), (x2, y2), (x3, y3) = a, b, c
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if d == 0:
        return [a, c]
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    t1 = math.atan2(y1 - uy, x1 - ux); t2 = math.atan2(y2 - uy, x2 - ux); t3 = math.atan2(y3 - uy, x3 - ux)
    def ccw(t0, t):  # angle from t0 going counter-clockwise
        return (t - t0) % (2 * math.pi)
    sweep = ccw(t1, t3)
    if ccw(t1, t2) > sweep:  # mid not on ccw path -> go clockwise
        sweep = sweep - 2 * math.pi
    return [(ux + r * math.cos(t1 + sweep * i / n), uy + r * math.sin(t1 + sweep * i / n)) for i in range(n + 1)]

def annotate_texts(sym, T=lambda x, y: (x, y)):
    out = []
    for cg in find(sym, 'commentGraphics'):
        for an in find(cg, 'annotate'):
            sd = an[1]
            txt = sd[1].strip('"') if isinstance(sd, list) else str(sd).strip('"')
            org = None
            for x in walk(an):
                if x[0] == 'origin':
                    org = (int(x[1][1]), int(x[1][2]))
            if org and txt:
                org = T(*org)
                out.append('(text %s (at %s %s 0) (effects (font (size 1 1)) (justify left bottom)))' %
                           (q(txt), f(org[0] * S), f(org[1] * S)))
    return out

# ---------- main ----------
def main(edf_path, xml_path, out_path):
    e = parse(open(edf_path, encoding='latin-1').read())

    # cells
    cells = {}
    for lib in find(e, 'library'):
        ln = ident(lib[1])
        for c in find(lib, 'cell'):
            cn = ident(c[1]); cdisp = display_name(c[1])
            for v in find(c, 'view'):
                cells[(ln, cn, ident(v[1]))] = dict(cell=c, view=v, name=cdisp)

    design_view = None
    for k, cv in cells.items():
        cont = first(cv['view'], 'contents')
        if cont and first(cont, 'page'):
            design_view = cv['view']
    page = first(first(design_view, 'contents'), 'page')

    # ---- library symbols for parts ----
    libsyms = {}   # key -> dict(name, text, ports{portid:(x,y,number,name)})
    def build_part_symbol(key, inst_props, ori='R0'):
        T = ORI[ori]
        skey = (key, ori)
        if skey in libsyms:
            return libsyms[skey]
        cv = cells[key]; intf = first(cv['view'], 'interface'); sym = first(intf, 'symbol')
        portinfo = {}
        for p in find(intf, 'port'):
            pid = ident(p[1]); pr = props(p)
            des = first(p, 'designator')
            pname = pr.get('Name') or display_name(p[1])
            if pname and '\\' in pname:  # OrCAD overbar notation P\E\N\ -> KiCad ~{PEN}
                pname = '~{' + pname.replace('\\', '') + '}'
            num = (des[1].strip('"') if des else '') or (pr.get('PackagePortNumbers') or '') or pname or pid
            portinfo[pid] = dict(number=num, name=pname, type=PINTYPE.get((pr.get('Type') or 'PAS').upper(), 'passive'))
        pins = []; ports = {}
        for pi in find(sym, 'portImplementation'):
            pid = ident(pi[1])
            cl = first(pi, 'connectLocation')
            cpt = next(x for x in walk(cl) if x[0] == 'pt') if cl else None
            if not cpt:
                continue
            cx0, cy0 = int(cpt[1]), int(cpt[2])
            body0 = (cx0, cy0)
            for fig in find(pi, 'figure'):
                for pl in walk(fig):
                    if pl[0] == 'pointList':
                        p = pts(pl)
                        if len(p) >= 2:
                            body0 = max(p, key=lambda t: abs(t[0] - cx0) + abs(t[1] - cy0))
            cx, cy = T(cx0, cy0); body = T(*body0)
            dx, dy = body[0] - cx, body[1] - cy
            length = math.hypot(dx, dy) * S
            ang = 0 if length == 0 else int(round(math.degrees(math.atan2(dy, dx)))) % 360
            info = portinfo.get(pid, dict(number=pid, name=pid, type='passive'))
            ports[pid] = (cx0, cy0, info['number'], info['name'])
            pins.append('(pin %s line (at %s %s %d) (length %s) (name %s %s) (number %s %s))' %
                        (info['type'], f(cx * S), f(cy * S), ang, f(length), q(info['name']), FONT, q(info['number']), FONT))
        graphics = figures_to_lib(find(sym, 'figure'), T) + annotate_texts(sym, T)
        base = (re.sub(r'[^A-Za-z0-9_+\-.]', '_', cv['name']) or 'PART') + ('' if ori == 'R0' else '_' + ori)
        name = base; i = 2
        while name in {v['name'] for v in libsyms.values()}:
            name = '%s_%d' % (base, i); i += 1
        hide_names = (inst_props.get('Pin Names Visible') or 'True').lower() == 'false'
        hide_nums = (inst_props.get('Pin Numbers Visible') or 'True').lower() == 'false'
        head = '(symbol %s %s%s(pin_names (offset 0.254)%s) (in_bom yes) (on_board yes)' % (
            q('orcad:' + name), '', '(pin_numbers hide) ' if hide_nums else '', ' hide' if hide_names else '')
        text = '\n'.join([head,
                          '  (property "Reference" "U" (at 0 0 0) %s)' % FONT,
                          '  (property "Value" %s (at 0 0 0) %s)' % (q(name), FONT),
                          '  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
                          '  (symbol %s %s)' % (q(name + '_0_1'), ' '.join(graphics)),
                          '  (symbol %s %s)' % (q(name + '_1_1'), ' '.join(pins)),
                          ')'])
        libsyms[skey] = dict(name=name, text=text, ports=ports)
        return libsyms[skey]

    # ---- nets ----
    nets = []
    for n in find(page, 'net'):
        nd = n[1]
        nname = display_name(nd)
        alias_pts = []
        if isinstance(nd, list) and nd[0] == 'name':
            for dsp in find(nd, 'display'):
                org = first(dsp, 'origin')
                if org:
                    alias_pts.append((int(org[1][1]), int(org[1][2])))
        j = first(n, 'joined'); members = []
        for pr in find(j, 'portRef'):
            ir = first(pr, 'instanceRef')
            members.append((ident(pr[1]), ident(ir[1]) if ir else None))
        segs = []
        for fg in find(n, 'figure'):
            for pl in walk(fg):
                if pl[0] == 'pointList':
                    p = pts(pl)
                    segs += [s for s in zip(p, p[1:]) if s[0] != s[1]]
        auto = bool(re.match(r'^N\d+$', nname))
        nets.append(dict(name=nname, auto=auto, members=members, segs=segs, alias=alias_pts))

    # ---- part instances ----
    parts = []
    pinpos = {}  # (inst, portid) -> abs EDIF point
    for ins in find(page, 'instance'):
        iname = ident(ins[1]); vr = first(ins, 'viewRef'); cr = first(vr, 'cellRef'); lr = first(cr, 'libraryRef')
        key = (ident(lr[1]), ident(cr[1]), ident(vr[1]))
        des = first(ins, 'designator')
        if not des:
            continue  # border / title block
        sd = des[1]
        ref = dec((sd[1] if isinstance(sd, list) else sd).strip('"'))
        ref_org = text_disp(des)
        pr = props(ins)
        val_org = (None, 0, 'left bottom')
        for p in find(ins, 'property'):
            if (display_name(p[1]) if isinstance(p[1], list) else p[1]) == 'Value':
                val_org = text_disp(p)
        ori, o = transform(first(ins, 'transform'))
        ls = build_part_symbol(key, pr, ori)
        for pid, (x, y, num, nm) in ls['ports'].items():
            X, Y = ORI[ori](x, y)
            pinpos[(iname, pid)] = (X + o[0], Y + o[1])
        parts.append(dict(iname=iname, ref=ref, key=key, ori=ori, o=o, props=pr, ref_org=ref_org, val_org=val_org))

    # ---- page ports: power symbols & off-page connectors ----
    portpos = {}  # port id -> list of (abs pt, kind, cellkey, ori, origin)
    port_disp = {}
    intf_pg = first(design_view, 'interface')
    if intf_pg:
        for p in find(intf_pg, 'port'):
            port_disp[ident(p[1])] = display_name(p[1])
    for pi in find(page, 'portImplementation'):
        pid = ident(pi[1])
        ins = first(pi, 'instance')
        cl = first(pi, 'connectLocation')
        cpt = next(x for x in walk(cl) if x[0] == 'pt')
        abs_pt = (int(cpt[1]), int(cpt[2]))
        if ins:
            vr = first(ins, 'viewRef'); cr = first(vr, 'cellRef'); lr = first(cr, 'libraryRef')
            ckey = (ident(lr[1]), ident(cr[1]), ident(vr[1]))
            ori, o = transform(first(ins, 'transform'))
        else:
            ckey, ori, o = None, 'R0', abs_pt
        cname = cells[ckey]['name'] if ckey in cells else ''
        kind = 'offpage' if 'OFFPAGE' in cname.upper() else 'power'
        portpos.setdefault(pid, []).append(dict(pt=abs_pt, kind=kind, ckey=ckey, ori=ori, o=o))

    # net of each page port
    port_net = {}
    for ni, n in enumerate(nets):
        for pid, inst in n['members']:
            if inst is None:
                port_net[pid] = ni

    # ---- geometry: split segments, islands ----
    out_wires = []; labels = []; glabels = []; powers = []; junctions = set()
    power_lib = {}
    report = collections.Counter()

    def on_seg(p, s):
        (x1, y1), (x2, y2) = s
        x, y = p
        if x1 == x2 == x and min(y1, y2) < y < max(y1, y2):
            return True
        if y1 == y2 == y and min(x1, x2) < x < max(x1, x2):
            return True
        if x1 != x2 and y1 != y2:  # diagonal
            cr = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
            if cr == 0 and min(x1, x2) < x < max(x1, x2):
                return True
        return False

    # XML net aliases (OrCAD net labels), converted to EDIF coordinates
    xml_alias_by_net = collections.defaultdict(list); alias_labels = []
    seg_owner = {}
    for ni_, n_ in enumerate(nets):
        for sgm in n_['segs']:
            seg_owner[sgm] = ni_
    try:
        _xr = ET.parse(xml_path).getroot()
        _D = lambda el: (el.find('Defn').attrib if el.find('Defn') is not None else {})
        for ns_ in _xr.find('Schematic').find('Page').findall('NetScalar'):
            for w in ns_.findall('WireScalar'):
                wa = _D(w)
                w0 = (int(wa['startX']), -int(wa['startY'])); w1 = (int(wa['endX']), -int(wa['endY']))
                for al in w.findall('Alias'):
                    aa = _D(al)
                    p = nearest_on_seg((int(aa['locX']), -int(aa['locY'])), (w0, w1))
                    owner = None
                    for sgm, o_ in seg_owner.items():
                        if p == sgm[0] or p == sgm[1] or on_seg_g(p, sgm):
                            owner = o_; break
                    if owner is not None:
                        xml_alias_by_net[owner].append(dict(name=aa['name'], pt=p, ang=(int(aa.get('rotation', 0)) % 4) * 90))
    except Exception as ex:
        print('alias step skipped:', ex)

    for ni, n in enumerate(nets):
        pin_pts = [pinpos[(pid, inst)] if False else pinpos.get((inst, pid)) for pid, inst in n['members'] if inst]
        pin_pts = [p for p in pin_pts if p]
        port_pts = []
        for pid, inst in n['members']:
            if inst is None:
                for pp in portpos.get(pid, []):
                    port_pts.append((pp['pt'], pid, pp))
        key_pts = set(pin_pts) | {p for p, _, _ in port_pts} | {a for s in n['segs'] for a in s}
        segs = []
        for s in n['segs']:
            inner = sorted([p for p in key_pts if on_seg(p, s)],
                           key=lambda p: (p[0] - s[0][0]) ** 2 + (p[1] - s[0][1]) ** 2)
            chain = [s[0]] + inner + [s[1]]
            segs += [c for c in zip(chain, chain[1:]) if c[0] != c[1]]
        # dedupe
        segs = list({tuple(sorted(s)) for s in segs})
        # union-find islands
        parent = {}
        def find_(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]; a = parent[a]
            return a
        def union(a, b):
            parent[find_(a)] = find_(b)
        for a, b in segs:
            union(a, b)
        for p in pin_pts:
            find_(p)
        for p, _, _ in port_pts:
            find_(p)
        islands = collections.defaultdict(set)
        for p in list(parent):
            islands[find_(p)].add(p)
        # degree for junctions
        deg = collections.Counter()
        for a, b in segs:
            deg[a] += 1; deg[b] += 1
        for p, d in deg.items():
            if d >= 3:
                junctions.add(p)
        for a, b in segs:
            out_wires.append((a, b))
        # ---- name carriers per island ----
        inames = collections.defaultdict(set)
        for p, pid, pp in port_pts:
            nm_ = port_disp.get(pid, n['name']) if pp['kind'] == 'power' else port_disp.get(pid, n['name'])
            inames[find_(p)].add(nm_)
        for al in xml_alias_by_net.get(ni, []):
            r_ = None
            for sgm in segs:
                if al['pt'] == sgm[0] or al['pt'] == sgm[1] or on_seg(al['pt'], sgm):
                    r_ = find_(sgm[0]); break
            if r_ is None:
                continue
            inames[r_].add(al['name'])
            alias_labels.append((al['name'], al['pt'], al['ang']))
            report['labels_alias'] += 1
        roots = list(islands)
        if len(roots) > 1:
            # group islands that share a name
            grp = {r_: r_ for r_ in roots}
            def g(x):
                while grp[x] != x:
                    x = grp[x]
                return x
            byname = collections.defaultdict(list)
            for r_ in roots:
                for nm_ in inames.get(r_, ()):
                    byname[nm_].append(r_)
            for nm_, rs in byname.items():
                for r2 in rs[1:]:
                    grp[g(r2)] = g(rs[0])
            groups = collections.defaultdict(list)
            for r_ in roots:
                groups[g(r_)].append(r_)
            if len(groups) > 1:
                kind = 'global' if port_pts else 'local'
                for gr in groups.values():
                    r_ = gr[0]
                    anchor, ang = fallback_anchor(islands[r_], segs, set(pin_pts))
                    labels.append((n['name'], anchor, kind, ang))
                    report['labels_fallback_' + kind] += 1

    # power symbols and offpage global labels
    flag_nets = {}
    for pid, lst in portpos.items():
        ni = port_net.get(pid)
        netname = nets[ni]['name'] if ni is not None else pid
        shown = port_disp.get(pid, netname)
        for pp in lst:
            if pp['kind'] == 'offpage':
                ang = 0
                cv = cells.get(pp['ckey'])
                cont = first(cv['view'], 'contents') if cv else None
                if cont:
                    gpts = [ORI[pp['ori']](*t) for x_ in walk(cont) if x_[0] == 'pt' for t in [(int(x_[1]), int(x_[2]))]]
                    if gpts:
                        gx = sum(t[0] for t in gpts) / len(gpts) + pp['o'][0]; gy = sum(t[1] for t in gpts) / len(gpts) + pp['o'][1]
                        dx, dy = gx - pp['pt'][0], gy - pp['pt'][1]
                        ang = (0 if dx >= 0 else 180) if abs(dx) >= abs(dy) else (90 if dy > 0 else 270)
                glabels.append((shown, pp['pt'], ang))
            else:
                ck = pp['ckey']
                lkey = (ck, shown, pp['ori'])
                if lkey not in power_lib:
                    cv = cells.get(ck)
                    graphics = []
                    if cv:
                        cont = first(cv['view'], 'contents')
                        if cont:
                            graphics = figures_to_lib(find(cont, 'figure'), ORI[pp['ori']])
                    lx, ly = pp['pt'][0] - pp['o'][0], pp['pt'][1] - pp['o'][1]
                    base = 'PWR_' + re.sub(r'[^A-Za-z0-9_+\-.]', '_', (cv['name'] if cv else 'P') + '_' + shown) + ('' if pp['ori'] == 'R0' else '_' + pp['ori'])
                    text = '\n'.join([
                        '(symbol %s (power) (pin_names (offset 0) hide) (in_bom yes) (on_board yes)' % q('orcad:' + base),
                        '  (property "Reference" "#PWR" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
                        '  (property "Value" %s (at 0 0 0) %s)' % (q(shown), FONT),
                        '  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
                        '  (symbol %s %s)' % (q(base + '_0_1'), ' '.join(graphics)),
                        '  (symbol %s (pin power_in line (at %s %s 0) (length 0) hide (name %s %s) (number "1" %s)))' % (
                            q(base + '_1_1'), f(lx * S), f(ly * S), q(shown), FONT, FONT),
                        ')'])
                    xs = re.findall(r'\(xy ([-\d.]+) ([-\d.]+)\)|\(center ([-\d.]+) ([-\d.]+)\)', ' '.join(graphics))
                    cxs = [float(a or c) for a, b, c, d in xs]; cys = [float(b or d) for a, b, c, d in xs]
                    power_lib[lkey] = dict(name=base, text=text,
                                           gc=((min(cxs) + max(cxs)) / 2, (min(cys) + max(cys)) / 2) if cxs else (lx * S, ly * S),
                                           ext=(max(cxs) - min(cxs), max(cys) - min(cys)) if cxs else (0, 0),
                                           pin=(lx * S, ly * S))
                powers.append(dict(lib=power_lib[lkey]['name'], lk=lkey, net=shown, ori=pp['ori'], o=pp['o']))
                if ni not in flag_nets:
                    flag_nets[ni] = pp['pt']

    # ---- XML: no-connects and notes ----
    connected_pins = set()
    for n_ in nets:
        mem = [(inst, pid) for pid, inst in n_['members']]
        if len(mem) >= 2 or n_['segs']:
            for pid, inst in n_['members']:
                if inst:
                    connected_pins.add((inst, pid))
    nc_pts = []; notes = []
    try:
        xr = ET.parse(xml_path).getroot()
        D = lambda el: (el.find('Defn').attrib if el.find('Defn') is not None else {})
        xpage = xr.find('Schematic').find('Page')
        pkgs = {D(p)['name']: p for p in xr.find('Cache').findall('Package')}
        inst_by_ref = {p['ref']: p for p in parts}
        for pi_ in xpage.findall('PartInst'):
            ref = D(pi_.find('Reference')).get('name')
            p = inst_by_ref.get(ref)
            if not p:
                continue
            pk = pkgs.get(D(pi_)['pkgName'])
            numbers = {}
            if pk is not None:
                pp = pk.find('PhysicalPart')
                if pp is not None:
                    for pn in pp.findall('PinNumber'):
                        numbers[D(pn)['position']] = D(pn)['number']
            ports = libsyms[(p['key'], p['ori'])]['ports']
            by_num = {v[2]: pid for pid, v in ports.items()}
            nm_cnt = collections.Counter(v[3] for v in ports.values())
            by_name = {v[3]: pid for pid, v in ports.items() if nm_cnt[v[3]] == 1}
            for port in pi_.findall('PortInstScalar'):
                a = D(port)
                if D(port.find('IsNoConnect')).get('val') == '1':
                    num = numbers.get(a['position'])
                    pid = by_num.get(num) if num else None
                    if pid is None:
                        xn = a['name']
                        if '\\' in xn:
                            xn = '~{' + xn.replace('\\', '') + '}'
                        pid = by_name.get(xn) or by_num.get(xn)
                    if pid is None:
                        print('no-connect not mapped:', ref, a['name'])
                    # OrCAD keeps the NC flag on pins that were wired later (and hides the X);
                    # only mark pins that are really unconnected in the EDIF netlist
                    if pid and (p['iname'], pid) in pinpos and (p['iname'], pid) not in connected_pins:
                        nc_pts.append(pinpos[(p['iname'], pid)])
                    elif pid:
                        report['nc_skipped_connected'] += 1
        for g in xpage.findall('GraphicCommentTextInst'):
            a = D(g)
            txt = a.get('name', '').replace('\r', '\n')
            notes.append((txt, int(a['locX']), int(a['locY'])))
    except Exception as ex:
        print('XML step skipped:', ex)

    # ---- emit ----
    root = U()
    proj = re.sub(r'\.kicad_sch$', '', out_path.split('/')[-1])
    L = []
    L.append('(kicad_sch (version 20230121) (generator orcad_edif2kicad)')
    L.append('  (uuid %s)' % root)
    L.append('  (paper "A1")')
    L.append('  (lib_symbols')
    for v in libsyms.values():
        L.append(v['text'])
    for v in power_lib.values():
        L.append(v['text'])
    if flag_nets:
        L.append(PWR_FLAG_LIB)
    L.append('  )')
    for p in sorted(junctions):
        x, y = kpt(*p)
        L.append('  (junction (at %s %s) (diameter 0) (color 0 0 0 0) (uuid %s))' % (f(x), f(y), U()))
    for p in nc_pts:
        x, y = kpt(*p)
        L.append('  (no_connect (at %s %s) (uuid %s))' % (f(x), f(y), U()))
    for a, b in out_wires:
        x1, y1 = kpt(*a); x2, y2 = kpt(*b)
        L.append('  (wire (pts (xy %s %s) (xy %s %s)) (stroke (width 0) (type default)) (uuid %s))' % (f(x1), f(y1), f(x2), f(y2), U()))
    def jl(ang):
        return 'left' if ang in (0, 90) else 'right'
    for name, p, kind, ang in labels:
        x, y = kpt(*p)
        if kind == 'global':
            L.append('  (global_label %s (shape passive) (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify %s)) (uuid %s))' % (q(name), f(x), f(y), ang, jl(ang), U()))
        else:
            L.append('  (label %s (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify %s bottom)) (uuid %s))' % (q(name), f(x), f(y), ang, jl(ang), U()))
    global_names = {g[0] for g in glabels} | {pw['net'] for pw in powers}
    for name, p, ang in alias_labels:
        x, y = kpt(*p)
        if name in global_names:
            L.append('  (global_label %s (shape passive) (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify %s)) (uuid %s))' % (q(name), f(x), f(y), ang, jl(ang), U()))
            continue
        L.append('  (label %s (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify %s bottom)) (uuid %s))' % (q(name), f(x), f(y), ang, jl(ang), U()))
    for name, p, ang in glabels:
        x, y = kpt(*p)
        L.append('  (global_label %s (shape passive) (at %s %s %d) (fields_autoplaced) (effects (font (size 1.27 1.27)) (justify %s)) (uuid %s))' % (q(name), f(x), f(y), ang, jl(ang), U()))
    for txt, x, y in notes:
        L.append('  (text %s (at %s %s 0) (effects (font (size 2 2)) (justify left bottom)) (uuid %s))' % (q(txt), f(x * S), f(y * S), U()))
    n_pwr = 0
    for pw in powers:
        n_pwr += 1
        ang, mir = 0, None
        x, y = kpt(*pw['o'])
        pl = power_lib[pw['lk']]
        gx, gy = pl['gc']; px_, py_ = pl['pin']
        vx_, vy_ = gx - px_, gy - py_
        nrm = math.hypot(vx_, vy_) or 1
        off = max(pl['ext']) / 2 + 1.6
        tx, ty = gx + vx_ / nrm * off, gy + vy_ / nrm * off   # lib coords, Y up
        val_at = (x + tx, y - ty)
        ref = '#PWR%04d' % n_pwr
        L.append('  (symbol (lib_id %s) (at %s %s %d)%s (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid %s)' % (
            q('orcad:' + pw['lib']), f(x), f(y), ang, ' (mirror %s)' % mir if mir else '', U()))
        L.append('    (property "Reference" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (q(ref), f(x), f(y)))
        L.append('    (property "Value" %s (at %s %s 0) %s)' % (q(pw['net']), f(val_at[0]), f(val_at[1]), FONT))
        L.append('    (property "Footprint" "" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (f(x), f(y)))
        L.append('    (pin "1" (uuid %s))' % U())
        L.append('    (instances (project %s (path "/%s" (reference %s) (unit 1)))))' % (q(proj), root, q(ref)))
    for i_, (ni_, pt_) in enumerate(sorted(flag_nets.items(), key=lambda t: str(t))):
        x, y = kpt(*pt_)
        ref = '#FLG%04d' % (i_ + 1)
        L.append('  (symbol (lib_id "orcad:PWR_FLAG") (at %s %s 0) (unit 1) (in_bom yes) (on_board yes) (dnp no) (uuid %s)' % (f(x), f(y), U()))
        L.append('    (property "Reference" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (q(ref), f(x), f(y)))
        L.append('    (property "Value" "PWR_FLAG" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (f(x), f(y)))
        L.append('    (property "Footprint" "" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (f(x), f(y)))
        L.append('    (pin "1" (uuid %s))' % U())
        L.append('    (instances (project %s (path "/%s" (reference %s) (unit 1)))))' % (q(proj), root, q(ref)))
    for p in parts:
        ls = libsyms[(p['key'], p['ori'])]
        ang, mir = 0, None
        x, y = kpt(*p['o'])
        val = p['props'].get('Value') or ''
        fp = p['props'].get('PCB Footprint') or ''
        dnp = 'OPEN' in val.upper()
        (rp, ra, rj), (vp, va, vj) = p['ref_org'], p['val_org']
        rx, ry = kpt(*(rp or p['o']))
        vx, vy = kpt(*(vp or p['o']))
        rj = (' (justify %s)' % rj) if rj else ''
        vj = (' (justify %s)' % vj) if vj else ''
        L.append('  (symbol (lib_id %s) (at %s %s %d)%s (unit 1) (in_bom yes) (on_board yes) (dnp %s) (uuid %s)' % (
            q('orcad:' + ls['name']), f(x), f(y), ang, ' (mirror %s)' % mir if mir else '', 'yes' if dnp else 'no', U()))
        L.append('    (property "Reference" %s (at %s %s %d) (effects (font (size 1.27 1.27))%s))' % (q(p['ref']), f(rx), f(ry), ra, rj))
        L.append('    (property "Value" %s (at %s %s %d) (effects (font (size 1.27 1.27))%s%s))' % (q(val), f(vx), f(vy), va, vj, '' if vp else ' hide'))
        L.append('    (property "Footprint" "" (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (f(x), f(y)))
        L.append('    (property "OrCAD_Footprint" %s (at %s %s 0) (effects (font (size 1.27 1.27)) hide))' % (q(fp), f(x), f(y)))
        for pid, (_, _, num, _) in ls['ports'].items():
            L.append('    (pin %s (uuid %s))' % (q(num), U()))
        L.append('    (instances (project %s (path "/%s" (reference %s) (unit 1)))))' % (q(proj), root, q(p['ref'])))
    L.append('  (sheet_instances (path "/" (page "1")))')
    L.append(')')
    open(out_path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')

    import os
    outdir = os.path.dirname(os.path.abspath(out_path))
    libtexts = [v['text'] for v in libsyms.values()] + [v['text'] for v in power_lib.values()] + ([PWR_FLAG_LIB] if flag_nets else [])
    with open(os.path.join(outdir, 'orcad.kicad_sym'), 'w', encoding='utf-8') as fh:
        fh.write('(kicad_symbol_lib (version 20220914) (generator orcad_edif2kicad)\n')
        for t in libtexts:
            fh.write(t.replace('(symbol "orcad:', '(symbol "', 1) + '\n')
        fh.write(')\n')
    with open(os.path.join(outdir, 'sym-lib-table'), 'w', encoding='utf-8') as fh:
        fh.write('(sym_lib_table\n  (lib (name "orcad")(type "KiCad")(uri "${KIPRJMOD}/orcad.kicad_sym")(options "")(descr "OrCAD EDIF 변환 심볼"))\n)\n')

    # reference netlist from EDIF (ref, pin number) groups
    refnet = {}
    inst_ref = {p['iname']: p['ref'] for p in parts}
    for n in nets:
        grp = []
        for pid, inst in n['members']:
            if inst and inst in inst_ref:
                grp.append([inst_ref[inst], libsyms[[(p['key'], p['ori']) for p in parts if p['iname'] == inst][0]]['ports'][pid][2]])
        refnet[n['name']] = grp
    json.dump(refnet, open(out_path.replace('.kicad_sch', '.edif_netlist.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('parts', len(parts), 'lib symbols', len(libsyms), 'power', len(powers), 'offpage', len(glabels),
          'wires', len(out_wires), 'pwr_flags', len(flag_nets), 'junctions', len(junctions), 'noconnect', len(nc_pts), 'notes', len(notes), dict(report))

PWR_FLAG_LIB = '''(symbol "orcad:PWR_FLAG" (power) (pin_numbers hide) (pin_names (offset 0) hide) (in_bom yes) (on_board yes)
  (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) hide))
  (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
  (symbol "PWR_FLAG_0_0" (pin power_out line (at 0 0 90) (length 0) (name "pwr" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27))))))
  (symbol "PWR_FLAG_0_1" (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27)) (stroke (width 0) (type default)) (fill (type none))))
)'''

def on_seg_g(p, s):
    (x1, y1), (x2, y2) = s
    x, y = p
    if x1 == x2 == x and min(y1, y2) <= y <= max(y1, y2):
        return True
    if y1 == y2 == y and min(x1, x2) <= x <= max(x1, x2):
        return True
    return False

def fallback_anchor(members, segs, pinset):
    """pick a dangling wire end (or any point) and the label direction pointing away from the wire"""
    deg = collections.Counter(); nb = {}
    for a, b in segs:
        if a in members:
            deg[a] += 1; deg[b] += 1; nb[a] = b; nb[b] = a
    ends = sorted([p for p in members if deg[p] == 1 and p not in pinset]) or sorted([p for p in members if deg[p] == 1]) or sorted(members)
    p = ends[0]
    if p in nb:
        dx, dy = p[0] - nb[p][0], p[1] - nb[p][1]   # EDIF Y-up
        ang = 0 if dx > 0 else 180 if dx < 0 else 90 if dy > 0 else 270
    else:
        ang = 0
    return p, ang

def nearest_on_seg(a, s):
    (x1, y1), (x2, y2) = s
    if x1 == x2:
        return (x1, min(max(a[1], min(y1, y2)), max(y1, y2)))
    if y1 == y2:
        return (min(max(a[0], min(x1, x2)), max(x1, x2)), y1)
    return s[0]

if __name__ == '__main__':
    main(*sys.argv[1:4])
