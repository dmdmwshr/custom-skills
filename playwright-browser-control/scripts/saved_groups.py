"""Read-only Chromium saved-group metadata. Never opens or writes the database."""
import json,struct,hashlib,sys
from pathlib import Path
def var(b,p):
 n=0;shift=0
 while p<len(b) and shift<=63:
  x=b[p];p+=1;n|=(x&127)<<shift
  if x<128:return n,p
  shift+=7
 raise ValueError('incomplete_varint')
def sized(b,p):
 n,p=var(b,p)
 if p+n>len(b):raise ValueError('incomplete_length')
 return b[p:p+n],p+n
def snappy(b):
 expected,p=var(b,0);out=bytearray()
 if expected>32*1024*1024:raise ValueError('block_too_large')
 while p<len(b):
  tag=b[p];p+=1;t=tag&3
  if t==0:
   n=tag>>2
   if n>=60:
    w=n-59;n=int.from_bytes(b[p:p+w],'little');p+=w
   n+=1;out.extend(b[p:p+n]);p+=n
  else:
   if t==1:n=4+((tag>>2)&7);off=((tag&224)<<3)|b[p];p+=1
   else:
    n=1+(tag>>2);w=2 if t==2 else 4;off=int.from_bytes(b[p:p+w],'little');p+=w
   if not 0<off<=len(out):raise ValueError('invalid_copy')
   for _ in range(n):out.append(out[-off])
 if len(out)!=expected:raise ValueError('snappy_size')
 return bytes(out)
def block_rows(b):
 n=int.from_bytes(b[-4:],'little');end=len(b)-4*(n+1);p=0;prev=b''
 if not 0<=end<=len(b):raise ValueError('invalid_restart')
 while p<end:
  a,p=var(b,p);c,p=var(b,p);v,p=var(b,p)
  if a>len(prev) or p+c+v>end:raise ValueError('invalid_block')
  key=prev[:a]+b[p:p+c];p+=c
  value=b[p:p+v];p+=v;prev=key
  yield key,value
def table_rows(f):
 b=f.read_bytes()
 if b[-8:]!=struct.pack('<Q',0xdb4775248b80fb57):raise ValueError('invalid_footer')
 p=len(b)-48
 _,p=var(b,p);_,p=var(b,p);io,p=var(b,p);iz,p=var(b,p)
 def read(o,z):
  chunk=b[o:o+z];kind=b[o+z]
  if kind==0:return chunk
  if kind==1:return snappy(chunk)
  raise ValueError('unsupported_compression')
 for _,v in block_rows(read(io,iz)):
  o,p=var(v,0);z,p=var(v,p)
  for key,value in block_rows(read(o,z)):
   if len(key)<8:raise ValueError('internal_key_short')
   tag=int.from_bytes(key[-8:],'little')
   yield key[:-8],tag>>8,tag&255,value
def records(f):
 b=f.read_bytes();p=0;parts=[]
 while p+7<=len(b):
  remain=32768-p%32768
  if remain<7:p+=remain;continue
  n=int.from_bytes(b[p+4:p+6],'little');kind=b[p+6];p+=7
  if n>32768-(p%32768) and p%32768:raise ValueError('bad_log_length')
  if p+n>len(b):break
  data=b[p:p+n];p+=n
  if kind==0:continue
  if kind==1:yield data
  elif kind==2:parts=[data]
  elif kind==3:parts.append(data)
  elif kind==4:parts.append(data);yield b''.join(parts);parts=[]
  else:raise ValueError('bad_log_type')
def active_files(root):
 current=(root/'CURRENT').read_text().strip();manifest=root/current
 files={};logs=set()
 for b in records(manifest):
  p=0
  while p<len(b):
   tag,p=var(b,p)
   if tag==1:_,p=sized(b,p)
   elif tag in (2,3,4,9):
    n,p=var(b,p)
    if tag==2:log=n
    if tag==9:prevlog=n
   elif tag==5:_,p=var(b,p);_,p=sized(b,p)
   elif tag==6:_,p=var(b,p);n,p=var(b,p);files.pop(n,None)
   elif tag==7:
    level,p=var(b,p);n,p=var(b,p);_,p=var(b,p);_,p=sized(b,p);_,p=sized(b,p);files[n]=level
   else:raise ValueError('unsupported_manifest_tag')
 return current,files,{x for x in [locals().get('log',0),locals().get('prevlog',0)] if x}
def batch_rows(root,logs):
 for n in logs:
  f=root/f'{n:06d}.log'
  if not f.exists():continue
  for b in records(f):
   if len(b)<12:raise ValueError('batch_short')
   seq,count=struct.unpack('<QI',b[:12]);p=12
   for i in range(count):
    kind=b[p];p+=1;key,p=sized(b,p);value=b''
    if kind==1:value,p=sized(b,p)
    elif kind!=0:raise ValueError('batch_tag')
    yield key,seq+i,kind,value
def proto(b,allowed=None):
 p=0;d={}
 while p<len(b):
  tag,p=var(b,p);n=tag>>3;t=tag&7
  if t==0:v,p=var(b,p)
  elif t==2:v,p=sized(b,p)
  elif t in (1,5):
   w=8 if t==1 else 4;v=b[p:p+w];p+=w
  else:raise ValueError('proto_wire')
  if allowed is None or n in allowed:d[n]=v
 return d
def fingerprint(v):
 return hashlib.sha256(v if isinstance(v,bytes) else str(v).encode()).hexdigest()[:16] if v else None
def text(b):
 return b.decode('utf-8') if isinstance(b,bytes) else None
def snapshot(root):
 current,files,logs=active_files(root);latest={}
 def ingest(rows):
  for key,seq,kind,value in rows:
   if not key.startswith(b'saved_tab_group-dt-'):continue
   if key not in latest or seq>latest[key][0]:latest[key]=(seq,kind,value)
 for n in files:
  f=root/f'{n:06d}.ldb'
  if not f.exists():f=root/f'{n:06d}.sst'
  ingest(table_rows(f))
 ingest(batch_rows(root,logs))
 if (root/'CURRENT').read_text().strip()!=current:raise ValueError('manifest_changed')
 groups=[];tab_counts={}
 for key,(seq,kind,v) in latest.items():
  if kind!=1:continue
  data=proto(v,{2,3})
  specifics=proto(data.get(2,b''),{1,4,5,6})
  guid=specifics.get(1)
  if 4 in specifics:
   g=proto(specifics[4],{2})
   title=text(g.get(2,b''))
   if not title:continue
   attribution=proto(specifics.get(6,b''),{1})
   created=proto(attribution.get(1,b''),{1})
   device=proto(created.get(1,b''),{1})
   local=proto(data.get(3,b''),{1,7,8})
   groups.append({'title':title,'id':text(guid),'creatorHash':fingerprint(device.get(1)),
                  'hasLocalGroupId':bool(local.get(1)),'hidden':bool(local.get(7)),
                  'archived':bool(local.get(8))})
  elif 5 in specifics:
   tab=proto(specifics[5],{1});h=text(tab.get(1));tab_counts[h]=tab_counts.get(h,0)+1
 for g in groups:g['savedTabs']=tab_counts.get(g['id'],0)
 return {g['id']: g for g in groups}
