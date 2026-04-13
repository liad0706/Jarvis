"""Tiny server that serves the RuView map HTML and proxies API calls to avoid CORS."""
import http.server
import urllib.request
import json
import os

PORT = 8888
RUVIEW_API = "http://localhost:3000"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "ruview_map.html")
STATIC_DIR = SCRIPT_DIR

# Map file extensions to MIME types
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".css": "text/css",
    ".js": "application/javascript",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(HTML_PATH, "rb") as f:
                self.wfile.write(f.read())
        elif self.path.startswith("/api/") or self.path == "/health":
            # Proxy to RuView
            try:
                url = RUVIEW_API + self.path
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
            except Exception as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            # Serve static files (images, css, js) from scripts directory
            safe_path = self.path.lstrip("/")
            file_path = os.path.join(STATIC_DIR, safe_path)
            file_path = os.path.normpath(file_path)
            # Security: ensure file is within STATIC_DIR
            if not file_path.startswith(os.path.normpath(STATIC_DIR)):
                self.send_response(403)
                self.end_headers()
                return
            if os.path.isfile(file_path):
                ext = os.path.splitext(file_path)[1].lower()
                mime = MIME_TYPES.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "max-age=60")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()

    def log_message(self, format, *args):
        import sys
        print(format % args, file=sys.stderr)


if __name__ == "__main__":
    print(f"RuView Map: http://localhost:{PORT}")
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
