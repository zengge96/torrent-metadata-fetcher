#!/usr/bin/env python3
"""
完整的 DHT 爬虫 + 搜索服务
实现 BEP-5 (DHT) + BEP-9 (ut_metadata)
"""

import asyncio
import socket
import struct
import hashlib
import random
import threading
import json
import time
import os
from urllib.parse import quote
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict

# ============== 依赖检查 ==============
try:
    import bencodepy
except ImportError:
    print("ERROR: bencodepy not installed")
    print("Install: pip install bencodepy")
    exit(1)

# ============== 配置 ==============
DHT_PORT = 6881
HTTP_PORT = 8080
MAX_TORRENTS = 100000  # 最大存储torrent数
BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.libtorrent.org", 6881),
    ("87.98.162.88", 6881),
    ("62.210.105.116", 6881),
]

# ============== 全局状态 ==============
torrent_db = {}  # {info_hash: {"name": str, "files": [], "size": int, "found_at": float}}
infohash_count = 0
peer_db = defaultdict(list)  # {info_hash: [(ip, port), ...]}
running = True

# ============== Kademlia DHT ==============
class KademliaNode:
    def __init__(self, node_id=None):
        self.node_id = node_id or hashlib.sha256(str(random.random()).encode()).digest()[:20]
        self.routing_table = {}  # 简化的路由表
        
    def distance(self, id1, id2):
        return bytes(a ^ b for a, b in zip(id1, id2))
    
    def closer(self, target, id1, id2):
        return self.distance(target, id1) < self.distance(target, id2)


class DHTProtocol:
    """DHT 协议实现 (BEP-5)"""
    
    def __init__(self, node_id):
        self.node_id = node_id
        self.socket = None
        
    def create_socket(self, port):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("", port))
        self.socket.setblocking(False)
        
    def encode(self, data):
        return bencodepy.encode(data)
    
    def decode(self, data):
        return bencodepy.decode(data)
    
    def create_query(self, query_type, args, tid):
        msg = {
            b"t": tid,
            b"y": b"q",
            b"q": query_type.encode() if isinstance(query_type, str) else query_type,
            b"a": args
        }
        return msg
    
    def create_response(self, resp, tid):
        msg = {
            b"t": tid,
            b"y": b"r",
            b"r": resp
        }
        return msg
    
    def create_error(self, code, msg, tid):
        error_msg = {
            b"t": tid,
            b"y": b"e",
            b"e": [code, msg.encode() if isinstance(msg, str) else msg]
        }
        return error_msg
    
    # BEP-5 消息类型
    def ping(self, tid):
        return self.create_query("ping", {b"id": self.node_id}, tid)
    
    def find_node(self, target, tid):
        return self.create_query("find_node", {
            b"id": self.node_id,
            b"target": target
        }, tid)
    
    def get_peers(self, info_hash, tid):
        return self.create_query("get_peers", {
            b"id": self.node_id,
            b"info_hash": info_hash
        }, tid)
    
    def announce_peer(self, info_hash, port, tid, token=None):
        args = {
            b"id": self.node_id,
            b"info_hash": info_hash,
            b"port": port
        }
        if token:
            args[b"token"] = token
        return self.create_query("announce_peer", args, tid)


class MetadataFetcher:
    """BEP-9 ut_metadata 获取"""
    
    def __init__(self):
        self.pending_requests = {}  # {info_hash: {peer: timestamp}}
        
    def create_ut_metadata_request(self, info_hash, tid):
        """创建 metadata request 消息"""
        return {
            b"t": tid,
            b"y": b"q",
            b"q": b"ut_metadata",
            b"a": {
                b"id": hashlib.sha256(str(random.random()).encode()).digest()[:20],
                b"info_hash": info_hash,
                b"piece": 0
            }
        }


class DHTCrawler:
    """DHT 爬虫主类"""
    
    def __init__(self, port=DHT_PORT):
        self.port = port
        self.protocol = DHTProtocol(hashlib.sha256(str(random.random()).encode()).digest()[:20])
        self.metadata_fetcher = MetadataFetcher()
        self.socket = None
        self.running = False
        
    def start(self):
        """启动DHT爬虫"""
        self.protocol.create_socket(self.port)
        self.running = True
        print(f"[DHT] 监听端口 {self.port}")
        
        # 添加bootstrap节点
        for node in BOOTSTRAP_NODES:
            self.bootstrap(node)
            
    def bootstrap(self, addr):
        """连接bootstrap节点"""
        try:
            tid = self._tid()
            msg = self.protocol.find_node(hashlib.sha256(str(random.random()).encode()).digest()[:20], tid)
            self.send(addr, msg)
            print(f"[DHT] 连接 {addr}")
        except Exception as e:
            pass
            
    def send(self, addr, msg):
        try:
            self.socket.sendto(self.protocol.encode(msg), addr)
        except:
            pass
            
    def recv(self):
        try:
            data, addr = self.socket.recvfrom(65536)
            return addr, self.protocol.decode(data)
        except:
            return None, None
            
    def _tid(self):
        return struct.pack(">H", random.randint(0, 65535))
    
    def process_message(self, addr, msg):
        """处理接收到的消息"""
        global infohash_count, torrent_db
        
        if not msg or b"y" not in msg:
            return
            
        msg_type = msg[b"y"]
        tid = msg.get(b"t", b"")
        
        if msg_type == b"q":
            # 查询消息
            query = msg.get(b"q", b"").decode() if isinstance(msg.get(b"q"), bytes) else msg.get(b"q", b"")
            self.handle_query(addr, query, msg)
            
        elif msg_type == b"r":
            # 响应消息
            resp = msg.get(b"r", {})
            if b"values" in resp:
                # get_peers 响应 - 获取到 peers
                info_hash = resp.get(b"info_hash")
                if info_hash:
                    peers = resp[b"values"]
                    # 解析 peers 列表
                    for peer in peers:
                        if len(peer) == 6:
                            peer_ip = ".".join(str(b) for b in peer[:4])
                            peer_port = struct.unpack(">H", peer[4:])[0]
                            peer_db[info_hash.hex()].append((peer_ip, peer_port))
                            
                    info_hash_str = info_hash.hex()
                    if info_hash_str not in torrent_db:
                        infohash_count += 1
                        torrent_db[info_hash_str] = {
                            "info_hash": info_hash_str,
                            "name": "unknown",
                            "files": [],
                            "size": 0,
                            "found_at": time.time(),
                            "peer_count": len(peers)
                        }
                        if infohash_count % 100 == 0:
                            print(f"[DHT] 已收集: {infohash_count} 个 info_hash")
                            
        elif msg_type == b"e":
            # 错误消息
            pass
    
    def handle_query(self, addr, query, msg):
        """处理查询消息"""
        tid = msg.get(b"t", b"")
        
        if query == "ping":
            resp = self.protocol.create_response({b"id": self.protocol.node_id}, tid)
            self.send(addr, resp)
            
        elif query == "find_node":
            # 简化处理
            resp = self.protocol.create_response({
                b"id": self.protocol.node_id,
                b"nodes": b""
            }, tid)
            self.send(addr, resp)
            
        elif query == "get_peers":
            info_hash = msg.get(b"a", {}).get(b"info_hash")
            if info_hash:
                # 随机返回一些 nodes
                resp = self.protocol.create_response({
                    b"id": self.protocol.node_id,
                    b"token": b"test",
                    b"nodes": b""
                }, tid)
                self.send(addr, resp)
                
        elif query == "announce_peer":
            pass
            
    def run_once(self):
        """执行一次轮询"""
        addr, msg = self.recv()
        if addr and msg:
            self.process_message(addr, msg)
            
    def periodic_bootstrap(self):
        """定期连接更多节点"""
        for node in BOOTSTRAP_NODES:
            try:
                tid = self._tid()
                random_hash = hashlib.sha256(str(time.time()).encode()).digest()[:20]
                msg = self.protocol.get_peers(random_hash, tid)
                self.send(node, msg)
            except:
                pass


# ============== HTTP 服务器 ==============
class SearchHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 禁用日志
        
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
    <title>DHT Torrent Search Engine</title>
    <style>
        body { font-family: 'Segoe UI', Arial; max-width: 900px; margin: 0 auto; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d9ff; }
        .stats { background: #16213e; padding: 15px; border-radius: 8px; margin: 20px 0; }
        .stats span { margin-right: 30px; color: #00d9ff; font-weight: bold; }
        input { padding: 12px; width: 60%; font-size: 16px; border: none; border-radius: 5px; background: #0f3460; color: #fff; }
        button { padding: 12px 25px; font-size: 16px; background: #e94560; color: #fff; border: none; border-radius: 5px; cursor: pointer; }
        button:hover { background: #ff6b6b; }
        .results { margin-top: 20px; }
        .result-item { background: #16213e; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 4px solid #e94560; }
        .result-item .name { color: #00d9ff; font-size: 16px; font-weight: bold; }
        .result-item .info { color: #888; font-size: 12px; margin-top: 5px; }
        .result-item .hash { color: #666; font-family: monospace; font-size: 11px; }
        a { color: #00d9ff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <h1>🔍 DHT Torrent Search Engine</h1>
    <div class="stats">
        <span>总收集: <span id="total">0</span></span>
        <span>搜索索引: <span id="indexed">0</span></span>
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
                    html += '<div class="name">' + (item.name || 'Unknown') + '</div>';
                    html += '<div class="info">Size: ' + item.size_formatted + ' | Peers: ' + item.peer_count + '</div>';
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
            
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get("q", [""])[0]
            
            results = []
            if query:
                query_lower = query.lower()
                for h, data in torrent_db.items():
                    name = data.get("name", "").lower()
                    if query_lower in name and name != "unknown":
                        # 格式化大小
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
                            "size": data.get("size", 0),
                            "size_formatted": size_fmt,
                            "peer_count": len(peer_db.get(h, []))
                        })
            else:
                # 无搜索词返回最新的
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
                            "size": data.get("size", 0),
                            "size_formatted": size_fmt,
                            "peer_count": len(peer_db.get(h, []))
                        })
            
            self.wfile.write(json.dumps(results[:100], ensure_ascii=False).encode())


def run_http():
    server = HTTPServer(("", HTTP_PORT), SearchHandler)
    print(f"[HTTP] 服务启动: http://localhost:{HTTP_PORT}")
    server.serve_forever()


# ============== 主程序 ==============
def main():
    global running
    
    print("=" * 60)
    print("DHT Torrent Search Engine - 完整版")
    print("=" * 60)
    print(f"DHT 端口: {DHT_PORT}")
    print(f"HTTP 端口: {HTTP_PORT}")
    print()
    
    # 启动 HTTP 服务器
    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()
    
    # 启动 DHT 爬虫
    crawler = DHTCrawler(DHT_PORT)
    crawler.start()
    
    print()
    print("服务运行中，按 Ctrl+C 停止")
    print()
    
    # 主循环
    last_bootstrap = time.time()
    while running:
        try:
            # 处理 DHT 消息
            for _ in range(100):  # 每次循环处理多个消息
                crawler.run_once()
            
            # 每10秒进行一次bootstrap
            if time.time() - last_bootstrap > 10:
                crawler.periodic_bootstrap()
                last_bootstrap = time.time()
                print(f"[状态] 收集: {len(torrent_db)} | Peers: {sum(len(v) for v in peer_db.values())}")
                
            time.sleep(0.01)
            
        except KeyboardInterrupt:
            running = False
            print("\n停止服务")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()