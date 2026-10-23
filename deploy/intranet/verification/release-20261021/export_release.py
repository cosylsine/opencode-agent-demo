import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
docker = [str(root / '.tools/docker/docker'), '-H', 'unix:///home/xjx/.cache/opencode-deploy-build/docker.sock']
tags = {'application':'opencode-agent-demo:20261021-intranet-amd64', 'runtime':'opencode-agent-demo-runtime:20261018-office-amd64'}
archive = root/'images/offline-images.new.tar.gz'
# Check both sides of the pipe; never publish a truncated docker save.
with archive.open('wb') as stream:
    save = subprocess.Popen(docker+['save',*tags.values()],stdout=subprocess.PIPE)
    compress = subprocess.Popen(['gzip','-1'],stdin=save.stdout,stdout=stream)
    save.stdout.close()
    assert compress.wait()==0
    assert save.wait()==0
with tarfile.open(archive) as tar:
    manifests=json.load(tar.extractfile('manifest.json'))
    assert sorted(tag for m in manifests for tag in m['RepoTags'])==sorted(tags.values())
entries={}
for key,tag in tags.items():
    d=json.loads(subprocess.check_output(docker+['image','inspect',tag]))[0]
    entries[key]={'tag':tag,'id':d['Id'],'architecture':d['Architecture'],'os':d['Os'],'size':d['Size']}
    match=next(m for m in manifests if tag in m['RepoTags'])
    with tarfile.open(archive) as tar:
        assert 'sha256:'+hashlib.sha256(tar.extractfile(match['Config']).read()).hexdigest()==d['Id']
hashvalue=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()
entries.update(created_date='2026-10-21',build_network='none',archive_sha256=hashvalue,archive_bytes=archive.stat().st_size,database_head='0007',runtime_versions={'Python':'3.13.2','Node':'22.23.3','OpenCode':'1.18.31'},test_model_in_image=False,runtime_data_in_image=False)
archive.replace(root/'images/offline-images.tar.gz')
(root/'images/IMAGE-MANIFEST.json').write_text(json.dumps(entries,indent=2)+'\n')
(root/'images/SHA256SUMS').write_text(hashvalue+'  images/offline-images.tar.gz\n')
print(json.dumps(entries,indent=2))
