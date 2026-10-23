"""Offline container supervisor and configuration, using only bundled dependencies."""

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path('/app')
STATE = ROOT / 'var'
sys.path.insert(0, str(ROOT))


def settings():
    base = os.environ.get('VLLM_BASE_URL', 'http://127.0.0.1:8000/v1').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path != '/v1':
        raise ValueError('VLLM_BASE_URL 必须形如 http://127.0.0.1:8000/v1')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('VLLM_BASE_URL 不应包含密码、查询参数或片段')
    model = os.environ.get('VLLM_MODEL', 'Qwen3.8-27B').strip()
    if not model:
        raise ValueError('VLLM_MODEL 不能为空')
    context = int(os.environ.get('VLLM_CONTEXT', '32768'))
    output = int(os.environ.get('VLLM_MAX_OUTPUT', '8192'))
    app_port = int(os.environ.get('APP_PORT', '18000'))
    opencode_port = int(os.environ.get('OPENCODE_PORT', '14096'))
    if not 1024 <= app_port <= 65535 or not 1024 <= opencode_port <= 65535:
        raise ValueError('APP_PORT 和 OPENCODE_PORT 必须在 1024..65535')
    if app_port == opencode_port or (parsed.hostname in ('127.0.0.1', 'localhost')
                                    and parsed.port in (app_port, opencode_port)):
        raise ValueError('页面、OpenCode、vLLM 必须使用不同端口')
    if not 256 <= output < context:
        raise ValueError('VLLM_CONTEXT 必须大于 VLLM_MAX_OUTPUT，输出长度至少为 256')
    return base, model, context, output, app_port, opencode_port


def web_settings(app_port):
    bind_host = os.environ.get('APP_HOST', '127.0.0.1').strip()
    if bind_host not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('APP_HOST 只支持 127.0.0.1 或 0.0.0.0')
    origin = os.environ.get('DEMO_ORIGIN', '').strip() or f'http://127.0.0.1:{app_port}'
    parsed = urlsplit(origin)
    if (parsed.scheme != 'http' or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or parsed.port != app_port):
        raise ValueError('DEMO_ORIGIN 必须是 http://服务器IP:APP_PORT，不带尾斜杠或路径')
    networks = tuple(
        ipaddress.ip_network(value.strip())
        for value in os.environ.get('DEMO_ALLOWED_CIDRS', '').split(',')
        if value.strip()
    )
    if bind_host == '0.0.0.0':
        if not networks:
            raise ValueError('内网访问必须配置 DEMO_ALLOWED_CIDRS')
        if parsed.hostname in ('127.0.0.1', 'localhost', '::1', '0.0.0.0'):
            raise ValueError('内网访问的 DEMO_ORIGIN 必须使用服务器真实内网地址')
    return bind_host, origin


def seed_directory(target, source):
    target.mkdir(parents=True, exist_ok=True)
    marker = target / '.offline-initialized'
    if not marker.exists():
        for item in source.iterdir():
            if item.name == 'node_modules':
                continue
            dest = target / item.name
            if not dest.exists():
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        marker.touch()
    modules = target / 'node_modules'
    if not modules.exists() and not modules.is_symlink():
        modules.symlink_to('/opt/opencode-deps/node_modules', target_is_directory=True)


def sync_tuning_catalog(target, source):
    """Upgrade shipped tuning instructions on existing persistent catalog mounts."""
    for relative in ('agents/office.md', 'agents/control.md', 'agents/st-code.md', 'skills/pid-tuning/SKILL.md',
                     'skills/control-safety/SKILL.md'):
        content = (source / relative).read_bytes()
        dest = target / relative
        if dest.is_symlink():
            raise ValueError(f'内置调优文件不能是符号链接：{relative}')
        if dest.exists():
            previous = dest.read_bytes()
            if previous == content:
                continue
            digest = hashlib.sha256(previous).hexdigest()
            backup = target / '.builtin-backups/tuning-v3' / relative / digest
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not backup.exists():
                backup.write_bytes(previous)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name(dest.name + '.upgrade-tmp')
        temporary.write_bytes(content)
        temporary.replace(dest)
        print(f'已更新内置调优说明：{relative}；旧内容保存在 .builtin-backups/tuning-v3。', flush=True)


def configure(create=False):
    base, model, context, output, app_port, opencode_port = settings()
    _, origin = web_settings(app_port)
    secret_file = STATE / 'runtime-secrets.json'
    if create:
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not secret_file.exists():
            values = {key: secrets.token_urlsafe(40)
                      for key in ('OPENCODE_SERVER_PASSWORD', 'MCP_SERVICE_TOKEN')}
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(values, stream)
        seed_directory(ROOT / '.opencode', Path('/opt/catalog-seed'))
        sync_tuning_catalog(ROOT / '.opencode', Path('/opt/catalog-seed'))
        seed_directory(STATE / 'opencode/config/opencode', Path('/opt/opencode-deps'))
    values = json.loads(secret_file.read_text())
    if any(len(values.get(key, '')) < 32
           for key in ('OPENCODE_SERVER_PASSWORD', 'MCP_SERVICE_TOKEN')):
        raise ValueError('持久化服务凭据不完整；请恢复 data/var/runtime-secrets.json 的备份')
    os.environ.update(values)
    os.environ.update({
        'DEMO_STATE_DIR': str(STATE / 'backend'),
        'DEMO_CATALOG_ROOT': str(ROOT),
        'DEMO_LOCAL_DEV': '1',
        'DEMO_ORIGIN': origin,
        'DEMO_OPENCODE_URL': f'http://127.0.0.1:{opencode_port}',
        'DEMO_PROVIDER': 'local-vllm',
        'DEMO_MODEL': model,
        'DEMO_MODEL_API_KEY': os.environ.get('VLLM_API_KEY', 'EMPTY'),
        'DEMO_PROVIDER_CONFIG': str(STATE / 'provider.json'),
        'OPENCODE_BIN': '/opt/bin/opencode',
        'DEMO_OPENCODE_PORT': str(opencode_port),
        'INTERNAL_API_BASE_URL': f'http://127.0.0.1:{app_port}/internal/v1',
        'NO_PROXY': '*', 'no_proxy': '*',
    })
    if create:
        provider = {'provider': {'local-vllm': {
            'npm': '@ai-sdk/openai-compatible', 'name': '内网 vLLM',
            'options': {'baseURL': base, 'apiKey': '{env:DEMO_MODEL_API_KEY}'},
            'models': {model: {'name': model, 'tool_call': True,
                               'limit': {'context': context, 'output': output}}},
        }}}
        config = STATE / 'provider.json'
        config.write_text(json.dumps(provider, ensure_ascii=False, indent=2))
        config.chmod(0o600)
    return app_port, opencode_port


def preflight():
    _, model, _, _, app_port, opencode_port = settings()
    bind_host, _ = web_settings(app_port)
    for host, port in ((bind_host, app_port), ('127.0.0.1', opencode_port)):
        with socket.socket() as sock:
            # Match the servers' bind behavior: a recently stopped listener can
            # leave TIME_WAIT connections even though the port is available.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError as exc:
                raise ValueError(f'宿主机端口 {port} 被占用；请修改 config/demo.env 的端口') from exc
    free = shutil.disk_usage(STATE).free
    if free < 1024**3:
        raise ValueError('数据磁盘可用空间不足 1 GiB')
    print(f'配置有效；端口 {app_port}/{opencode_port} 可用；模型 ID={model}', flush=True)


def serve():
    app_port, _ = configure(create=True)
    bind_host, public_origin = web_settings(app_port)
    preflight()
    stopping = False
    children = {}

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    commands = {
        'sampler': [sys.executable, '-m', 'examples.live_process',
                    '--state-dir', os.environ['DEMO_STATE_DIR']],
        'backend': [sys.executable, '-m', 'uvicorn', 'apps.orchestrator.main:app',
                    '--host', bind_host, '--port', str(app_port), '--workers', '1',
                    '--no-proxy-headers'],
        'opencode': [sys.executable, 'scripts/launch_opencode.py'],
    }
    code = 0
    try:
        for name, command in commands.items():
            children[name] = subprocess.Popen(command, cwd=ROOT, start_new_session=True)
        (STATE / 'supervisor.json').write_text(json.dumps(
            {name: process.pid for name, process in children.items()}))
        print(f'服务启动中。浏览器：{public_origin}；采样窗口需要约 120 秒。', flush=True)
        while not stopping:
            for name, process in children.items():
                if process.poll() is not None:
                    print(f'{name} 退出，code={process.returncode}；停止整组服务等待 Docker 重启。',
                          flush=True)
                    stopping, code = True, 1
                    break
            time.sleep(0.5)
    finally:
        for process in children.values():
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 20
        for process in children.values():
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        while True:
            try:
                child, _ = os.waitpid(-1, os.WNOHANG)
                if child == 0:
                    break
            except ChildProcessError:
                break
        (STATE / 'supervisor.json').unlink(missing_ok=True)
    return code


def health():
    app_port, opencode_port = configure()
    pids = json.loads((STATE / 'supervisor.json').read_text())
    for pid in pids.values():
        os.kill(pid, 0)
    with httpx.Client(trust_env=False, timeout=3) as client:
        response = client.get(f'http://127.0.0.1:{app_port}/api/v1/health')
        response.raise_for_status()
        report = response.json()
        local_ready = all(item['ready'] for item in report['components']
                          if item['name'] in ('database', 'disk'))
        oc = client.get(f'http://127.0.0.1:{opencode_port}/global/health',
                        auth=('opencode', os.environ['OPENCODE_SERVER_PASSWORD']))
        oc.raise_for_status()
        if not report['live'] or not local_ready or oc.json().get('version') != '1.18.31':
            return 1
    print('容器进程、数据库、页面 API 和 OpenCode 正常；业务 ready 请运行 check。')
    return 0


def check(wait_seconds):
    # A check immediately after `up` must also wait for first-time credential
    # creation. Reading those files here races the supervisor's initialization.
    _, _, _, _, app_port, _ = settings()
    deadline = time.monotonic() + wait_seconds
    last = None
    with httpx.Client(trust_env=False, timeout=10) as client:
        while True:
            try:
                r = client.get(f'http://127.0.0.1:{app_port}/api/v1/health')
                r.raise_for_status()
                last = r.json()
                if last.get('ready'):
                    print(json.dumps(last, ensure_ascii=False, indent=2))
                    print('验收通过：模型结构化调用、MCP、采样、数据库均已就绪。')
                    return 0
            except httpx.HTTPError as exc:
                last = {'error': type(exc).__name__}
            if time.monotonic() >= deadline:
                print(json.dumps(last, ensure_ascii=False, indent=2))
                print('尚未就绪；先运行 probe-model，再查看 logs。首次启动至少等待 120 秒。')
                return 1
            print('等待就绪（每 10 秒检查一次）……', flush=True)
            time.sleep(10)


def model_probe(list_only):
    base, model, _, _, _, _ = settings()
    os.environ['DEMO_MODEL_API_KEY'] = os.environ.get('VLLM_API_KEY', 'EMPTY')
    headers = {'Authorization': 'Bearer ' + os.environ['DEMO_MODEL_API_KEY']}
    with httpx.Client(trust_env=False, timeout=15, headers=headers) as client:
        response = client.get(base + '/models')
        if not response.is_success:
            print(f'模型列表 HTTP {response.status_code}；检查地址和 API Key。')
            return 1
        available = [item['id'] for item in response.json().get('data', [])]
        print('vLLM 返回的模型 ID：' + json.dumps(available, ensure_ascii=False), flush=True)
        if list_only:
            return 0
        if model not in available:
            print('VLLM_MODEL 不匹配；将 config/demo.env 改成上面准确的 ID。')
            return 1
    result = subprocess.run([sys.executable, 'scripts/probe_vllm.py',
                             '--base-url', base, '--model', model], cwd=ROOT)
    return result.returncode


async def agent_probe():
    configure()
    from apps.orchestrator.config import Settings
    from packages.adapters.opencode import OpenCodeClient
    client = OpenCodeClient(Settings.from_env())
    try:
        await client.probe()
        print(json.dumps({'ready': client.ready, 'error_code': client.error_code}, indent=2))
        return 0 if client.ready else 1
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['serve', 'health', 'check', 'preflight',
                                          'models', 'probe-model', 'probe-agent', 'smoke', 'smoke-office', 'init-admin'])
    parser.add_argument('--wait', type=int, default=0)
    parser.add_argument('--min-b-candidates', type=int, choices=range(1, 13), default=1)
    args = parser.parse_args()
    if args.action == 'serve':
        return serve()
    if args.action == 'init-admin':
        configure(create=True)
        return subprocess.call([sys.executable, 'scripts/init_admin.py'], cwd=ROOT)
    if args.action == 'preflight':
        preflight()
        return 0
    if args.action == 'health':
        return health()
    if args.action == 'check':
        return check(args.wait)
    if args.action in ('models', 'probe-model'):
        return model_probe(args.action == 'models')
    if args.action == 'probe-agent':
        return asyncio.run(agent_probe())
    if args.action == 'smoke-office':
        app_port, _ = configure()
        return subprocess.call([sys.executable, 'scripts/smoke_office.py', '--base',
                                f'http://127.0.0.1:{app_port}'], cwd=ROOT)
    app_port, _ = configure()
    return subprocess.call([sys.executable, 'scripts/smoke_real.py',
                            '--base', f'http://127.0.0.1:{app_port}',
                            '--min-b-candidates', str(args.min_b_candidates)], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # Do not include credentials or full HTTP request headers in diagnostics.
        print(f'部署检查失败：{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        raise SystemExit(1)
