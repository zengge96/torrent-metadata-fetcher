#!/usr/bin/env python3
"""
DHT Crawler + Metadata Search Server
仅供学习研究使用
"""

import asyncio
import socket
import struct
import hashlib
import threading
import json
import time
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler
import bencodepy

# 配置
DHT_PORT = 6881
HTTP_PORT = 8080
BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.libtorrent.org", 6881),
]

# 存储收集到的 metadata
torrent_db = {}
infohash_set = set()


class DHTNode:
    def __init__(self, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", port))
        self.sock.setblocking(False)
        self.running = True
        self.my_id = hashlib.sha256(str(time.time()).encode()).digest()[:20]
        
    def send(self, addr, msg):
        try:
            self.sock.sendto(bencodepy.encode(msg), addr)
        except:
            pass
    
    def recv(self):
        try:
            data, addr = self.sock.recvfrom(65535)
            return addr, bencodepy.decode(data)
        except:
            return None, None
    
    def ping(self, addr, tid):
        return {
            b"t": tid,
            b"a": {b"id": self.my_id},
            b"q": b"ping",
            b"y": b"q"
        }
    
    def get_peers(self, addr, info_hash, tid):
        return {
            b"t": tid,
            b"a": {b"id": self.my_id, b"info_hash": info_hash},
            b"q": b"get_peers",
            b"y": b"q"
        }
    
    def announce_peer(self, addr, info_hash, tid):
        return {
            b"t": tid,
            b"a": {b"id": self.my_id, b"info_hash": info_hash, b"port": DHT_PORT, b"token": b"test"},
            b"q": b"announce_peer",
            b"y": b"q"
        }


class MetadataFetcher:
    """从 peers 获取 torrent metadata"""
    
    def __init__(self):
        self.pending = {}
        
    async def fetch_metadata(self, info_hash, peer_addr):
        """尝试从 peer 获取 metadata"""
        # 这里需要实现 BEP-9 (ut_metadata) 或 BEP-5
        # 简化版本：直接返回 info_hash 作为标识
        return None


def generate_transaction_id():
    return struct.pack(">H", int(time.time() * 1000) & 0xFFFF)


async def dht_worker(dht_node):
    """DHT 网络监听"""
    print(f"[DHT] 启动监听端口 {DHT_PORT}")
    print(f"[DHT] 连接 bootstrap 节点...")
    
    # 连接 bootstrap 节点
    for node in BOOTSTRAP_NODES:
        try:
            tid = generate_transaction_id()
            msg = dht_node.ping(node, tid)
            dht_node.send(node, msg)
            print(f"[DHT] -> {node}")
        except:
            pass
    
    print(f"[DHT] 开始监听网络...")
    while dht_node.running:
        addr, msg = dht_node.recv()
        if msg:
            try:
                # 处理响应
                if msg.get(b"y") == b"r":
                    # 收到响应，可能是 get_peers 的回复
                    if b"values" in msg.get(b"r", {}):
                        peers = msg[b"r"][b"values"]
                        info_hash = msg[b"r"].get(b"info_hash")
                        if info_hash:
                            info_hash_str = info_hash.hex()
                            if info_hash_str not in infohash_set:
                                infohash_set.add(info_hash_str)
                                # 模拟 metadata（真实需要从 peer 获取）
                                torrent_db[info_hash_str] = {
                                    "info_hash": info_hash_str,
                                    "found_at": time.time(),
                                    "files": [],
                                    "name": "unknown"
                                }
                                if len(torrent_db) % 100 == 0:
                                    print(f"[DHT] 已收集: {len(torrent_db)} 个 torrent")
                    
                    # 处理 ping 响应
                    if b"id" in msg.get(b"r", {}):
                        print(f"[DHT] 节点响应: {addr}")
                        
                # 处理查询
                elif msg.get(b"y") == b"q":
                    q = msg.get(b"q", b"").decode()
                    tid = msg.get(b"t", b"")
                    if q == "get_peers":
                        # 收到 get_peers 查询，随机返回一些 peer
                        info_hash = msg.get(b"a", {}).get(b"info_hash", b"")
                        if info_hash:
                            # 随机返回一些假 peer（简化版）
                            pass
                    elif q == "ping":
                        # 回复 ping
                        resp = {
                            b"t": tid,
                            b"r": {b"id": dht_node.my_id},
                            b"y": b"r"
                        }
                        dht_node.send(addr, resp)
            except Exception as e:
                pass
        
        # 定期向 bootstrap 节点查询，随机 infohash
        if int(time.time()) % 10 == 0:
            for node in BOOTSTRAP_NODES:
                try:
                    # 随机 infohash
                    random_hash = hashlib.sha256(str(time.time()).encode()).digest()[:20]
                    tid = generate_transaction_id()
                    msg = dht_node.get_peers(node, random_hash, tid)
                    dht_node.send(node, msg)
                except:
                    pass
        
        await asyncio.sleep(0.01)


class SearchHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.strip("/")
        
        if path == "" or path == "index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>DHT Torrent Search</title>
    <style>
        body { font-family: Arial; padding: 20px; max-width: 800px; margin: 0 auto; }
        input { padding: 10px; width: 70%; font-size: 16px; }
        button { padding: 10px 20px; font-size: 16px; }
        pre { background: #f5f5f5; padding: 10px; overflow: auto; }
    </style>
</head>
<body>
    <h1>🔍 DHT Torrent Search</h1>
    <p>收集了 <span id="count">0</span> 个 torrent</p>
    <form action="/search" method="GET">
        <input type="text" name="q" placeholder="搜索文件名...">
        <button type="submit">搜索</button>
    </form>
    <hr>
    <pre id="results"></pre>
    <script>
        fetch('/stats').then(r=>r.json()).then(d=>{
            document.getElementById('count').innerText = d.count;
        });
    </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
            
        elif path == "stats":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "count": len(torrent_db),
                "db_size": len(torrent_db)
            }).encode())
            
        elif path.startswith("search?"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get("q", [""])[0]
            
            results = []
            if query:
                query_lower = query.lower()
                for h, data in torrent_db.items():
                    name = data.get("name", "").lower()
                    if query_lower in name or not query:
                        results.append(data)
            else:
                results = list(torrent_db.values())[:50]
            
            self.wfile.write(json.dumps(results[:100], ensure_ascii=False).encode())
            
        else:
            self.send_response(404)
            self.end_headers()


def run_http_server():
    server = HTTPServer(("", HTTP_PORT), SearchHandler)
    print(f"[HTTP] 服务启动: http://localhost:{HTTP_PORT}")
    server.serve_forever()


async def main():
    dht_node = DHTNode(DHT_PORT)
    
    # 启动 HTTP 服务
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()
    
    # 启动 DHT 监听
    await dht_worker(dht_node)


if __name__ == "__main__":
    print("=" * 50)
    print("DHT Torrent Metadata Crawler + Search Server")
    print("=" * 50)
    print(f"注意: 需要安装 bencodepy: pip install bencodepy")
    print()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n停止服务")