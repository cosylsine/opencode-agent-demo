import hashlib
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
docker = [str(root / '.tools/docker/docker'), '-H', 'unix:///home/xjx/.cache/opencode-deploy-build/docker.sock']
image = 'opencode-agent-demo:20261021-intranet-amd64'
files = {}
for name in ['apps', 'packages', 'migrations', 'scripts', 'examples', '.opencode']:
    for p in (root / 'source' / name).rglob('*'):
        if p.is_file() and not any(x in p.parts for x in ['node_modules', '__pycache__', 'tests', 'test-results', 'dist-copy-update', 'playwright-report']):
            files['/app/' + str(p.relative_to(root / 'source'))] = hashlib.sha256(p.read_bytes()).hexdigest()
files['/app/opencode.json'] = hashlib.sha256((root / 'source/opencode.json').read_bytes()).hexdigest()
files['/opt/deploy/runtime.py'] = hashlib.sha256((root / 'build/runtime.py').read_bytes()).hexdigest()
program = '''import json,sys,hashlib
from pathlib import Path
expected=json.load(sys.stdin)
bad={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name,digest in expected.items() if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest}
assert not bad,bad
assert not Path('/app/var/runtime-secrets.json').exists()
assert not Path('/app/tests').exists()
print(json.dumps({'files_compared':len(expected),'mismatches':bad,'runtime_secrets_present':False,'test_backend_present':False}))
'''
result = subprocess.run(docker + ['run', '--rm', '-i', '--network', 'none', '--entrypoint', 'python', image, '-c', program], input=json.dumps(files), text=True, capture_output=True, check=True)
data = json.loads(result.stdout)
data['image_id'] = subprocess.check_output(docker + ['image', 'inspect', '-f', '{{.Id}}', image], text=True).strip()
data['hashes'] = files
(root / 'verification/release-20261021/image-source-check.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps({k:v for k,v in data.items() if k != 'hashes'},indent=2))
