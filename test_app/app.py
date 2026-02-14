from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import os

PORT = int(os.environ.get("PORT", 3000))

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "ok",
            "message": "Hello from AppGarden test app!",
            "port": PORT,
        }).encode())

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Listening on port {PORT}")
    server.serve_forever()
