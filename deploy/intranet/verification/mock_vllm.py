"""Deterministic TEST-ONLY OpenAI server, never used by the deployment entrypoint.

It tests transport, streaming, and structured tool output; it is not a real LLM.
Run inside a --network none test container on that container's loopback.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = 'Qwen3.8-27B'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass

    def reply(self, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply({'object': 'list', 'data': [{'id': MODEL, 'object': 'model'}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        # Never persist Authorization or other request headers.
        with Path('/tmp/mock-requests.jsonl').open('a') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + '\n')
        tools = {item['function']['name']: item['function'] for item in payload.get('tools', [])}
        message = {'role': 'assistant', 'content': '服务连接正常。这是离线部署连通性测试。'}
        finish = 'stop'
        if 'StructuredOutput' in tools:
            args = {'schema_version': '1.0', 'assessment': 'NORMAL_STABLE', 'confidence': 1,
                    'needs_tuning': False, 'manual_review_required': False,
                    'evidence': [], 'reason': 'readiness', 'warnings': []}
            tool = 'StructuredOutput'
        elif 'echo' in tools:
            args, tool = {'text': 'ready'}, 'echo'
        else:
            args, tool = None, None
        if tool:
            message['content'] = None
            message['tool_calls'] = [{'id': 'call_offline_1', 'type': 'function',
                                      'function': {'name': tool, 'arguments': json.dumps(args)}}]
            finish = 'tool_calls'
        elif payload.get('response_format'):
            message['content'] = '{"status":"ready"}'
        result = {'id': 'chatcmpl-offline-test', 'object': 'chat.completion',
                  'created': int(time.time()), 'model': MODEL,
                  'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                  'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}}
        if not payload.get('stream'):
            return self.reply(result)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        if tool:
            message['tool_calls'][0]['index'] = 0
        for delta, stop in [(message, None), ({}, finish)]:
            chunk = {**result, 'object': 'chat.completion.chunk',
                     'choices': [{'index': 0, 'delta': delta, 'finish_reason': stop}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 38000), Handler).serve_forever()
