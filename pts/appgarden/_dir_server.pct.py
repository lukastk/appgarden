# ---
# jupyter:
#   kernelspec:
#     display_name: .venv
#     language: python
#     name: python3
# ---

# %%
#|default_exp _dir_server

# %%
#|hide
from nblite import nbl_export; nbl_export();

# %% [markdown]
# # Filtered directory HTTP server
#
# Standalone HTTP server used by `tunnel open --serve <dir>`. Supports include
# and exclude glob filters that match either the path-relative-to-root or any
# individual path component (so `--exclude node_modules` hides the whole
# subtree). Invoked via `python -m appgarden._dir_server <port> <root> <inc-json> <exc-json>`.

# %%
#|export
import fnmatch
import html
import http.server
import io
import json
import os
import sys
import urllib.parse

# %%
#|export
def _matches(rel: str, patterns: list[str]) -> bool:
    """True if any pattern matches the relative path or any of its components."""
    if not patterns:
        return False
    components = [c for c in rel.split(os.sep) if c]
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        for c in components:
            if fnmatch.fnmatch(c, pat):
                return True
    return False

# %%
#|export
def _allowed(abs_path: str, root: str, includes: list[str], excludes: list[str], is_dir: bool) -> bool:
    """Decide whether to expose ``abs_path``. Directories ignore include filters so the
    tree remains traversable; both files and directories honour exclude filters."""
    rel = os.path.relpath(abs_path, root)
    if rel == ".":
        return True
    if _matches(rel, excludes):
        return False
    if includes and not is_dir and not _matches(rel, includes):
        return False
    return True

# %%
#|export
def _make_handler(root: str, includes: list[str], excludes: list[str]):
    class FilteredHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

        def log_message(self, *a, **k):
            pass

        def list_directory(self, path):
            try:
                names = os.listdir(path)
            except OSError:
                self.send_error(404, "No permission to list directory")
                return None
            visible = []
            for name in names:
                full = os.path.join(path, name)
                if _allowed(full, root, includes, excludes, is_dir=os.path.isdir(full)):
                    visible.append(name)
            visible.sort(key=str.lower)

            try:
                displaypath = urllib.parse.unquote(self.path, errors="surrogatepass")
            except UnicodeDecodeError:
                displaypath = urllib.parse.unquote(self.path)
            displaypath = html.escape(displaypath, quote=False)

            parts = [
                '<!DOCTYPE html><html><head><meta charset="utf-8">',
                f"<title>Index of {displaypath}</title></head>",
                f"<body><h1>Index of {displaypath}</h1><hr><ul>",
            ]
            if displaypath not in ("/", ""):
                parts.append('<li><a href="../">../</a></li>')
            for name in visible:
                full = os.path.join(path, name)
                disp = name + ("/" if os.path.isdir(full) else "")
                parts.append(
                    f'<li><a href="{urllib.parse.quote(disp)}">{html.escape(disp)}</a></li>'
                )
            parts.append("</ul><hr></body></html>")
            body = "\n".join(parts).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

        def send_head(self):
            path = self.translate_path(self.path)
            if os.path.exists(path):
                is_dir = os.path.isdir(path)
                if not _allowed(path, root, includes, excludes, is_dir=is_dir):
                    self.send_error(404)
                    return None
            return super().send_head()

    return FilteredHandler

# %%
#|export
def main() -> None:
    port = int(sys.argv[1])
    root = sys.argv[2]
    includes = json.loads(sys.argv[3])
    excludes = json.loads(sys.argv[4])
    handler = _make_handler(root, includes, excludes)
    http.server.HTTPServer(("127.0.0.1", port), handler).serve_forever()

# %%
#|export
if __name__ == "__main__":
    main()
