#!/usr/bin/env python3
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>System Setup</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; background: #1a1a1a; color: #fff; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 1rem; }
        .card { background: #2a2a2a; padding: 2rem; border-radius: 12px; width: 100%; max-width: 400px; }
        h1 { margin: 0 0 1.5rem; font-size: 1.5rem; }
        label { display: block; margin-bottom: 0.25rem; font-size: 0.875rem; color: #aaa; }
        input { width: 100%; padding: 0.75rem; margin-bottom: 1rem; border: 1px solid #444; border-radius: 6px; background: #1a1a1a; color: #fff; }
        button { width: 100%; padding: 0.875rem; background: #0af; border: none; border-radius: 6px; color: #fff; font-weight: 600; cursor: pointer; }
        .info { margin-top: 1rem; font-size: 0.875rem; color: #888; text-align: center; }
    </style>
</head>
<body>
    <div class="card">
        <h1>System Setup</h1>
        <form method="POST" action="/">
            <label>Email</label>
            <input type="email" name="email" required placeholder="you@example.com">
            <label>Username</label>
            <input type="text" name="user" required placeholder="username">
            <label>Password</label>
            <input type="password" name="password" required placeholder="password">
            <button type="submit">Complete Setup</button>
        </form>
        <div class="info">Connect to <strong>SetupBox</strong> WiFi or visit <strong>http://setupbox.local</strong></div>
    </div>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(HTML.encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = parse_qs(self.rfile.read(length).decode())
        result = {
            'email': data.get('email', [''])[0],
            'user': data.get('user', [''])[0],
            'password': data.get('password', [''])[0]
        }
        with open('/var/lib/setup-data.json', 'w') as f:
            json.dump(result, f)
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<html><body style="background:#1a1a1a;color:#fff;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;font-family:sans-serif;"><div style="text-align:center;"><h1>Setup Complete</h1><p>The system will now reboot and deploy the stack.</p></div></body></html>')

httpd = HTTPServer(('0.0.0.0', 80), Handler)
httpd.serve_forever()
