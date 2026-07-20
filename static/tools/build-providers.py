#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path

MODULE = """#!/usr/bin/env python3
\"\"\"Generated Speciedex provider adapter for {label}.\"\"\"
from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping
from .common import Batch, BaseProvider, Taxon

class Provider(BaseProvider):
    NAME = {name!r}
    LABEL = {label!r}
    ROLE = {role!r}
    SITE_URL = {site_url!r}
    API_URL = {api_url!r}
    RESPONSE_SCHEMA = Path({schema_path!r})

    def fetch(self) -> Batch:
        raise NotImplementedError(\"Implement request and pagination using RESPONSE_SCHEMA.\")

    def normalize_record(self, value: Mapping[str, Any]) -> Taxon:
        raise NotImplementedError(\"Implement provider-specific field normalization.\")
"""

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('manifest',type=Path)
    ap.add_argument('--repo-root',type=Path,default=Path('.'))
    ap.add_argument('--overwrite',action='store_true')
    a=ap.parse_args()
    data=json.loads(a.manifest.read_text(encoding='utf-8'))
    made_m=made_s=0
    for p in data.get('providers',[]):
        name=str(p.get('name','')).strip()
        if not re.fullmatch(r'[a-z0-9_]+',name): raise SystemExit(f'Invalid provider name: {name!r}')
        mod=a.repo_root/'static'/'tools'/'providers'/f'{name}.py'
        schema_rel=str(p.get('response_schema_path',f'static/tools/providers/schemas/{name}.schema.json'))
        sch=a.repo_root/schema_rel
        if a.overwrite or not mod.exists():
            mod.parent.mkdir(parents=True,exist_ok=True)
            mod.write_text(MODULE.format(name=name,label=p.get('label',name),role=p.get('role',''),site_url=p.get('site_url'),api_url=p.get('api_url'),schema_path=schema_rel),encoding='utf-8')
            made_m+=1
        stub={'$schema':'https://json-schema.org/draft/2020-12/schema','schema_version':1,'provider':name,'content_type':'application/json','request':{'method':'GET','endpoint':p.get('api_url'),'headers':{},'query':{},'body':None},'records':{'root':'$','field_map':{'provider_id':None,'scientific_name':None,'canonical_name':None,'authorship':None,'rank':None,'status':None,'accepted_provider_id':None,'kingdom':None,'phylum':None,'class_name':None,'order':None,'family':None,'genus':None,'synonyms':None,'source_url':None,'source_modified':None}},'pagination':{'mode':'none','request_parameter':None,'next_path':None,'exhausted_path':None,'limit_parameter':None},'examples':[],'verification':{'status':'unverified','notes':'Populate from captured provider responses before enabling.'}}
        if a.overwrite or not sch.exists():
            sch.parent.mkdir(parents=True,exist_ok=True)
            sch.write_text(json.dumps(stub,indent=2)+'\n',encoding='utf-8')
            made_s+=1
    print(json.dumps({'providers_seen':len(data.get('providers',[])),'modules_created':made_m,'schemas_created':made_s},indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
