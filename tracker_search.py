#!/usr/bin/env python3
"""
基于 HTTP Tracker 的 Torrent 搜索服务
使用可用的 tracker API 获取种子信息
"""

import urllib.request
import urllib.parse
import bencodepy
import random
import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

# ============== 配置 ==============
HTTP_PORT = 8080

# 可用的 tracker 列表 - 确认能用的
TRACKERS = [
    "https://tracker.zhuqiy.com:443/announce",
    "https://tracker.pmman.tech:443/announce",
    "https://tracker.nekomi.cn:443/announce",
]

# ============== 全局状态 ==============
torrent_db = {}  # {info_hash: {...}}
search_index = {}  # {filename: [info_hash, ...]}
lock = threading.Lock()

def query_tracker(tracker_url, info_hash):
    """查询tracker获取peer信息"""
    # 正确URL编码info_hash
    import urllib.parse
    params = {
        "info_hash": info_hash,
        "peer_id": "-UT0000-000000000000",
        "port": 6881,
        "uploaded": 0,
        "downloaded": 0,
        "left": 1,
        "compact": 1,
    }
    
    try:
        query = f"info_hash={urllib.parse.quote(info_hash, safe='')}&peer_id=-UT0000-000000000000&port=6881&left=1&compact=1"
        req = urllib.request.Request(f"{tracker_url}?{query}", 
                                     headers={"User-Agent": "uTorrent/3.5"},
                                     timeout=10)
        resp = urllib.request.urlopen(req, timeout=10)
        return bencodepy.decode(resp.read())
    except Exception as e:
        return None

def discover_torrents():
    """发现更多torrent"""
    global torrent_db, search_index
    
    print("[Tracker] 开始发现torrent...")
    
    # 随机生成info_hash查询
    for i in range(500):
        info_hash = bytes([random.randint(0,255) for _ in range(20)])
        info_hash_hex = info_hash.hex()
        
        for tracker in TRACKERS:
            result = query_tracker(tracker, info_hash)
            if result and b"peers" in result:
                peers_data = result.get(b"peers", b"")
                
                # 解析peers
                peers = []
                for j in range(0, len(peers_data), 6):
                    if j + 6 <= len(peers_data):
                        ip = ".".join(str(b) for b in peers_data[j:j+4])
                        port = int.from_bytes(peers_data[j+4:j+6], 'big')
                        peers.append(f"{ip}:{port}")
                
                if peers:
                    with lock:
                        if info_hash_hex not in torrent_db:
                            torrent_db[info_hash_hex] = {
                                "info_hash": info_hash_hex,
                                "name": f"torrent_{i}_{info_hash_hex[:8]}",
                                "peers": peers,
                                "found_at": time.time(),
                                "tracker": tracker
                            }
                    
                    print(f"[Tracker] 进度: {i}/500, 发现: {len(torrent_db)}")
                    break
        
        time.sleep(0.2)
    
    print(f"[Tracker] 完成! 共发现 {len(torrent_db)} 个torrent")

# ============== HTTP 服务器 ==============
class SearchHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
        
    def do_GET(self):
        global torrent_db
        
        path = self.path.strip("/")
        
        if not path or path == "" or path == "index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Torrent Tracker Search</title>
    <style>
        body { font-family: 'Segoe UI', Arial; max-width: 900px; margin: 0 auto; padding: 20px; background: #0d1117; color: #c9d1d9; }
        h1 { color: #58a6ff; }
        .stats { background: #161b22; padding: 15px; border-radius: 8px; margin: 20px 0; }
        .stats span { margin-right: 30px; color: #58a6ff; font-weight: bold; }
        input { padding: 12px; width: 60%; font-size: 16px; border-radius: 5px; background: #161b22; color: #fff; border: 1px solid #30363d; }
        button { padding: 12px 25px; font-size: 16px; background: #238636; color: #fff; border: none; border-radius: 5px; cursor: pointer; }
        .results { margin-top: 20px; }
        .result-item { background: #161b22; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #238636; }
        .result-item .name { color: #58a6ff; }
        .result-item .info { color: #8b949e; font-size: 12px; }
        .result-item .hash { color: #6e7681; font-family: monospace; font-size: 11px; }
    </style>
</head>
<body>
    <h1>🔍 Torrent Search (Tracker-based)</h1>
    <div class="stats">
        <span>已发现: <span id="total">0</span></span>
        <span>Tracker: 1337.abcvg.info (可用)</span>
    </div>
    <form action="/search">
        <input type="text" name="q" placeholder="搜索..." autofocus>
        <button type="submit">搜索</button>
    </form>
    <div class="results" id="results"></div>
    <script>
        fetch('/api/stats').then(r=>r.json()).then(d=>{
            document.getElementById('total').innerText = d.total;
        });
        const params = new URLSearchParams(window.location.search);
        const q = params.get('q');
        if (q) {
            fetch('/api/search?q=' + encodeURIComponent(q)).then(r=>r.json()).then(d=>{
                let html = '';
                d.forEach(item=>{
                    html += '<div class="result-item">';
                    html += '<div class="name">' + item.name + '</div>';
                    html += '<div class="info">Peers: ' + item.peers.length + ' | From: ' + item.tracker + '</div>';
                    html += '<div class="hash">magnet:?xt=urn:btih:' + item.info_hash + '</div>';
                    html += '</div>';
                });
                document.getElementById('results').innerHTML = html;
            });
        }
    </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
            
        elif path == "api/stats":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"total": len(torrent_db)}).encode())
            
        elif path.startswith("api/search"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get("q", [""])[0]
            
            results = []
            if query:
                query_lower = query.lower()
                for h, data in torrent_db.items():
                    if query_lower in data.get("name", "").lower():
                        results.append(data)
            else:
                results = list(torrent_db.values())[:50]
            
            self.wfile.write(json.dumps(results[:100], ensure_ascii=False).encode())


def run_http():
    server = HTTPServer(("", HTTP_PORT), SearchHandler)
    print(f"[HTTP] 服务启动: http://localhost:{HTTP_PORT}")
    server.serve_forever()


def main():
    print("=" * 60)
    print("Torrent Tracker Search Engine")
    print("=" * 60)
    print(f"HTTP 端口: {HTTP_PORT}")
    print(f"Tracker: {TRACKERS}")
    print()
    
    # 启动 HTTP 服务器
    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()
    
    # 启动torrent发现
    discover_thread = threading.Thread(target=discover_torrents, daemon=True)
    discover_thread.start()
    
    print("服务运行中...")
    
    while True:
        time.sleep(10)
        print(f"[状态] 已发现: {len(torrent_db)}")


if __name__ == "__main__":
    main()