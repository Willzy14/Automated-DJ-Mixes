"""Index every project folder name under the work folders and the STUDIO-2 backup drives (G: 2013-2024, F: 2025) into Tools/folder_index.json (~25 s). Gitignored."""
import os,json,time
t=time.time()
out=[]
roots=[]
for base in [r"G:\Stereo Masters",r"G:\Stem Masters",r"F:\Stereo Masters",r"F:\Stem Masters"]:
    for y in os.listdir(base): roots.append((base,os.path.join(base,y)))
B=r"C:\Users\Carillon\Wired Masters Dropbox\Sam Wills"
for r in ['1. Stereo Masters','2.1. Finished Stem Mixes','2. Ongoing Stem Mixes']: roots.append((B,os.path.join(B,r)))
for base,p in roots:
    try:
        for name in os.listdir(p):
            if name.endswith(' Project') or ' - ' in name:
                out.append((name,os.path.join(p,name)))
    except Exception as e: print('ERR',p,e)
json.dump(out,open(os.path.join(os.path.dirname(__file__),'folder_index.json'),'w'))
print(len(out),'folders indexed in',round(time.time()-t),'s')
