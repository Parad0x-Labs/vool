"""Real local dispatchers, isolated profiles and Chromium evidence for cosmetics."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


def finish_browser(manager, browser):
    """Stop this test's driver, which owns and closes every browser it launched.

    A Browser.close request over the closing browser's own session can lose its
    acknowledgement. Stopping the owning driver closes its transport directly.
    """
    manager.stop()


@pytest.fixture
def served(tmp_path, monkeypatch):
    from core import runtime_continuity, runtime_paths
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from storage import db

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    monkeypatch.setenv("VOOL_UPDATE_DISABLED", "1")
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None)
    monkeypatch.setattr(runtime_continuity, "_DB_PATH_OVERRIDE", None)
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH_OVERRIDE", None)
    from storage.migrations import run_migrations

    run_migrations()
    runtime = RuntimeServices(display_name="VOOL")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, response):
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Connection", "close")
            self.close_connection = True
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self):
            url = urlsplit(self.path)
            self.send(dispatch_get(path=url.path, query=parse_qs(url.query), headers=dict(self.headers),
                                   runtime=runtime, model_name="local", client_host="127.0.0.1"))

        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(size)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {}
            self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                                    runtime=runtime, model_name="local", client_host="127.0.0.1",
                                    workspace_root_provider=lambda: str(tmp_path / "workspace")))

    class LocalPage(str):
        pass

    class RestartableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = False

    def start(port):
        server = RestartableServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    server, thread = start(0)
    address = LocalPage(f"http://127.0.0.1:{server.server_port}")

    def restart():
        nonlocal runtime, server, thread
        port = server.server_port
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        runtime.shutdown()
        runtime = RuntimeServices(display_name="VOOL")
        server, thread = start(port)

    address.restart = restart
    try:
        yield address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        runtime.shutdown()


def network_guard(page, base):
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    return lambda: [url for url in requests if not url.startswith((base + "/", "data:", "blob:"))]


def shoot(page, name):
    folder = Path(tempfile.gettempdir()) / "shots"
    folder.mkdir(exist_ok=True)
    page.screenshot(path=str(folder / (name + ".png")))


def trusted_snapshot(page):
    from core.toybox_fragment import TRUSTED

    return page.locator(TRUSTED).evaluate_all("""elements=>elements.map(el=>{
      const s=getComputedStyle(el),r=el.getBoundingClientRect();
      return {id:el.id,cls:el.className,color:s.color,background:s.backgroundColor,
       border:s.borderColor,font:s.fontFamily,opacity:s.opacity,visibility:s.visibility,
       z:s.zIndex,box:[r.x,r.y,r.width,r.height]};})""")


def contrast_report(page):
    return page.evaluate("""()=>{
 const rgb=s=>(s.match(/[\\d.]+/g)||[]).map(Number), blend=(a,b)=>a.slice(0,3).map((v,i)=>v*(a[3]??1)+b[i]*(1-(a[3]??1)));
 const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
 const ratio=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
 const failures=[],walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
 while(walk.nextNode()){
  const text=walk.currentNode,el=text.parentElement;if(!text.textContent.trim()||!el||['SCRIPT','STYLE','NOSCRIPT'].includes(el.tagName))continue;
  const rect=el.getBoundingClientRect(),s=getComputedStyle(el);
  if(!rect.width||!rect.height||s.visibility==='hidden'||rect.bottom<0||rect.top>innerHeight||rect.left>innerWidth||rect.right<0)continue;
  let chain=[],p=el;while(p){chain.push(getComputedStyle(p));p=p.parentElement;}
  if(chain.some(s=>s.display==='none'||s.opacity==='0'))continue;
  let bg=[255,255,255],unplated=false;for(const style of chain.reverse()){const c=rgb(style.backgroundColor);if((c[3]??1)===1)unplated=false;bg=blend(c,bg);if(style.backgroundImage!=='none')unplated=true;}
  if(unplated){failures.push({text:text.textContent.trim().slice(0,60),tag:el.tagName,id:el.id,cls:el.className,kind:'image_needs_opaque_plate'});continue;}
  const fg=blend(rgb(s.color),bg),r=ratio(fg,bg);
  if(r<4.5)failures.push({text:text.textContent.trim().slice(0,60),tag:el.tagName,id:el.id,cls:el.className,ratio:r,fg:s.color,bg});
 }
 return failures;
}""")


def motion_report(page):
    return page.evaluate("""()=>document.getAnimations().filter(a=>a.playState==='running'&&a.effect.target.closest('#main,#side,#sidebar,.tb-root')).map(a=>a.animationName)""")


def frame_budget(page, seconds=1):
    client = page.context.new_cdp_session(page)
    client.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    client.send("Performance.enable")
    before = {x["name"]: x["value"] for x in client.send("Performance.getMetrics")["metrics"]}
    deltas = page.evaluate("""ms=>new Promise(resolve=>{const d=[],start=performance.now();let last=start;
      function frame(t){d.push(t-last);last=t;if(t-start<ms)requestAnimationFrame(frame);else resolve(d);}requestAnimationFrame(frame);})""", seconds * 1000)
    after = {x["name"]: x["value"] for x in client.send("Performance.getMetrics")["metrics"]}
    client.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    client.detach()
    values = sorted(deltas[2:])
    return {"p95_ms": values[int(len(values) * .95)], "task_seconds": after["TaskDuration"] - before["TaskDuration"]}
