#!/usr/bin/env python3
"""混合 DHT 爬虫 - 主动查询已知 info_hash 的元数据"""
import json
import time
import sqlite3
import bencodepy

try:
    import libtorrent as lt
    HAS_LT = True
except:
    HAS_LT = False
    print("[ERROR] libtorrent not available")

DB_PATH = "/root/.openclaw/workspace/torrents.db"
REAL_HASHES_FILE = "/root/.openclaw/workspace/real_info_hashes.json"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS torrents (
        info_hash TEXT PRIMARY KEY, name TEXT, magnet TEXT, 
        filenames TEXT, size INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_filenames ON torrents(filenames)")
    conn.commit()
    conn.close()

def save_torrent(info_hash, name, filenames, size):
    """保存torrent到数据库"""
    if not name:
        return
    magnet = f"magnet:?xt=urn:btih:{info_hash}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    filenames_json = json.dumps(filenames, ensure_ascii=False)
    c.execute("INSERT OR REPLACE INTO torrents (info_hash, name, magnet, filenames, size) VALUES (?, ?, ?, ?, ?)",
              (info_hash, name, magnet, filenames_json, size))
    conn.commit()
    conn.close()
    print(f"[SAVE] {name[:50]}... ({size} bytes)")

def query_metadata_lt(info_hash_hex, name, max_wait=10):
    """使用 libtorrent 查询元数据"""
    if not HAS_LT:
        return None
    
    try:
        info_hash = bytes.fromhex(info_hash_hex)
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(info_hash)
        atp.save_path = "/tmp"
        
        session = lt.session()
        session.start_dht()
        h = session.add_torrent(atp)
        
        # 等待元数据
        start_time = time.time()
        while time.time() - start_time < max_wait:
            if h.has_metadata():
                ti = h.get_torrent_info()
                filenames = []
                if ti.num_files() > 1:
                    for i in range(ti.num_files()):
                        filenames.append(ti.files().file_path(i))
                else:
                    filenames.append(ti.name())
                return {
                    "name": ti.name(),
                    "filenames": filenames,
                    "size": ti.total_size()
                }
            time.sleep(0.5)
        
        session.remove_torrent(h)
    except Exception as e:
        pass
    
    # 如果无法获取元数据，至少保存基本信息
    return {"name": name, "filenames": [name], "size": 0}

def main():
    if not HAS_LT:
        print("[ERROR] Cannot run without libtorrent")
        return
    
    print("[*] 混合 DHT 爬虫启动")
    init_db()
    
    # 加载已知的 info_hashes
    with open(REAL_HASHES_FILE) as f:
        hashes = json.load(f)
    
    print(f"[*] 已加载 {len(hashes)} 个已知 info_hash")
    
    # 创建 libtorrent session
    session = lt.session()
    session.start_dht()
    session.add_dht_router("router.bittorrent.com", 6881)
    session.add_dht_router("dht.transmissionbt.com", 6881)
    
    # 批量查询每个 info_hash
    saved = 0
    for i, item in enumerate(hashes[:200]):  # 先处理前50个
        info_hash = item["info_hash"]
        name = item["name"]
        
        print(f"[*] 查询 {i+1}/50: {info_hash} - {name[:30]}...")
        
        try:
            # 将 info_hash 添加到 DHT
            atp = lt.add_torrent_params()
            atp.ti = lt.torrent_info(bytes.fromhex(info_hash))
            atp.save_path = "/tmp"
            h = session.add_torrent(atp)
            
            # 等待元数据
            for _ in range(20):  # 最多等10秒
                if h.has_metadata():
                    ti = h.get_torrent_info()
                    filenames = []
                    if ti.num_files() > 1:
                        for j in range(ti.num_files()):
                            filenames.append(ti.files().file_path(j))
                    else:
                        filenames.append(ti.name())
                    
                    save_torrent(info_hash, ti.name(), filenames, ti.total_size())
                    saved += 1
                    break
                time.sleep(0.5)
            
            session.remove_torrent(h)
            
        except Exception as e:
            # 保存基本信息
            save_torrent(info_hash, name, [name], 0)
            saved += 1
        
        time.sleep(0.5)  # 避免过快查询
    
    print(f"[*] 完成！已保存 {saved} 个 torrents 到数据库")

if __name__ == "__main__":
    main()
