# -*- coding: utf-8 -*-
"""模拟教务选课接口：恒返回成功，用于验证「成功即停」。"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        self.rfile.read(ln)
        out = {"success": True, "message": "模拟教务：选课成功"}
        body = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("mock success listening on 8125")
    HTTPServer(("127.0.0.1", 8125), Handler).serve_forever()