import pickle,sys
ins,calls,callers=pickle.load(open('cg2.pkl','rb'))
for x in sys.argv[1:]:
    a=int(x,16); print(x,'<-',[hex(c) for c in callers.get(a,[])])
