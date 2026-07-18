#!/usr/bin/env python3
"""Speciedex multi-source, append-only taxonomic ingestion system."""
from __future__ import annotations
import argparse, hashlib, json, logging, os, random, sqlite3, sys, tempfile, time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NAME='Speciedex Stat Grabber'; VERSION='2.0.0'; SCHEMA=1
LOG=logging.getLogger('speciedex.stat_grabber')
ACTIVE={'accepted','valid','provisionally accepted','unknown','reference'}
RANKS={'species':'species','genera':'genus','families':'family','orders':'order','classes':'class','phyla':'phylum','kingdoms':'kingdom'}

def now()->str:return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def nspace(v:Any)->str:return ' '.join(str(v or '').strip().split())
def nkey(v:Any)->str:return nspace(v).casefold()
def sint(v:Any,d:int=0)->int:
    try:x=int(v)
    except (TypeError,ValueError):return d
    return x if x>=0 else d

def read_json(path:Path,default:Any)->Any:
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):return default

def write_json(path:Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True); payload=json.dumps(value,ensure_ascii=False,indent=2)+'\n'; tmp=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',newline='\n',dir=path.parent,prefix='.'+path.name+'.',suffix='.tmp',delete=False) as f:
            f.write(payload); f.flush(); os.fsync(f.fileno()); tmp=Path(f.name)
        tmp.replace(path)
    finally:
        if tmp and tmp.exists():tmp.unlink(missing_ok=True)

def append_jsonl(path:Path,values:Iterable[dict[str,Any]])->int:
    path.parent.mkdir(parents=True,exist_ok=True); count=0
    with path.open('a',encoding='utf-8',newline='\n') as f:
        for value in values:f.write(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n');count+=1
        f.flush();os.fsync(f.fileno())
    return count

def file_hash(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

class HTTPFailure(RuntimeError):pass
@dataclass
class HTTP:
    timeout:int=30;retries:int=4;backoff:float=2.0;user_agent:str=f'Speciedex/{VERSION}';requests:int=0
    def get(self,url:str,params:dict[str,Any]|None=None,headers:dict[str,str]|None=None)->Any:
        if params:
            q=urlencode({k:v for k,v in params.items() if v is not None},doseq=True);url+=('&' if '?' in url else '?')+q
        hdr={'Accept':'application/json','User-Agent':self.user_agent};hdr.update(headers or {});req=Request(url,headers=hdr,method='GET');last=None
        for attempt in range(1,self.retries+1):
            try:
                self.requests+=1
                with urlopen(req,timeout=self.timeout) as r:
                    if not 200<=getattr(r,'status',200)<300:raise HTTPFailure(f'HTTP {r.status}: {url}')
                    return json.loads(r.read().decode(r.headers.get_content_charset() or 'utf-8'))
            except (HTTPError,URLError,TimeoutError,OSError,json.JSONDecodeError,HTTPFailure) as e:
                last=e
                if attempt>=self.retries:break
                delay=self.backoff**(attempt-1)+random.uniform(0,.5);time.sleep(delay)
        raise HTTPFailure(f'{url}: {last}')

@dataclass
class Taxon:
    provider:str;provider_id:str;scientific_name:str;canonical_name:str;rank:str;status:str='unknown';authorship:str='';kingdom:str='';phylum:str='';class_name:str='';order:str='';family:str='';genus:str='';accepted_provider_id:str='';source_url:str='';source_modified:str='';retrieved_at:str='';synonyms:list[str]=field(default_factory=list);extra:dict[str,Any]=field(default_factory=dict)
    def dict(self)->dict[str,Any]:
        d=asdict(self);d['class']=d.pop('class_name');return d
@dataclass
class Batch:
    records:list[Taxon];next_cursor:str|None;exhausted:bool;requests:int=0;raw:int=0

class Provider:
    def __init__(self,d:dict[str,Any],http:HTTP,state:Path,batch:int):self.d=d;self.http=http;self.state_path=state;self.state=read_json(state,{});self.batch=batch;self.name=str(d['name'])
    @property
    def cursor(self)->str|None:
        v=self.state.get('cursor');return str(v) if v not in (None,'') else None
    def fetch(self)->Batch:raise NotImplementedError
    def success(self,b:Batch)->None:
        self.state.update({'provider':self.name,'cursor':b.next_cursor,'bootstrap_complete':b.exhausted,'last_success':now(),'last_error':None,'last_batch_records':len(b.records),'last_requests':b.requests});write_json(self.state_path,self.state)
    def failure(self,e:Exception)->None:self.state.update({'provider':self.name,'last_attempt':now(),'last_error':str(e)});write_json(self.state_path,self.state)

class GBIF(Provider):
    def fetch(self)->Batch:
        base=self.d.get('base_url','https://api.gbif.org/v1').rstrip('/');off=sint(self.cursor,0);limit=min(self.batch,1000);p=self.http.get(base+'/species/search',{'limit':limit,'offset':off});rows=p.get('results',[]) if isinstance(p,dict) else []
        out=[]
        for x in rows:
            if not isinstance(x,dict) or x.get('key') is None:continue
            name=nspace(x.get('scientificName') or x.get('canonicalName'))
            if not name:continue
            out.append(Taxon(self.name,str(x['key']),name,nspace(x.get('canonicalName') or name),nspace(x.get('rank')).lower(),nspace(x.get('taxonomicStatus')).lower() or 'unknown',nspace(x.get('authorship')),nspace(x.get('kingdom')),nspace(x.get('phylum')),nspace(x.get('class')),nspace(x.get('order')),nspace(x.get('family')),nspace(x.get('genus')),nspace(x.get('acceptedKey')),f'{base}/species/{x["key"]}','',now(),[],{'nub_key':x.get('nubKey'),'name_type':x.get('nameType')}))
        end=bool(p.get('endOfRecords')) if isinstance(p,dict) else len(rows)<limit
        return Batch(out,None if end else str(off+limit),end,1,len(rows))

class WoRMS(Provider):
    def fetch(self)->Batch:
        base=self.d.get('base_url','https://www.marinespecies.org/rest').rstrip('/');page=sint(self.cursor,1);p=self.http.get(base+'/AphiaRecordsByDate',{'startdate':self.d.get('start_date','0001-01-01T00:00:00'),'enddate':self.d.get('end_date','9999-12-31T23:59:59'),'marine_only':'false','offset':page});rows=p if isinstance(p,list) else [];out=[]
        for x in rows[:self.batch]:
            if not isinstance(x,dict) or x.get('AphiaID') is None:continue
            name=nspace(x.get('scientificname'))
            if not name:continue
            out.append(Taxon(self.name,str(x['AphiaID']),name,name,nspace(x.get('rank')).lower(),nspace(x.get('status')).lower() or 'unknown',nspace(x.get('authority')),nspace(x.get('kingdom')),nspace(x.get('phylum')),nspace(x.get('class')),nspace(x.get('order')),nspace(x.get('family')),nspace(x.get('genus')),nspace(x.get('valid_AphiaID')),nspace(x.get('url')),nspace(x.get('modified')),now(),[],{'isMarine':x.get('isMarine')}))
        end=len(rows)==0;return Batch(out,None if end else str(page+1),end,1,len(rows))

class Wikispecies(Provider):
    def fetch(self)->Batch:
        api=self.d.get('api_url','https://species.wikimedia.org/w/api.php');params={'action':'query','format':'json','formatversion':2,'generator':'allpages','gapnamespace':0,'gaplimit':min(self.batch,500),'gapfilterredir':'nonredirects','prop':'info|pageprops'}
        if self.cursor:params['gapcontinue']=self.cursor
        p=self.http.get(api,params);pages=((p.get('query') or {}).get('pages') or []) if isinstance(p,dict) else [];out=[]
        for x in pages:
            if not isinstance(x,dict) or x.get('pageid') is None:continue
            title=nspace(x.get('title'))
            if not title or ':' in title:continue
            out.append(Taxon(self.name,str(x['pageid']),title,title,'unknown','reference',source_url='https://species.wikimedia.org/wiki/'+title.replace(' ','_'),retrieved_at=now(),extra={'reference_only':True}))
        c=(p.get('continue') or {}).get('gapcontinue') if isinstance(p,dict) else None
        return Batch(out,str(c) if c else None,c is None,1,len(pages))

class INaturalist(Provider):
    def fetch(self)->Batch:
        base=self.d.get('base_url','https://api.inaturalist.org/v1').rstrip('/');page=sint(self.cursor,1);limit=min(self.batch,200);p=self.http.get(base+'/taxa',{'page':page,'per_page':limit,'order':'asc','order_by':'id','is_active':'true'});rows=p.get('results',[]) if isinstance(p,dict) else [];out=[]
        for x in rows:
            if not isinstance(x,dict) or x.get('id') is None:continue
            name=nspace(x.get('name'))
            if not name:continue
            out.append(Taxon(self.name,str(x['id']),name,name,nspace(x.get('rank')).lower(),('accepted' if x.get('is_active') else 'inactive'),source_url=f'https://www.inaturalist.org/taxa/{x["id"]}',retrieved_at=now(),extra={'rank_level':x.get('rank_level'),'ancestry':x.get('ancestry'),'iconic_taxon_name':x.get('iconic_taxon_name')}))
        end=len(rows)<limit;return Batch(out,None if end else str(page+1),end,1,len(rows))

class ITIS(Provider):
    def fetch(self)->Batch:
        base=self.d.get('base_url','https://www.itis.gov/ITISWebService/jsonservice').rstrip('/');cur=sint(self.cursor,int(self.d.get('start_tsn',1)));window=int(self.d.get('scan_window',max(self.batch*4,100)));out=[];req=0;tsn=cur
        while tsn<cur+window and len(out)<self.batch:
            p=self.http.get(base+'/getFullRecordFromTSN',{'tsn':tsn});req+=1
            if isinstance(p,dict):
                core=p.get('coreMetadata') or {};usage=p.get('usage') or {};acc=p.get('acceptedName') or {};name=nspace(usage.get('taxonName') or acc.get('acceptedName'));lineage={}
                h=(p.get('hierarchyUp') or {}).get('hierarchyList') or []
                for row in h:
                    if isinstance(row,dict):lineage[nspace(row.get('rankName')).lower()]=nspace(row.get('taxonName'))
                if name:out.append(Taxon(self.name,str(tsn),name,name,nspace(core.get('rankName')).lower() or 'unknown',nspace(usage.get('usage')).lower() or 'unknown',nspace(core.get('author')),lineage.get('kingdom',''),lineage.get('phylum',''),lineage.get('class',''),lineage.get('order',''),lineage.get('family',''),lineage.get('genus',''),source_url=f'https://www.itis.gov/servlet/SingleRpt/SingleRpt?search_topic=TSN&search_value={tsn}',retrieved_at=now()))
            tsn+=1
        end=tsn>int(self.d.get('max_tsn',9999999));return Batch(out,None if end else str(tsn),end,req,tsn-cur)

class FileJSONL(Provider):
    def fetch(self)->Batch:
        path=Path(str(self.d['path']));path=path if path.is_absolute() else Path.cwd()/path
        if not path.exists():raise FileNotFoundError(path)
        off=sint(self.cursor,0);out=[];raw=0;nextoff=off;total=0
        with path.open('r',encoding='utf-8') as f:
            for i,line in enumerate(f):
                total=i+1
                if i<off:continue
                if len(out)>=self.batch:break
                nextoff=i+1;raw+=1
                try:x=json.loads(line)
                except json.JSONDecodeError:continue
                if not isinstance(x,dict):continue
                pid=x.get('id');name=nspace(x.get('scientific_name') or x.get('scientificName') or x.get('name'))
                if pid in (None,'') or not name:continue
                out.append(Taxon(self.name,str(pid),name,nspace(x.get('canonical_name') or x.get('canonicalName') or name),nspace(x.get('rank')).lower(),nspace(x.get('status')).lower() or 'unknown',nspace(x.get('authorship')),nspace(x.get('kingdom')),nspace(x.get('phylum')),nspace(x.get('class')),nspace(x.get('order')),nspace(x.get('family')),nspace(x.get('genus')),nspace(x.get('accepted_id')),nspace(x.get('source_url')),nspace(x.get('modified')),now(),list(x.get('synonyms',[])),{'bulk_source':str(path)}))
        with path.open('rb') as f:total=sum(1 for _ in f)
        end=nextoff>=total;return Batch(out,None if end else str(nextoff),end,0,raw)

ADAPTERS={'gbif':GBIF,'worms':WoRMS,'wikispecies':Wikispecies,'inaturalist':INaturalist,'itis':ITIS,'file_jsonl':FileJSONL}

class Archive:
    def __init__(self,root:Path,target:int,maxsize:int):
        self.root=root;self.volumes=root/'volumes';self.revisions=root/'revisions';self.conflicts=root/'conflicts';self.states=root/'provider-state';self.manifest_path=root/'manifest.json';self.db_path=root/'index.sqlite3';self.target=target;self.max=maxsize
        for p in (self.volumes,self.revisions,self.conflicts,self.states):p.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.db_path);self.db.row_factory=sqlite3.Row;self._schema();self.manifest=read_json(self.manifest_path,{}) or {'schema_version':SCHEMA,'generated_at':now(),'record_format':'jsonl','target_volume_bytes':target,'max_volume_bytes':maxsize,'total_primary_records':0,'total_revisions':0,'volumes':[],'active_volume':None};self._save()
    def _schema(self):
        self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;CREATE TABLE IF NOT EXISTS taxa(speciedex_id TEXT PRIMARY KEY,identity_key TEXT,scientific_name TEXT,canonical_name TEXT,rank TEXT,status TEXT,authorship TEXT,kingdom TEXT,phylum TEXT,class_name TEXT,order_name TEXT,family TEXT,genus TEXT,record_json TEXT,record_hash TEXT,volume_file TEXT,line_number INTEGER,created_at TEXT,updated_at TEXT);CREATE INDEX IF NOT EXISTS taxa_identity ON taxa(identity_key);CREATE INDEX IF NOT EXISTS taxa_name ON taxa(canonical_name,rank,kingdom);CREATE TABLE IF NOT EXISTS source_ids(provider TEXT,provider_id TEXT,speciedex_id TEXT,PRIMARY KEY(provider,provider_id));CREATE TABLE IF NOT EXISTS assertions(provider TEXT,provider_id TEXT,speciedex_id TEXT,assertion_json TEXT,assertion_hash TEXT,updated_at TEXT,PRIMARY KEY(provider,provider_id));CREATE TABLE IF NOT EXISTS synonyms(synonym_key TEXT,speciedex_id TEXT,provider TEXT,PRIMARY KEY(synonym_key,speciedex_id,provider));CREATE TABLE IF NOT EXISTS conflicts(conflict_id TEXT PRIMARY KEY,conflict_json TEXT,created_at TEXT);''');self.db.commit()
    def close(self):self.db.commit();self.db.close()
    def _save(self):self.manifest['generated_at']=now();write_json(self.manifest_path,self.manifest)
    def identity(self,r:Taxon)->str:return '|'.join([nkey(r.canonical_name),nkey(r.rank),nkey(r.kingdom),nkey(r.authorship)])
    def sid(self,key:str)->str:return 'spx:sha256:'+hashlib.sha256(key.encode()).hexdigest()
    def hash(self,v:Any)->str:return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def active(self)->dict[str,Any]:
        active=self.manifest.get('active_volume')
        for e in self.manifest['volumes']:
            if e['file']==active and not e['sealed']:return e
        n=len(self.manifest['volumes'])+1;e={'file':f'volumes/species-{n:06d}.jsonl','record_count':0,'size_bytes':0,'sha256':None,'sealed':False,'created_at':now(),'sealed_at':None};self.manifest['volumes'].append(e);self.manifest['active_volume']=e['file'];self._save();return e
    def seal(self,e):
        p=self.root/e['file'];e['size_bytes']=p.stat().st_size if p.exists() else 0
        if e['size_bytes']>=self.target:e['sealed']=True;e['sealed_at']=now();e['sha256']=file_hash(p);self.manifest['active_volume']=None
        self._save()
    def source(self,pid:str,provider:str)->str|None:
        row=self.db.execute('SELECT speciedex_id FROM source_ids WHERE provider=? AND provider_id=?',(provider,pid)).fetchone();return str(row['speciedex_id']) if row else None
    def candidates(self,r:Taxon)->list[sqlite3.Row]:return list(self.db.execute('SELECT * FROM taxa WHERE canonical_name=? AND rank=? AND kingdom=?',(nkey(r.canonical_name),nkey(r.rank),nkey(r.kingdom))))
    def add(self,r:Taxon)->str:
        key=self.identity(r);sid=self.sid(key);primary={'schema_version':SCHEMA,'speciedex_id':sid,'identity_key':key,'canonical_name':r.canonical_name,'scientific_name':r.scientific_name,'rank':r.rank,'status':r.status,'authorship':r.authorship,'taxonomy':{'kingdom':r.kingdom,'phylum':r.phylum,'class':r.class_name,'order':r.order,'family':r.family,'genus':r.genus},'first_seen':r.retrieved_at or now(),'initial_source':{'provider':r.provider,'provider_id':r.provider_id,'url':r.source_url}}
        e=self.active();p=self.root/e['file'];est=len(json.dumps(primary,ensure_ascii=False).encode())+1;size=p.stat().st_size if p.exists() else 0
        if size+est>self.max:e['sealed']=True;e['sealed_at']=now();e['sha256']=file_hash(p);self.manifest['active_volume']=None;self._save();e=self.active();p=self.root/e['file']
        line=e['record_count']+1;append_jsonl(p,[primary]);e['record_count']=line;e['size_bytes']=p.stat().st_size;self.manifest['total_primary_records']+=1;pj=json.dumps(primary,ensure_ascii=False,separators=(',',':'))
        self.db.execute('INSERT INTO taxa VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(sid,key,nkey(r.scientific_name),nkey(r.canonical_name),nkey(r.rank),nkey(r.status),nkey(r.authorship),nkey(r.kingdom),nkey(r.phylum),nkey(r.class_name),nkey(r.order),nkey(r.family),nkey(r.genus),pj,self.hash(primary),e['file'],line,primary['first_seen'],primary['first_seen']));self.assertion(sid,r);self.db.commit();self.seal(e);return sid
    def assertion(self,sid:str,r:Taxon)->bool:
        a=r.dict();h=self.hash(a);old=self.db.execute('SELECT assertion_hash FROM assertions WHERE provider=? AND provider_id=?',(r.provider,r.provider_id)).fetchone();changed=bool(old and old['assertion_hash']!=h);aj=json.dumps(a,ensure_ascii=False,separators=(',',':'));ts=now();self.db.execute('INSERT INTO source_ids VALUES(?,?,?) ON CONFLICT(provider,provider_id) DO UPDATE SET speciedex_id=excluded.speciedex_id',(r.provider,r.provider_id,sid));self.db.execute('INSERT INTO assertions VALUES(?,?,?,?,?,?) ON CONFLICT(provider,provider_id) DO UPDATE SET speciedex_id=excluded.speciedex_id,assertion_json=excluded.assertion_json,assertion_hash=excluded.assertion_hash,updated_at=excluded.updated_at',(r.provider,r.provider_id,sid,aj,h,ts))
        for s in r.synonyms:
            if nkey(s):self.db.execute('INSERT OR IGNORE INTO synonyms VALUES(?,?,?)',(nkey(s),sid,r.provider))
        if changed:
            vol=self.manifest['total_revisions']//100000+1;append_jsonl(self.revisions/f'revisions-{vol:06d}.jsonl',[{'schema_version':SCHEMA,'event':'provider_assertion_changed','speciedex_id':sid,'provider':r.provider,'provider_id':r.provider_id,'changed_at':ts,'assertion':a}]);self.manifest['total_revisions']+=1;self._save()
        self.db.commit();return changed
    def conflict(self,r:Taxon,ids:list[str],reason:str):
        v={'provider':r.provider,'provider_id':r.provider_id,'canonical_name':r.canonical_name,'rank':r.rank,'kingdom':r.kingdom,'candidates':ids,'reason':reason,'created_at':now()};cid=self.hash(v);v['conflict_id']=cid;self.db.execute('INSERT OR IGNORE INTO conflicts VALUES(?,?,?)',(cid,json.dumps(v,ensure_ascii=False),v['created_at']));append_jsonl(self.conflicts/'unresolved.jsonl',[v]);self.db.commit()
    def stats(self)->dict[str,int]:
        d={}
        for out,rank in RANKS.items():d[out]=int(self.db.execute("SELECT COUNT(*) c FROM taxa WHERE rank=? AND status IN ('accepted','valid','provisionally accepted','unknown','reference')",(rank,)).fetchone()['c'])
        d['records_archived']=int(self.db.execute('SELECT COUNT(*) c FROM taxa').fetchone()['c']);d['source_assertions']=int(self.db.execute('SELECT COUNT(*) c FROM assertions').fetchone()['c']);d['synonyms']=int(self.db.execute('SELECT COUNT(*) c FROM synonyms').fetchone()['c']);d['unresolved_conflicts']=int(self.db.execute('SELECT COUNT(*) c FROM conflicts').fetchone()['c']);d['volumes']=len(self.manifest['volumes']);return d
    def verify(self)->list[str]:
        errors=[]
        for e in self.manifest['volumes']:
            p=self.root/e['file']
            if not p.exists():errors.append('Missing '+e['file']);continue
            if p.stat().st_size!=e['size_bytes']:errors.append('Size mismatch '+e['file'])
            if e['sealed'] and file_hash(p)!=e['sha256']:errors.append('Hash mismatch '+e['file'])
        return errors

def score(r:Taxon,row:sqlite3.Row)->int:
    s=35 if nkey(r.canonical_name)==row['canonical_name'] else 0;s+=20 if nkey(r.authorship) and nkey(r.authorship)==row['authorship'] else 0;s+=10 if nkey(r.rank)==row['rank'] else 0;s+=15 if nkey(r.kingdom) and nkey(r.kingdom)==row['kingdom'] else 0
    s+=min(sum(4 for v,c in ((r.phylum,'phylum'),(r.class_name,'class_name'),(r.order,'order_name'),(r.family,'family'),(r.genus,'genus')) if nkey(v) and nkey(v)==row[c]),20);return s

def resolve(a:Archive,r:Taxon)->tuple[str,str|None,list[str],str]:
    direct=a.source(r.provider_id,r.provider)
    if direct:return 'match',direct,[direct],'source id'
    exact=list(a.db.execute('SELECT * FROM taxa WHERE identity_key=?',(a.identity(r),)))
    if len(exact)==1:return 'match',str(exact[0]['speciedex_id']),[str(exact[0]['speciedex_id'])],'identity'
    if len(exact)>1:return 'conflict',None,[str(x['speciedex_id']) for x in exact],'duplicate identity'
    scored=sorted([(score(r,x),str(x['speciedex_id'])) for x in a.candidates(r)],reverse=True)
    if not scored:return 'create',None,[],'new'
    best=scored[0][0];ids=[i for s,i in scored if s==best]
    if best>=75 and len(ids)==1:return 'match',ids[0],ids,'high confidence'
    if best>=50:return 'conflict',None,ids,'ambiguous'
    return 'create',None,ids,'low confidence'

def available(d:dict[str,Any])->tuple[bool,str]:
    if not d.get('enabled',True):return False,'disabled'
    missing=[x for x in d.get('required_env',[]) if not os.getenv(str(x))]
    if missing:return False,'missing environment: '+','.join(missing)
    if d.get('adapter') not in ADAPTERS:return False,'unsupported adapter'
    return True,''

def main()->int:
    root=Path(__file__).resolve().parents[2];p=argparse.ArgumentParser();p.add_argument('command',nargs='?',choices=['scan','verify','providers','reindex'],default='scan');p.add_argument('--registry',default=str(root/'static/tools/providers.json'));p.add_argument('--data-root',default=str(root/'static/data'));p.add_argument('--provider',action='append',default=[]);p.add_argument('--all-providers',action='store_true');p.add_argument('--batch-size',type=int,default=500);p.add_argument('--provider-budget',type=int,default=4);p.add_argument('--timeout',type=int,default=30);p.add_argument('--retries',type=int,default=4);p.add_argument('--backoff',type=float,default=2.0);p.add_argument('--volume-target-mb',type=int,default=48);p.add_argument('--volume-max-mb',type=int,default=90);p.add_argument('--history-limit',type=int,default=672);p.add_argument('--verbose',action='store_true');args=p.parse_args();logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    data=Path(args.data_root);registry=read_json(Path(args.registry),{});defs=registry.get('providers',[]);archive=Archive(data/'taxonomy',args.volume_target_mb*1024*1024,args.volume_max_mb*1024*1024);http=HTTP(args.timeout,args.retries,args.backoff,f'Speciedex.org-StatGrabber/{VERSION} (https://speciedex.org)')
    try:
        if args.command=='verify':
            errors=archive.verify();[print(e,file=sys.stderr) for e in errors];return 1 if errors else 0
        eligible=[];skipped=[];wanted=set(args.provider)
        for d in defs:
            if wanted and d.get('name') not in wanted:continue
            ok,reason=available(d)
            (eligible if ok else skipped).append(d if ok else {'provider':d.get('name'),'reason':reason})
        if args.command=='providers':print(json.dumps({'eligible':[d['name'] for d in eligible],'skipped':skipped},indent=2));return 0
        if args.command=='reindex':write_json(data/'statistics.json',{**archive.stats(),'last_updated':now(),'count_method':'local-deduplicated-append-only-canonical-corpus'});return 0
        sched=read_json(data/'taxonomy/scheduler.json',{});cur=sint(sched.get('cursor'),0)
        selected=eligible if args.all_providers or wanted else [eligible[(cur+i)%len(eligible)] for i in range(min(args.provider_budget,len(eligible)))] if eligible else []
        if eligible and not (args.all_providers or wanted):write_json(data/'taxonomy/scheduler.json',{'cursor':(cur+len(selected))%len(eligible),'updated_at':now(),'registered':len(defs),'eligible':len(eligible)})
        summaries=[]
        for d in selected:
            name=d['name'];state=archive.states/f'{name}.json';s={'provider':name,'fetched':0,'created':0,'matched':0,'revised':0,'conflicted':0,'rejected':0,'requests':0,'error':None}
            provider=ADAPTERS[d['adapter']](d,http,state,args.batch_size)
            try:
                b=provider.fetch();s['fetched']=len(b.records);s['requests']=b.requests
                for r in b.records:
                    if not r.provider_id or not r.scientific_name:s['rejected']+=1;continue
                    action,sid,ids,reason=resolve(archive,r)
                    if action=='match':s['matched']+=1;s['revised']+=int(archive.assertion(sid or '',r))
                    elif action=='create':archive.add(r);s['created']+=1
                    else:archive.conflict(r,ids,reason);s['conflicted']+=1
                provider.success(b)
            except Exception as e:provider.failure(e);s['error']=str(e);LOG.exception('Provider failed: %s',name)
            summaries.append(s)
        stats={**archive.stats(),'last_updated':now(),'count_method':'local-deduplicated-append-only-canonical-corpus','generator':{'name':NAME,'version':VERSION}};write_json(data/'statistics.json',stats);write_json(data/'statistics-sources.json',{'generated_at':now(),'providers':summaries,'skipped':skipped})
        hist=read_json(data/'statistics-history.json',[]);snap={k:stats.get(k) for k in ('last_updated','species','genera','families','orders','classes','phyla','kingdoms','records_archived','source_assertions','unresolved_conflicts')};keys=[k for k in snap if k!='last_updated'];hist[-1:]=[snap] if hist and all(hist[-1].get(k)==snap.get(k) for k in keys) else hist[-1:]+[snap] if False else hist
        if not hist or not all(hist[-1].get(k)==snap.get(k) for k in keys):hist.append(snap)
        else:hist[-1]=snap
        write_json(data/'statistics-history.json',hist[-args.history_limit:] if args.history_limit>0 else hist)
        for s in summaries:print(('FAILED' if s['error'] else 'OK').ljust(7),s['provider'],f"fetched={s['fetched']} created={s['created']} matched={s['matched']} revised={s['revised']} conflicts={s['conflicted']}")
        return 1 if summaries and all(s['error'] for s in summaries) else 0
    finally:archive.close()
if __name__=='__main__':raise SystemExit(main())
