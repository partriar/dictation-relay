_c=["user's old clipboard"]; HIST=[]
def copy(t): _c[0]=t; HIST.append(t)
def paste(): return _c[0]
