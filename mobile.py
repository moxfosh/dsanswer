"""Read-only LAN viewer for the latest answer."""
import ipaddress
import json
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

VIEWER_PORT = 43821

PAGE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>手机接收 · 截图答题</title>
<style>
*{box-sizing:border-box} body{margin:0;background:#f4f6f5;color:#142720;font-family:Inter,system-ui,sans-serif}
main{max-width:44rem;margin:0 auto;padding:2rem 1rem} header{display:flex;align-items:baseline;justify-content:space-between;gap:1rem}
h1{font-size:1.4rem;margin:0} #status{font-size:.875rem;color:#386153}
article{background:white;border:1px solid #cedbd5;border-radius:.8rem;margin-top:1.25rem;padding:1.25rem;
font-size:1.35rem;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere} article:empty{display:none}
</style>
<main><header><h1>最新答案</h1><span id="status" role="status">连接中…</span></header>
<article id="answer" aria-live="polite"></article></main>
<script>
const token = location.hash.slice(1);
const statusLine = document.querySelector('#status'), answer = document.querySelector('#answer');
const labels = {waiting:'等待电脑截图', busy:'正在答题…', ready:'已同步', error:'本次答题失败'};
async function sync() {
  if (!token) { statusLine.textContent = '请扫描电脑上的二维码'; return; }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch('/answer', {headers:{'X-Pairing-Key':token}, cache:'no-store', signal:controller.signal});
    if (response.status === 403) { statusLine.textContent = '请重新扫码'; answer.textContent = ''; return; }
    if (!response.ok) throw Error();
    const data = await response.json();
    statusLine.textContent = labels[data.state];
    if (answer.textContent !== data.answer) answer.textContent = data.answer;
  } catch { statusLine.textContent = '连接中断，正在重连…'; }
  finally { clearTimeout(timeout); }
  setTimeout(sync, 1000);
}
sync();
</script></html>""".encode("utf-8")


def local_addresses():
    records = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    addresses = dict.fromkeys(record[4][0] for record in records)
    return [addr for addr in addresses if not ipaddress.ip_address(addr).is_loopback]


class ViewerRequest(BaseHTTPRequestHandler):
    timeout = 8

    def do_GET(self):
        if self.path == "/":
            body, mime, status = PAGE, "text/html; charset=utf-8", 200
        elif self.path == "/answer":
            key = self.headers.get("X-Pairing-Key", "")
            if not key.isascii() or not secrets.compare_digest(key, self.server.token):
                body, mime, status = b"", "text/plain", 403
            else:
                body, mime, status = self.server.payload, "application/json; charset=utf-8", 200
        else:
            body, mime, status = b"", "text/plain", 404
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class MobileViewer(ThreadingHTTPServer):
    request_queue_size = 64

    def __init__(self, address, state, answer):
        try:
            super().__init__((address, VIEWER_PORT), ViewerRequest)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048:
                raise OSError(f"手机接收端口 {VIEWER_PORT} 已被占用，请先关闭其他正在共享的实例。") from exc
            raise
        self.token = secrets.token_urlsafe(18)
        self.url = f"http://{address}:{self.server_port}/#{self.token}"
        self.publish(state, answer)
        Thread(target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def publish(self, state, answer):
        self.payload = json.dumps({"state": state, "answer": answer}, ensure_ascii=False).encode("utf-8")

    def stop(self):
        self.shutdown()
        self.server_close()
