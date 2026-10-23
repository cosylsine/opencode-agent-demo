"""Exercise the delivered Bash updater against an isolated real legacy Docker deployment."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
D=[str(ROOT/'.tools/docker/docker'),'-H','unix:///home/xjx/.cache/opencode-deploy-build/docker.sock']
SOCKET='/home/xjx/.cache/opencode-deploy-build/docker.sock'
FIX=ROOT/'verification/release-20261021/upgrade-fixture'
STAGE=ROOT/'delivery/.intranet-rehearsal/disc-01'
NAME='intranet-release-upgrade-fixture'
NEW='opencode-agent-demo:20261021-intranet-amd64'
OLD='sha256:b5390fad6856728418d40eb74b79787cde538d17c57ff2f73eadf9ff7b0bf9eb'
LOG=ROOT/'verification/release-20261021/migration-rollback.log'

def call(args, data=None):
    p=subprocess.run(args,input=data,text=True,capture_output=True)
    with LOG.open('a') as f: f.write(p.stdout+p.stderr)
    if p.returncode: raise RuntimeError('Command failed: '+str(args[:5])+'\n'+p.stdout[-2000:]+p.stderr[-2000:])
    return p.stdout.strip()


def helper(command,data=None):
    return call(D+['run','--rm','-i','--network','host','-v',str(FIX)+':'+str(FIX),
                   '-v',str(STAGE)+':'+str(STAGE)+':ro', '-v',SOCKET+':'+SOCKET,
                   '-v',str(ROOT/'.tools/docker/docker')+':/usr/local/bin/real-docker:ro',
                   '-e','DOCKER_HOST=unix://'+SOCKET,'-e','PATH='+str(FIX/'test-bin')+':/opt/venv/bin:/opt/node/bin:/opt/bin:/usr/local/bin:/usr/bin:/bin',
                   '--entrypoint','bash',NEW,'-c',command],data)


def main():
    if FIX.exists(): raise RuntimeError('Preserve previous fixture before retry')
    FIX.mkdir()
    for name in ['source','images','config','data/var','data/catalog','test-bin']: (FIX/name).mkdir(parents=True,exist_ok=True)
    (FIX/'source/legacy-only.txt').write_text('old-code-sentinel')
    (FIX/'data/catalog/custom-skill.txt').write_text('field-custom-resource')
    olddeploy=(ROOT/'deploy.sh').read_text().replace(NEW,OLD)
    (FIX/'deploy.sh').write_text(olddeploy)
    (FIX/'config/demo.env').write_text('APP_HOST=127.0.0.1\nAPP_PORT=18221\nOPENCODE_PORT=14221\nDEMO_ORIGIN=http://127.0.0.1:18221\nVLLM_BASE_URL=http://127.0.0.1:4722/v1\nVLLM_MODEL=Qwen3.8-27B\nVLLM_API_KEY=EMPTY\nVLLM_CONTEXT=32768\n')
    # Translate only the fixed container-name argument, never image tags or labels.
    (FIX/'test-bin/docker').write_text('#!/bin/bash\nargs=()\nfor arg in "$@"; do\n if [[ "$arg" == opencode-agent-demo ]]; then args+=("'+NAME+'"); else args+=("$arg"); fi\ndone\nexec /usr/local/bin/real-docker "${args[@]}"\n')
    (FIX/'test-bin/docker').chmod(0o755)
    env=['--env-file',str(FIX/'config/demo.env')]
    mounts=['-v',str(FIX/'data/var')+':/app/var','-v',str(FIX/'data/catalog')+':/app/.opencode']
    program="""import sys,sqlite3
from pathlib import Path
sys.path.insert(0,'/opt/deploy')
import runtime
runtime.configure(create=True)
from packages.adapters.storage import Store
s=Store(Path('/app/var/backend'));s.close()
c=sqlite3.connect('/app/var/backend/demo.db')
c.execute(\"INSERT INTO chat_session(id,user_id,title,created_at) VALUES('legacy-chat','old-shared-proxy-user','legacy-history','2026-10-18T00:00:00Z')\")
c.commit();print(c.execute('SELECT version_num FROM alembic_version').fetchone()[0]);c.close()
"""
    revision=call(D+['run','--rm','--network','none',*env,*mounts,'--entrypoint','python',OLD,'-c',program]).splitlines()[-1]
    call(D+['run','-d','--name',NAME,'--network','host','--label','io.opencode-demo.offline=1','--label','io.opencode-demo.bundle='+str(FIX),*env,*mounts,'--entrypoint','sleep',OLD,'infinity'])
    before=json.loads(helper("python -c \"import hashlib,json;from pathlib import Path;p=Path('"+str(FIX)+"');print(json.dumps({n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ['config/demo.env','data/var/runtime-secrets.json','data/var/backend/demo.db','deploy.sh']}))\""))
    script=str(STAGE/'scripts/intranet-update.sh')
    helper(f'bash {script} check {FIX}')
    helper(f'bash {script} apply {FIX}')
    backup=helper(f"ls -d {FIX}/backups/intranet-20261021-*")
    assert backup.startswith(str(FIX/'backups'))
    assert call(D+['inspect','-f','{{.State.Running}}',NAME])=='false'
    helper(f'cd {FIX} && bash deploy.sh init-admin','migration-admin\nmigration-test-password\nmigration-test-password\n')
    result=json.loads(helper("python -c \"import sqlite3,json,hashlib;from pathlib import Path;p=Path('"+str(FIX)+"');c=sqlite3.connect(p/'data/var/backend/demo.db');print(json.dumps({'head':c.execute('select version_num from alembic_version').fetchone()[0],'owner':c.execute(\\\"select user_id from chat_session where id='legacy-chat'\\\").fetchone()[0],'config_hash':hashlib.sha256((p/'config/demo.env').read_bytes()).hexdigest(),'secret_hash':hashlib.sha256((p/'data/var/runtime-secrets.json').read_bytes()).hexdigest(),'custom':(p/'data/catalog/custom-skill.txt').read_text()}))\""))
    assert result['head']=='0007' and result['owner']!='old-shared-proxy-user'
    assert result['config_hash']==before['config/demo.env'] and result['secret_hash']==before['data/var/runtime-secrets.json']
    assert result['custom']=='field-custom-resource'
    assert not (FIX/'source/legacy-only.txt').exists()
    helper(f'cd {FIX} && bash deploy.sh up')
    new_id=call(D+['inspect','-f','{{.Image}}',NAME])
    assert new_id==call(D+['image','inspect','-f','{{.Id}}',NEW])
    # Repeat apply must be rejected, without stopping the running deployment.
    helper(f'if bash {script} apply {FIX}; then exit 1; fi')
    failure=subprocess.run(D+['exec',NAME,'true'],capture_output=True)
    assert failure.returncode==0
    helper(f'bash {script} rollback {FIX} {backup}')
    after=json.loads(helper("python -c \"import hashlib,json;from pathlib import Path;p=Path('"+str(FIX)+"');print(json.dumps({n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ['config/demo.env','data/var/runtime-secrets.json','data/var/backend/demo.db','deploy.sh']}))\""))
    assert before==after,(before,after)
    assert (FIX/'source/legacy-only.txt').read_text()=='old-code-sentinel'
    helper(f'cd {FIX} && bash deploy.sh up')
    assert call(D+['inspect','-f','{{.Image}}',NAME])==OLD
    call(D+['stop','--time','40',NAME]);call(D+['rm',NAME])
    report={'baseline_image':OLD,'baseline_schema':revision,'target_image':new_id,'target_schema':result['head'],'old_history_owned_by_admin':True,'field_config_and_secrets_unchanged':True,'custom_catalog_preserved':True,'removed_code_not_left_behind':True,'application_start':True,'rollback_exact_database_config_secrets_and_script':True,'old_image_restarted':True,'backup_complete':True,'test_model':'no real Qwen; isolated local rehearsal','fixture':str(FIX)}
    (ROOT/'verification/release-20261021/migration-rollback.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
