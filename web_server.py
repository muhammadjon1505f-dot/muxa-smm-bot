"""
Muxa SMM Bot — Render.com va 24/7 Hosting uchun Yengil HTTP Health-Check Serveri.
Render Web Service uchun port ochadi va xizmatni 'Healthy' holatda ushlab turadi.
"""
import os
import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger("health_server")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz", "/ping"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            response = {
                "status": "online",
                "service": "Muxa SMM Telegram Bot",
                "uptime": "24/7 active",
                "endpoints": {
                    "health": "/healthz",
                    "root": "/"
                }
            }
            self.wfile.write(json.dumps(response, ensure_ascii=False, indent=2).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')

    def log_message(self, format, *args):
        # Jim rejim: Render konsol loglarini keraksiz so'rovlar bilan to'ldirmaslik uchun
        pass


def run_health_server(port: int = 10000):
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        logger.info(f"Health-check server listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health-check server failed on port {port}: {e}")


def start_health_server_in_background(port: int = None):
    if port is None:
        port = int(os.getenv("PORT", "10000"))
    thread = threading.Thread(target=run_health_server, args=(port,), daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    p = int(os.getenv("PORT", "10000"))
    print(f"Starting test server on port {p}...")
    run_health_server(p)
