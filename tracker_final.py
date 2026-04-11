#!/usr/bin/env python3
"""BitTorrent Tracker + DHT Spider"""
import urllib.request, urllib.parse, bencodepy, random, time, threading, socket, struct, os
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse

try:
    import libtorrent as lt
    HAS_LT = True
except:
    HAS_LT = False

HTTP_PORT = 8080
DHT_PORT = 6881
TRACKERS = ["http://pybittrack.retiolus.net/announce", "http://tracker.mywaifu.best:6969/announce"]
BOOTSTRAP = [("router.bittorrent.com", 6881), ("dht.transmissionbt.com", 6881), ("router.utorrent.com", 6881)]

torrents = {}
lock = threading.Lock()
lt_session = None

REAL_INFO_HASHES = []
try:
    with open("/root/.openclaw/workspace/real_info_hashes.json") as f:
        REAL_INFO_HASHES = json.load(f)
    print(f"[*] Loaded {len(REAL_INFO_HASHES)} real info_hashes")
except: pass

import bencodepy as bencoder

import sqlite3

# 数据库路径
DB_PATH = "/root/.openclaw/workspace/torrents.db"

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS torrents (info_hash TEXT PRIMARY KEY, name TEXT, magnet TEXT, filenames TEXT, size INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_filenames ON torrents(filenames)")
    conn.commit()
    conn.close()
    
def save_torrent(info_hash, name, filenames, size):
    """保存torrent到数据库"""
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    import json
    filenames_json = json.dumps(filenames, ensure_ascii=False)
    c.execute("INSERT OR REPLACE INTO torrents (info_hash, name, magnet, filenames, size) VALUES (?, ?, ?, ?, ?)", (info_hash, name, magnet, filenames_json, size))
    conn.commit()
    conn.close()
    
def search_by_filename(query):
    """按文件名搜索"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 同时搜索name和filenames字段
    c.execute("SELECT info_hash, name, magnet, filenames, size FROM torrents WHERE name LIKE ? OR filenames LIKE ?", 
              (f"%{query}%", f"%{query}%"))
    results = []
    import json
    for row in c.fetchall():
        results.append({"info_hash": row[0], "name": row[1], "magnet": row[2], "filenames": json.loads(row[3]), "size": row[4]})
    conn.close()
    return results

# 初始化数据库
init_db()


# ===== DHT Spider =====
class DHTSpider:
    def __init__(self, port=DHT_PORT):
        self.port = port
        self.nid = os.urandom(20)
        self.nodes = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1)
        self.running = False
        self.info_hashes = set()
        
    def send_krpc(self, msg, addr):
        try: self.sock.sendto(bencoder.encode(msg), addr)
        except: pass
        
    def send_find_node(self, addr, target=None):
        target = target or os.urandom(20)
        nid = target[:14] + os.urandom(6)
        msg = {b"t": os.urandom(2), b"y": b"q", b"q": b"find_node", 
               b"a": {b"id": nid, b"target": target}}
        self.send_krpc(msg, addr)
        
    def parse_nodes(self, data):
        result = []
        for i in range(0, len(data), 26):
            if i+26 <= len(data):
                nid = data[i:i+20]
                ip = ".".join(str(b) for b in data[i+20:i+24])
                port = struct.unpack("!H", data[i+24:i+26])[0]
                result.append((nid, ip, port))
        return result
        
    def handle_msg(self, msg, addr):
        try:
            if msg.get(b"y") == b"r":
                nodes = msg.get(b"r", {}).get(b"nodes", b"")
                if nodes:
                    self.nodes.extend(self.parse_nodes(nodes))
            elif msg.get(b"y") == b"q":
                q = msg.get(b"q", b"")
                if q == b"get_peers" or q == b"announce_peer":
                    ih = msg.get(b"a", {}).get(b"info_hash")
                    if ih:
                        ih_hex = ih.hex()
                        if ih_hex not in self.info_hashes:
                            self.info_hashes.add(ih_hex)
                            print(f"[DHT] Found: {ih_hex}")
                            with lock:
                                if ih_hex not in torrents:
                                    torrents[ih_hex] = {"info_hash": ih_hex, "peers": [], "count": 0}
                                    threading.Thread(target=self.get_meta, args=(ih_hex,), daemon=True).start()
        except: pass
        
    def get_meta(self, info_hash):
        if not HAS_LT: return
        try:
            h = lt_session.add_torrent({"info_hash": info_hash, "name": info_hash})
            for _ in range(30):
                time.sleep(1)
                if h.has_metadata():
                    ti = h.get_torrent_info()
                    with lock:
                        if info_hash in torrents:
                            torrents[info_hash]["name"] = ti.name()
                            torrents[info_hash]["size"] = ti.total_size()
                            torrents[info_hash]["has_metadata"] = True
                            print(f"[DHT] Metadata: {ti.name()[:40]}")
                    break
        except Exception as e: print(f"[DHT] Error: {e}")
        
    def run(self):
        try: self.sock.bind(("0.0.0.0", self.port))
        except: print("[DHT] Port busy"); return
        print(f"[DHT] Listening on {self.port}")
        self.running = True
        for a in BOOTSTRAP: self.send_find_node(a)
        last_bootstrap = time.time()
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                self.handle_msg(bencoder.decode(data), addr)
            except socket.timeout:
                if time.time() - last_bootstrap > 20:
                    for a in BOOTSTRAP: self.send_find_node(a)
                    last_bootstrap = time.time()
            except: pass

# ===== Original functions =====
def init_dht():
    global lt_session
    if not HAS_LT: return None
    print("[DHT] Init libtorrent...")
    s = lt.session()
    s.listen_on(0, 0)
    for a in BOOTSTRAP: s.add_dht_router(a[0], a[1])
    s.start_dht()
    for _ in range(10):
        time.sleep(1)
        if s.status().dht_nodes > 0:
            print(f"[DHT] Nodes: {s.status().dht_nodes}")
            break
    return s

def query_tracker(tr, ih):
    try:
        url = f"{tr}?info_hash={ih.hex()}&peer_id=00112233445566778899&port=6881&uploaded=0&downloaded=0&left=0"
        return bencoder.decode(urllib.request.urlopen(url, timeout=5).read())
    except: return None

def get_torrent_name(info_hash):
    """从REAL_INFO_HASHES获取torrent名称"""
    for rh in REAL_INFO_HASHES:
        if rh["info_hash"] == info_hash:
            return rh.get("name", "")[:50]
    return ""

def discover():
    global lt_session
    print("[*] Starting...")
    lt_session = init_dht()
    idx = 0
    while True:
        if REAL_INFO_HASHES:
            ih_hex = REAL_INFO_HASHES[idx % len(REAL_INFO_HASHES)]["info_hash"]
            ih = bytes.fromhex(ih_hex)
            idx += 1
        else:
            ih = bytes([random.randint(0,255) for _ in range(20)])
            ih_hex = ih.hex()
            
        for tr in TRACKERS:
            r = query_tracker(tr, ih)
            if r and b"peers" in r:
                peers_data = r.get(b"peers", b"")
                if len(peers_data) > 0:
                    peers = []
                    for j in range(0, len(peers_data), 6):
                        if j+6 <= len(peers_data):
                            ip = ".".join(str(b) for b in peers_data[j:j+4])
                            port = int.from_bytes(peers_data[j+4:j+6], 'big')
                            peers.append(f"{ip}:{port}")
                    
                    # 获取torrent名称
                    name = get_torrent_name(ih_hex)
                    
                    with lock:
                        if ih_hex not in torrents:
                            torrents[ih_hex] = {"info_hash": ih_hex, "peers": peers, "count": len(peers), "name": name}
                            print(f"[*] {ih_hex[:16]}... ({len(peers)} peers) - {name[:30]}")
        time.sleep(1)

class H(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/api/stats":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            with lock: self.wfile.write(json.dumps({"total": len(torrents)}).encode())
        elif self.path.startswith("/api/search"):
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            db_results = search_by_filename(q)
            print(f"[SEARCH] q={q}, db={len(db_results)}")
            if db_results:
                results = db_results
            else:
                with lock: results = [{"info_hash": k, **v} for k,v in torrents.items() if q in v.get("name","").lower() or q in k]
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(results).encode())
        elif self.command == "HEAD" and self.path.startswith("/api/torrent/"):
            info_hash = self.path.split("/api/torrent/")[1].split("?")[0]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bittorrent")
            self.send_header("Content-Disposition", f'attachment; filename="{info_hash}.torrent"')
            self.end_headers()
        elif self.path.startswith("/api/torrent/"):
            info_hash = self.path.split("/api/torrent/")[1].strip("/")
            with lock: t = torrents.get(info_hash)
            if t and t.get("torrent_file"):
                try:
                    with open(t["torrent_file"], "rb") as f: data = f.read()
                    self.send_response(200); self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f"attachment; filename={info_hash}.torrent")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except: pass
            self.send_response(404); self.end_headers()
        else:
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            html = '''<html><head><meta charset="utf-8"><title>DHT Tracker</title>
<style>body{background:#111;color:#eee;font-family:Arial;max-width:900px;margin:40px auto;padding:20px}
h1{color:#58a6ff}input{padding:12px;width:60%;font-size:16px;background:#222;color:#fff;border:1px solid #444}
button{padding:12px 25px;font-size:16px;background:#238636;color:#fff;border:none;cursor:pointer}
.result{background:#222;padding:12px;margin:8px 0;border-left:3px solid #238636}
.result .hash{color:#58a6ff;font-family:monospace}.result .info{color:#888;font-size:12px}
.stats{background:#161b22;padding:15px;border-radius:8px;margin:20px 0}.stats span{color:#4f;font-weight:bold;font-size:20px}
</style></head>
<body><h1>DHT爬虫 + Tracker</h1>
<div class="stats">Found: <span id="c">0</span> torrents</div>
<form action="/search"><input type="text" name="q" placeholder="Search by filename" autofocus><button>Search</button></form>
<div id="results"></div>
<script>
function updateCount(){fetch('/api/stats').then(r=>r.json()).then(d=>document.getElementById('c').innerText=d.total)}
setInterval(updateCount,3000);updateCount();
const params=new URLSearchParams(window.location.search);
const q=params.get('q');
if(q){
  fetch('/api/search?q='+encodeURIComponent(q)).then(r=>r.json()).then(d=>{
    let h='<h3>Results: '+d.length+'</h3>';
    d.forEach(x=>{
      h+='<div class="result">';
      h+='<div class="hash">'+(x.name||x.info_hash)+'</div>';
      h+='<div class="info">Size: '+(x.size?(x.size/1024/1024/1024).toFixed(1)+' GB':'N/A')+'</div>';
      let magnet = x.magnet || "magnet:?xt=urn:btih:"+x.info_hash;
      h+='<div class="info" style="word-break:break-all">'+magnet+'</div>';
      if(x.filenames){
        h+='<div class="info">Files: '+x.filenames.slice(0,5).join(", ")+(x.filenames.length>5?"...":"")+'</div>';
      }
      h+='<div class="info"><a href="/api/torrent/'+x.info_hash+'" style="color:#4f" download>Download .torrent</a></div>';
      h+='</div>';
    });
    document.getElementById('results').innerHTML=h;
  });
}
</script></body></html>'''
            self.wfile.write(html.encode())

if __name__ == "__main__":
    print("[*] Starting DHT Tracker...")
    threading.Thread(target=lambda: DHTSpider(DHT_PORT).run(), daemon=True).start()
    threading.Thread(target=discover, daemon=True).start()
    HTTPServer(("", HTTP_PORT), H).serve_forever()
