import re
def parse(text):
    tok=re.compile(r'\(|\)|"[^"]*"|[^\s()]+')
    stack=[[]]
    for m in tok.finditer(text):
        t=m.group()
        if t=='(': stack.append([])
        elif t==')':
            l=stack.pop(); stack[-1].append(l)
        else: stack[-1].append(t)
    return stack[0][0]
def find(l,key): return [x for x in l if isinstance(x,list) and x and x[0]==key]
def first(l,key):
    r=find(l,key); return r[0] if r else None
def walk(l):
    yield l
    for x in l:
        if isinstance(x,list): yield from walk(x)
