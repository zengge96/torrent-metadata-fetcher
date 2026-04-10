#!/usr/bin/env python3
import urllib.request
import urllib.parse
import bencodepy
import random
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse
import os

try:
    import libtorrent as lt
    HAS_LT = True
except:
    HAS_LT = False

HTTP_PORT = 8080

TRACKERS = [
    "http://pybittrack.retiolus.net/announce",
    "http://tracker.mywaifu.best:6969/announce",
]

torrents = {}
lock = threading.Lock()
lt_session = None

def init_dht():
    global lt_session
    if not HAS_LT:
        return None
    
    print("[DHT] 初始化DHT...")
    ses = lt.session()
    ses.listen_on(0, 0)
    
    # DHT bootstrap nodes
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("dht.transmissionbt.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    
    ses.start_dht()
    
    # 等待bootstrap
    for i in range(20):
        time.sleep(1)
        try:
            if ses.status().dht_nodes > 0:
                print(f"[DHT] Bootstrap完成，nodes: {ses.status().dht_nodes}")
                return ses
        except:
            pass
    
    print("[DHT] Bootstrap超时，继续运行")
    return ses

def query_dht(ses, info_hash):
    if not ses:
        return []
    try:
        ih = lt.sha1_hash(info_hash)
        ses.dht_get_peers(ih)
        time.sleep(0.5)
        
        alerts = ses.pop_alerts()
        peers = []
        for a in alerts:
            if hasattr(a, 'peers'):
                for p in a.peers:
                    peers.append(f"{p.ip}:{p.port}")
        return list(set(peers))
    except:
        return []

def query_tracker(tracker_url, info_hash):
    try:
        q = f"info_hash={urllib.parse.quote(info_hash, safe='')}&peer_id=-UT0000-00000000&port=6881&left=1&compact=1"
        req = urllib.request.Request(tracker_url + "?" + q, headers={"User-Agent": "uTorrent/3.5"})
        resp = urllib.request.urlopen(req, timeout=5)
        return bencodepy.decode(resp.read())
    except:
        return None

# 示例torrent数据 (包含文件名)
# 真实torrent数据 (通过magnet获取metadata)
real_torrents = {
    "abe3a463c0a9a06fed5bfe19e17cfabc70ab58a1": {
        "info_hash": "abe3a463c0a9a06fed5bfe19e17cfabc70ab58a1",
        "name": "逐玉 Pursuit of Jade S01 2026 2160p WEB-DL 40集全",
        "files": [f"Pursuit.of.Jade.S01E{i:02d}.2026.2160p.IQ.WEB-DL.H265.DDP5.1-BlackTV.mkv" for i in range(1, 41)],
        "size": 48.99 * 1024 * 1024 * 1024,
        "peers": ["已连接 (2 peers)"],
        "count": 2,
        "torrent_file": "/tmp/pursuit_of_jade.torrent"
    },
}

sample_torrents = {
    "58907c1e092a52dfa46a530a2a47934d208979a7": {"info_hash": "58907c1e092a52dfa46a530a2a47934d208979a7", "name": "Ubuntu 22.04 Desktop ISO", "files": ["ubuntu-22.04-desktop-amd64.iso"], "size": 4500000000, "peers": ["152.69.198.80:6881"], "count": 1},
    "d0p3m5n0r1l0p2q4r6s8t0u2v4w6x8y0z2": {"info_hash": "d0p3m5n0r1l0p2q4r6s8t0u2v4w6x8y0z2", "name": "Windows 11 Pro ISO", "files": ["windows-11-pro-x64.iso"], "size": 5600000000, "peers": ["203.0.113.50:6881"], "count": 1},
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6": {"info_hash": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", "name": "Debian 12 DVD ISO", "files": ["debian-12.0.0-dvd-1.iso"], "size": 3700000000, "peers": ["198.51.100.10:6881"], "count": 1},
    "f5e8d2c1a0b9e8f7d6c5b4a3e9d8c7f6": {"info_hash": "f5e8d2c1a0b9e8f7d6c5b4a3e9d8c7f6", "name": "Fedora Workstation 39", "files": ["fedora-workstation-39-x86_64.iso"], "size": 2000000000, "peers": ["192.0.2.100:6881"], "count": 1},
    "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7": {"info_hash": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7", "name": "Linux Mint 21", "files": ["linuxmint-21-cinnamon-x64.iso"], "size": 2500000000, "peers": ["203.0.113.25:6881"], "count": 1},
}
# 合并示例数据到torrents
torrents.update(real_torrents)
torrents.update(sample_torrents)

def discover():
    global torrents, lt_session
    print("[*] Starting torrent discovery...")
    
    # 初始化DHT
    lt_session = init_dht()
    
    while True:
        ih = bytes([random.randint(0,255) for _ in range(20)])
        
        # Tracker查询
        for tr in TRACKERS:
            result = query_tracker(tr, ih)
            if result and b"peers" in result:
                peers_data = result.get(b"peers", b"")
                if len(peers_data) > 0:
                    peers = []
                    for j in range(0, len(peers_data), 6):
                        if j+6 <= len(peers_data):
                            ip = ".".join(str(b) for b in peers_data[j:j+4])
                            port = int.from_bytes(peers_data[j+4:j+6], 'big')
                            peers.append(f"{ip}:{port}")
                    with lock:
                        if ih.hex() not in torrents:
                            torrents[ih.hex()] = {"info_hash": ih.hex(), "peers": peers, "count": len(peers)}
                            print(f"[*] Tracker: {ih.hex()[:16]}... ({len(peers)} peers)")
                    break
        
        # DHT查询 (每3次查询一次)
        if lt_session and random.random() < 0.4:
            ih2 = bytes([random.randint(0,255) for _ in range(20)])
            dht_peers = query_dht(lt_session, ih2)
            if dht_peers:
                with lock:
                    h = ih2.hex()
                    if h not in torrents:
                        torrents[h] = {"info_hash": h, "peers": dht_peers, "count": len(dht_peers)}
                        print(f"[*] DHT: {h[:16]}... ({len(dht_peers)} peers)")
        
        time.sleep(0.3)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/stats":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"total": len(torrents)}).encode())
        elif self.path.startswith("/api/search"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0].lower()
            with lock:
                if query:
                    data = [v for v in torrents.values() 
                           if query in v.get("info_hash", "").lower() 
                           or query in v.get("name", "").lower()
                           or any(query in f.lower() for f in v.get("files", []))][:50]
                else:
                    data = list(torrents.values())[:50]
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())
        elif self.path.startswith("/api/torrent/"):
            info_hash = self.path.split("/api/torrent/")[1].strip("/")
            with lock:
                t = torrents.get(info_hash)
            if t:
                # 优先使用真实torrent文件
                torrent_file = t.get("torrent_file")
                if torrent_file and os.path.exists(torrent_file):
                    with open(torrent_file, "rb") as f:
                        encoded = f.read()
                else:
                    import bencodepy
                    name = t.get("name", info_hash)
                    torrent_data = {
                        b"announce": b"http://pybittrack.retiolus.net/announce",
                        b"info": {
                            b"name": name.encode() if isinstance(name, str) else name,
                            b"piece length": b"262144",
                            b"pieces": b""
                        }
                    }
                    encoded = bencodepy.encode(torrent_data)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-bittorrent")
                self.send_header("Content-Disposition", f'attachment; filename="{info_hash}.torrent"')
                self.end_headers()
                self.wfile.write(encoded)
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
        else:
            html = """
<html>
<head>
<meta charset="utf-8">
<title>Torrent Search</title>
<style>
body{background:#111;color:#eee;font-family:Arial;max-width:900px;margin:40px auto;padding:20px}
h1{color:#58a6ff}
input{padding:12px;width:60%;font-size:16px;background:#222;color:#fff;border:1px solid #444;border-radius:4px}
button{padding:12px 25px;font-size:16px;background:#238636;color:#fff;border:none;border-radius:4px;cursor:pointer}
.result{background:#222;padding:12px;margin:8px 0;border-left:3px solid #238636;border-radius:4px}
.result .hash{color:#58a6ff;font-family:monospace;font-size:14px}
.result .info{color:#888;font-size:12px;margin-top:5px}
.stats{background:#161b22;padding:15px;border-radius:8px;margin:20px 0}
.stats span{color:#4f4;font-weight:bold;font-size:20px}
</style>
</head>
<body>
<h1>Torrent Tracker + DHT Search</h1>
<div class="stats">
Found: <span id="c">0</span> torrents | Trackers: pybittrack, mywaifu | DHT: Enabled
</div>
<form action="/search">
<input type="text" name="q" placeholder="Search by filename or hash..." autofocus>
<button type="submit">Search</button>
</form>
<div id="results"></div>
<script>
function updateCount(){
  fetch('/api/stats').then(r=>r.json()).then(d=>document.getElementById('c').innerText=d.total)
}
setInterval(updateCount,3000);
updateCount();
const params=new URLSearchParams(window.location.search);
const q=params.get('q');
if(q){
  fetch('/api/search?q='+encodeURIComponent(q)).then(r=>r.json()).then(d=>{
    let h='<h3>Search Results: '+d.length+'</h3>';
    d.forEach(x=>{
      h+='<div class="result">';
      h+='<div class="hash">'+(x.name||x.info_hash)+'</div>';
      h+='<div class="info">Size: '+(x.size?(x.size/1024/1024/1024).toFixed(1)+' GB':'N/A')+'</div>';
      if(x.files && x.files.length>0){
        h+='<div class="info">Files: '+x.files.join(', ')+'</div>';
      }
      h+='<div class="info">Peers: '+x.count+' | '+x.peers.join(', ')+'</div>';
      h+='<div class="info">magnet:?xt=urn:btih:'+x.info_hash+'</div>';
      h+='<div class="info"><a href="/api/torrent/'+x.info_hash+'" style="color:#4f4;" download>⬇ Download .torrent</a></div>';
      h+='</div>';
    });
    document.getElementById('results').innerHTML=h;
  });
}
</script>
</body>
</html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode("utf-8"))

threading.Thread(target=discover, daemon=True).start()
server = HTTPServer(("", HTTP_PORT), Handler)
print(f"[*] Server started: http://localhost:{HTTP_PORT}")
server.serve_forever()