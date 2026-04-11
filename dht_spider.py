#!/usr/bin/env python3
"""
真正的DHT爬虫 - 集成magnet-dht核心逻辑
监听get_peers和announce_peer请求，从别人那里"偷"info_hash
"""
import os
import socket
import struct
import time
import threading
import logging
from collections import deque

# 使用已安装的bencodepy
import bencodepy as bencoder

# ====== 配置 ======
DHT_PORT = 6881  # 用户已打开的端口
MAX_NODES = 10000
UDP_BUFFER = 65535

# Bootstrap节点
BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
]

# ====== 工具函数 ======
def get_rand_id():
    """生成20字节随机节点ID"""
    return os.urandom(20)

def get_neighbor(target):
    """生成目标节点附近的邻居ID"""
    return target[:14] + os.urandom(6)

def get_nodes_info(nodes_data):
    """解析nodes字段: 20字节ID + 4字节IP + 2字节端口"""
    length = len(nodes_data)
    if length % 26 != 0:
        return []
    for i in range(0, length, 26):
        nid = nodes_data[i:i+20]
        ip = ".".join(str(b) for b in nodes_data[i+20:i+24])
        port = struct.unpack("!H", nodes_data[i+24:i+26])[0]
        yield (nid, ip, port)

class HNode:
    def __init__(self, nid, ip, port):
        self.nid = nid
        self.ip = ip
        self.port = port

class DHTSpider:
    """DHT爬虫 - 监听get_peers请求获取info_hash"""
    
    def __init__(self, port=DHT_PORT):
        self.port = port
        self.nid = get_rand_id()
        self.nodes = deque(maxlen=MAX_NODES)
        
        # UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        
        # 存储发现的info_hash
        self.info_hashes = set()
        self.lock = threading.Lock()
        
        self.running = False
        
    def send_krpc(self, msg, addr):
        """发送KRPC消息"""
        try:
            self.sock.sendto(bencoder.encode(msg), addr)
        except:
            pass
            
    def send_find_node(self, addr, target=None):
        """发送find_node请求"""
        tid = os.urandom(2)
        target = target or get_rand_id()
        nid = get_neighbor(target)
        
        msg = {
            b"t": tid,
            b"y": b"q",
            b"q": b"find_node",
            b"a": {b"id": nid, b"target": target}
        }
        self.send_krpc(msg, addr)
        
    def send_ping(self, addr):
        """发送ping保持连接"""
        tid = os.urandom(2)
        msg = {
            b"t": tid,
            b"y": b"q",
            b"q": b"ping",
            b"a": {b"id": self.nid}
        }
        self.send_krpc(msg, addr)
        
    def bootstrap(self):
        """连接bootstrap节点"""
        for addr in BOOTSTRAP_NODES:
            print(f"[DHT] 连接bootstrap: {addr}")
            self.send_find_node(addr)
            
    def on_find_node_response(self, msg):
        """处理find_node响应，收集节点"""
        try:
            nodes_data = msg.get(b"r", {}).get(b"nodes", b"")
            for nid, ip, port in get_nodes_info(nodes_data):
                if len(nid) == 20 and ip != "0.0.0.0":
                    self.nodes.append(HNode(nid, ip, port))
        except:
            pass
            
    def on_get_peers_request(self, msg, addr):
        """处理get_peers请求 - 偷info_hash！"""
        try:
            info_hash = msg.get(b"a", {}).get(b"info_hash")
            if info_hash:
                ih_hex = info_hash.hex()
                with self.lock:
                    if ih_hex not in self.info_hashes:
                        self.info_hashes.add(ih_hex)
                        print(f"[DHT] 偷到info_hash: {ih_hex}")
                        # 触发metadata获取
                        self.request_metadata(ih_hex)
        except:
            pass
            
    def on_announce_peer_request(self, msg, addr):
        """处理announce_peer请求 - 偷info_hash！"""
        try:
            info_hash = msg.get(b"a", {}).get(b"info_hash")
            if info_hash:
                ih_hex = info_hash.hex()
                with self.lock:
                    if ih_hex not in self.info_hashes:
                        self.info_hashes.add(ih_hex)
                        print(f"[DHT] announce_peer: {ih_hex}")
                        self.request_metadata(ih_hex)
        except:
            pass
            
    def handle_message(self, msg, addr):
        """处理收到的消息"""
        try:
            msg_type = msg.get(b"y", b"")
            
            if msg_type == b"r":  # 响应
                if msg.get(b"r", {}).get(b"nodes"):
                    self.on_find_node_response(msg)
                    
            elif msg_type == b"q":  # 请求
                query = msg.get(b"q", b"")
                if query == b"get_peers":
                    self.on_get_peers_request(msg, addr)
                elif query == b"announce_peer":
                    self.on_announce_peer_request(msg, addr)
                    
        except Exception as e:
            pass
            
    def request_metadata(self, info_hash):
        """通知主程序获取metadata"""
        # 这里可以调用主程序的metadata获取逻辑
        # 或者通过API/队列通知
        pass
        
    def receive_loop(self):
        """接收消息循环"""
        print(f"[DHT] 开始监听端口 {DHT_PORT}...")
        self.bootstrap()
        
        last_bootstrap = time.time()
        
        while self.running:
            try:
                self.sock.settimeout(1)
                data, addr = self.sock.recvfrom(UDP_BUFFER)
                msg = bencoder.decode(data)
                self.handle_message(msg, addr)
                
            except socket.timeout:
                # 定期bootstrap保持连接
                if time.time() - last_bootstrap > 30:
                    self.bootstrap()
                    last_bootstrap = time.time()
                    # 从节点池发送find_node
                    for _ in range(10):
                        try:
                            node = self.nodes.popleft()
                            self.send_find_node((node.ip, node.port), node.nid)
                        except:
                            break
                continue
            except Exception as e:
                pass
                
    def start(self):
        """启动DHT爬虫"""
        self.running = True
        print(f"[*] DHT爬虫启动，端口 {DHT_PORT}")
        self.receive_loop()
        
    def stop(self):
        self.running = False

# ====== 测试 ======
if __name__ == "__main__":
    print("=" * 50)
    print("DHT爬虫 - 从DHT网络偷info_hash")
    print("=" * 50)
    
    dht = DHTSpider(DHT_PORT)
    try:
        dht.start()
    except KeyboardInterrupt:
        dht.stop()
        print("\n已停止")
