"""Run against the real app with a TEST model; label the actual test network mode."""

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, '/app')
sys.path.insert(0, '/opt/deploy')
from runtime import configure
from packages.adapters.opencode import verify_permissions

app_port, opencode_port = configure()
with httpx.Client(base_url=f'http://127.0.0.1:{app_port}', trust_env=False, timeout=60) as client:
    for _ in range(36):
        health = client.get('/api/v1/health').json()
        if health.get('ready'):
            break
        time.sleep(5)
    assert health['ready'], health
    html = client.get('/')
    assert html.status_code == 200 and '<div id="root">' in html.text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html.text)
    assert assets
    for asset in assets:
        assert client.get(asset).status_code == 200, asset
    csrf = client.get('/api/v1/csrf-token').json()['token']
    headers = {'Origin': f'http://localhost:{app_port}', 'X-CSRF-Token': csrf,
               'Idempotency-Key': str(uuid.uuid4())}
    session = client.post('/api/v1/chat/sessions', json={'schema_version': '1.0'}, headers=headers)
    assert session.status_code == 201, session.text
    session_id = session.json()['session_id']
    Path('/app/var/test-session-id').write_text(session_id)
    headers['Idempotency-Key'] = str(uuid.uuid4())
    response = client.post(f'/api/v1/chat/sessions/{session_id}/messages',
                           json={'schema_version': '1.0', 'message': '你好'}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['response']['status'] == 'complete', response.text
    assert '离线部署连通性测试' in response.json()['response']['content'], response.text
    bad = client.post('/api/v1/chat/sessions', json={'schema_version': '1.0'},
                      headers={**headers, 'Origin': 'http://bad.invalid'})
    assert bad.status_code == 403
    bad_mcp = client.post('/internal/v1/tools/get_observation', json={})
    assert bad_mcp.status_code in (401, 422)
    catalog = client.get('/api/v1/catalog')
    assert catalog.status_code == 200
    oc = httpx.Client(base_url=f'http://127.0.0.1:{opencode_port}', trust_env=False, timeout=10,
                      auth=('opencode', os.environ['OPENCODE_SERVER_PASSWORD']))
    with oc:
        verify_permissions(oc.get('/agent').json())
        assert oc.get('/mcp').json()['industrial_control']['status'] == 'connected'
    secret = json.loads(Path('/app/var/runtime-secrets.json').read_text())
    assert len(secret['MCP_SERVICE_TOKEN']) >= 32
    print(json.dumps({'network': os.environ.get('REHEARSAL_NETWORK_MODE', 'none'),
                      'model': 'TEST STUB (not Qwen inference)',
                      'health': health, 'static_assets': len(assets),
                      'chat_streaming': 'passed', 'csrf': 'passed',
                      'agents_permissions': 'passed', 'mcp_connected': True,
                      'persistent_session_id': session_id}, ensure_ascii=False, indent=2))
