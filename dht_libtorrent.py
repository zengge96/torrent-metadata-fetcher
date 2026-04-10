#!/usr/bin/env python3
"""
基于 libtorrent 的完整版 DHT 爬虫 + 搜索服务
支持 BEP-5 DHT + BEP-9 ut_metadata
"""

import libtorrent as lt
import asyncio
import threading
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote, parse_qs, urlparse

# ============== 配置 ==============
HTTP_PORT = 8080
DHT_PORT = 6881
MAX_TORRENTS = 100000

# ============== 全局状态 ==============
torrent_db = {}  # {info_hash: {...}}
infohash_set = set()
session = None
alert_queue = None


def init_dht():
    """初始化 libtorrent DHT"""
    global session, alert_queue
    
    # 创建 session
    ses = lt.session()
    ses.listen_on(DHT_PORT, DHT_PORT)
    
    # 启用 DHT
    ses.start_dht()
    ses.add_dht_router("router.bittorrent.com", 6881)
    ses.add_dht_router("dht.transmissionbt.com", 6881)
    ses.add_dht_router("router.utorrent.com", 6881)
    ses.add_dht_router("dht.libtorrent.org", 6881)
    
    session = ses
    print(f"[DHT] 启动成功，监听端口 {DHT_PORT}")
    return ses


def process_alerts():
    """处理 DHT alerts"""
    global torrent_db, infohash_set
    
    while True:
        try:
            # 获取 alerts
            alerts = session.pop_alerts()
            for alert in alerts:
                alert_type = type(alert).__name__
                
                # DHT 找到 peer
                if alert_type == "dht_get_peers_reply_alert":
                    info_hash = str(alert.info_hash)
                    if info_hash not in infohash_set:
                        infohash_set.add(info_hash)
                        torrent_db[info_hash] = {
                            "info_hash": info_hash,
                            "name": "unknown",
                            "files": [],
                            "size": 0,
                            "found_at": time.time(),
                            "peer_count": 0
                        }
                        
                # DHT 节点回复
                elif alert_type == "dht_bootstrap_alert":
                    print(f"[DHT] .bootstrap: {alert.message()}")
                    
                # torrent 元数据收到
                elif alert_type == "torrent_metadata_alert":
                    try:
                        info_hash = str(alert.info_hash)
                        if info_hash in torrent_db:
                            tor = alert.handle
                            if tor.is_valid():
                                torrent = tor.get_torrent_info()
                                if torrent:
                                    name = torrent.name()
                                    size = torrent.total_size()
                                    files = []
                                    for i in range(torrent.num_files()):
                                        f = torrent.files().at(i)
                                        files.append({
                                            "path": f.path,
                                            "size": f.size
                                        })
                                    torrent_db[info_hash].update({
                                        "name": name,
                                        "size": size,
                                        "files": files[:100]  # 限制文件数
                                    })
                    except:
                        pass
                        
        except Exception as e:
            pass
        time.sleep(0.1)


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
    <title>DHT Torrent Search - Powered by libtorrent</title>
    <style>
        body { font-family: 'Segoe UI', Arial; max-width: 900px; margin: 0 auto; padding: 20px; background: #0d1117; color: #c9d1d9; }
        h1 { color: #58a6ff; }
        .stats { background: #161b22; padding: 15px; border-radius: 8px; margin: 20px 0; border: 1px solid #30363d; }
        .stats span { margin-right: 30px; color: #58a6ff; font-weight: bold; }
        input { padding: 12px; width: 60%; font-size: 16px; border: none; border-radius: 5px; background: #161b22; color: #fff; border: 1px solid #30363d; }
        button { padding: 12px 25px; font-size: 16px; background: #238636; color: #fff; border: none; border-radius: 5px; cursor: pointer; }
        button:hover { background: #2ea043; }
        .results { margin-top: 20px; }
        .result-item { background: #161b22; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #238636; }
        .result-item .name { color: #58a6ff; font-size: 16px; font-weight: bold; }
        .result-item .info { color: #8b949e; font-size: 12px; margin-top: 5px; }
        .result-item .hash { color: #6e7681; font-family: monospace; font-size: 11px; }
        a { color: #58a6ff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <h1>🔍 DHT Torrent Search Engine</h1>
    <p style="color: #8b949e;">Powered by libtorrent (完整版 DHT 爬虫)</p>
    <div class="stats">
        <span>总收集: <span id="total">0</span></span>
        <span>已索引: <span id="indexed">0</span></span>
    </div>
    <form action="/search">
        <input type="text" name="q" placeholder="搜索文件名..." autofocus>
        <button type="submit">搜索</button>
    </form>
    <div class="results" id="results"></div>
    <script>
        fetch('/api/stats').then(r=>r.json()).then(d=>{
            document.getElementById('total').innerText = d.total;
            document.getElementById('indexed').innerText = d.indexed;
        });
        const params = new URLSearchParams(window.location.search);
        const q = params.get('q');
        if (q) {
            fetch('/api/search?q=' + encodeURIComponent(q)).then(r=>r.json()).then(d=>{
                let html = '';
                d.forEach(item=>{
                    html += '<div class="result-item">';
                    html += '<div class="name">' + item.name + '</div>';
                    html += '<div class="info">大小: ' + item.size_formatted + '</div>';
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
            indexed = sum(1 for v in torrent_db.values() if v.get("name") != "unknown")
            self.wfile.write(json.dumps({
                "total": len(torrent_db),
                "indexed": indexed
            }).encode())
            
        elif path.startswith("api/search"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            
            query = parse_qs(urlparse(path).query).get("q", [""])[0]
            
            results = []
            if query:
                query_lower = query.lower()
                for h, data in torrent_db.items():
                    name = data.get("name", "").lower()
                    if query_lower in name and name != "unknown":
                        size = data.get("size", 0)
                        if size > 1024*1024*1024:
                            size_fmt = f"{size/(1024*1024*1024):.2f} GB"
                        elif size > 1024*1024:
                            size_fmt = f"{size/(1024*1024):.2f} MB"
                        else:
                            size_fmt = f"{size/1024:.2f} KB"
                        results.append({
                            "info_hash": h,
                            "name": data.get("name", "Unknown"),
                            "size": size,
                            "size_formatted": size_fmt
                        })
            else:
                for h, data in list(torrent_db.items())[:50]:
                    if data.get("name") != "unknown":
                        size = data.get("size", 0)
                        if size > 1024*1024*1024:
                            size_fmt = f"{size/(1024*1024*1024):.2f} GB"
                        elif size > 1024*1024:
                            size_fmt = f"{size/(1024*1024):.2f} MB"
                        else:
                            size_fmt = f"{size/1024:.2f} KB"
                        results.append({
                            "info_hash": h,
                            "name": data.get("name", "Unknown"),
                            "size": size,
                            "size_formatted": size_fmt
                        })
            
            self.wfile.write(json.dumps(results[:100], ensure_ascii=False).encode())


def run_http():
    server = HTTPServer(("", HTTP_PORT), SearchHandler)
    print(f"[HTTP] 服务启动: http://localhost:{HTTP_PORT}")
    server.serve_forever()


def main():
    global session
    
    print("=" * 60)
    print("DHT Torrent Search Engine - libtorrent 完整版")
    print("=" * 60)
    print(f"DHT 端口: {DHT_PORT}")
    print(f"HTTP 端口: {HTTP_PORT}")
    print()
    
    # 初始化 DHT
    init_dht()
    
    # 启动 HTTP 服务器
    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()
    
    # 启动 alert 处理
    alert_thread = threading.Thread(target=process_alerts, daemon=True)
    alert_thread.start()
    
    print()
    print("服务运行中，按 Ctrl+C 停止")
    print()
    
    # 添加一些随机 torrent 来触发 DHT 搜索
    import hashlib
    last_status = time.time()
    while True:
        try:
            # 每10秒打印状态
            if time.time() - last_status > 10:
                indexed = sum(1 for v in torrent_db.values() if v.get("name") != "unknown")
                print(f"[状态] 收集: {len(torrent_db)} | 索引: {indexed}")
                last_status = time.time()
                
                # 添加随机查询来增加 DHT 活动
                random_hash = hashlib.sha256(str(time.time()).encode()).digest()
                ih = lt.info_hash_t(random_hash)
                session.dht_get_peers(ih)
                
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n停止服务")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()