I need to trace the exact code path to verify whether the described exploit is real and reachable.

The code path is fully traceable and the exploit is real. Here is the complete analysis:

---

### Title
Unauthenticated `NewPeak` Height Claim Poisons DNS Seeder `good_peers` Table — (`chia/seeder/crawler.py`, `chia/seeder/crawl_store.py`, `chia/seeder/peer_record.py`)

### Summary
The Chia crawler marks a remote peer as "reliable" and eligible for DNS seeder advertisement based solely on receiving a `NewPeak` message whose `height` field is ≥ `minimum_height`. No chain proof, weight proof, or block header is requested or verified. An attacker who can complete the Chia TLS/protocol handshake and send a single fabricated `NewPeak` message will have their IP inserted into the `good_peers` table after one crawl cycle, causing the DNS seeder to advertise attacker-controlled IPs to every bootstrapping node.

### Finding Description

**Step 1 — Entry point: `CrawlerAPI.new_peak`** [1](#0-0) 

The handler accepts any `NewPeak` message from any connected peer and immediately delegates to `Crawler.new_peak` with no pre-filtering.

**Step 2 — The only guard is a height integer comparison** [2](#0-1) 

`request.height` is an attacker-supplied integer. There is no request for block headers, no weight proof, no VDF verification, and no cross-check against any trusted chain state. If the integer satisfies `>= self.minimum_height`, `peer_connected_hostname(host, True)` is called unconditionally.

**Step 3 — `peer_connected_hostname` → `peer_connected` → `reliability.update(True, …)`** [3](#0-2) [4](#0-3) 

`peer_connected` sets `connected=True` on the record and calls `reliability.update(True, age)`, which increments `tries` and `successes`.

**Step 4 — `is_reliable()` returns `True` after a single success** [5](#0-4) 

After one call to `reliability.update(True, …)`, `tries=1` and `successes=1`. The condition `tries > 0 and tries <= 3 and successes * 2 >= tries` evaluates to `True` immediately (1 > 0, 1 ≤ 3, 2 ≥ 1). The peer is marked reliable after a single fabricated `NewPeak`.

**Step 5 — `load_reliable_peers_to_db` writes attacker IP to `good_peers`** [6](#0-5) 

Every crawl cycle, `save_to_db` calls `load_reliable_peers_to_db`, which deletes the old `good_peers` table and repopulates it with every peer for which `is_reliable()` is `True`. The attacker's IP is now in `good_peers`.

**Step 6 — DNS seeder serves attacker IPs to bootstrapping nodes** [7](#0-6) 

`get_good_peers` reads directly from `good_peers` and returns a shuffled list. If the attacker floods this table (by gossiping many attacker IPs via `RespondPeers` to the crawler, then answering each crawler connection with a fabricated `NewPeak`), bootstrapping nodes receive predominantly attacker-controlled IPs.

**Attacker IP injection into the crawl queue** [8](#0-7) 

The crawler accepts peer lists from any peer it connects to. An attacker running even one legitimate node can gossip hundreds of attacker-controlled IPs to the crawler via `RespondPeers`, seeding the crawl queue with attacker addresses.

### Impact Explanation

New nodes that bootstrap exclusively via the DNS seeder receive a list dominated by attacker IPs. The attacker nodes can refuse to serve blocks, serve stale chain tips, or withhold transactions, causing bootstrapping nodes to be permanently unable to sync valid blocks. This satisfies the **High** impact criterion: *permanent or long-lived inability for honest nodes to process valid blocks or sync updates under normal network assumptions*.

### Likelihood Explanation

- The attacker needs only one initial foothold: any node the crawler will connect to (trivially obtained by running a standard Chia node and waiting for the crawler to discover it via gossip).
- The Chia TLS layer uses self-signed certificates with no CA pinning; any node can complete the handshake.
- `minimum_height` is observable from the network or from the open-source config.
- The attack requires no cryptographic material, no privileged access, and no race condition. It is deterministic and repeatable every crawl cycle.

### Recommendation

1. **Require chain proof before marking a peer reliable**: After receiving `NewPeak`, request at least a block header or a weight proof for the claimed height and verify it against the known genesis challenge and expected difficulty before calling `peer_connected_hostname(host, True)`.
2. **Raise the `is_reliable()` threshold**: The first-branch condition (`tries <= 3`) allows reliability after a single success. Require a minimum number of independent successful connections (e.g., `tries >= 5`) before a peer is eligible for `good_peers`.
3. **Rate-limit and cap attacker-supplied peer lists**: Limit the number of new IPs accepted from a single `RespondPeers` message to reduce the attacker's ability to flood the crawl queue.

### Proof of Concept

```python
# Minimal sketch — spin up a TCP server that:
# 1. Completes the Chia TLS handshake (self-signed cert, any key)
# 2. Completes the Chia protocol handshake (NodeType.FULL_NODE)
# 3. Sends NewPeak(height=minimum_height, ...)
# Then run the crawler pointed at this server.
# After one crawl cycle, query the crawler DB:
#   SELECT * FROM good_peers WHERE ip = '<attacker_ip>';
# The row will be present.
```

The call sequence is:
`attacker TCP accept` → `TLS handshake` → `Chia protocol handshake` → `send NewPeak(height=minimum_height)` → `CrawlerAPI.new_peak` → `Crawler.new_peak` (line 342 height check passes) → `crawl_store.peer_connected_hostname(host, True)` → `peer_connected` → `reliability.update(True, age)` (tries=1, successes=1) → next `save_to_db` → `load_reliable_peers_to_db` → attacker IP in `good_peers`.

### Citations

**File:** chia/seeder/crawler_api.py (L44-47)
```python
    @metadata.request(peer_required=True)
    async def new_peak(self, request: full_node_protocol.NewPeak, peer: WSChiaConnection) -> Message | None:
        await self.crawler.new_peak(request, peer)
        return None
```

**File:** chia/seeder/crawler.py (L231-258)
```python
                for response in self.peers_retrieved:
                    for response_peer in response.peer_list:
                        if response_peer.host not in self.best_timestamp_per_peer:
                            self.best_timestamp_per_peer[response_peer.host] = response_peer.timestamp
                        self.best_timestamp_per_peer[response_peer.host] = max(
                            self.best_timestamp_per_peer[response_peer.host], response_peer.timestamp
                        )
                        if (
                            response_peer.host not in self.seen_nodes
                            and response_peer.timestamp > time.time() - 5 * 24 * 3600
                        ):
                            self.seen_nodes.add(response_peer.host)
                            new_peer = PeerRecord(
                                response_peer.host,
                                response_peer.host,
                                uint32(response_peer.port),
                                False,
                                uint64(0),
                                uint32(0),
                                uint64(0),
                                uint64(time.time()),
                                uint64(response_peer.timestamp),
                                "undefined",
                                uint64(0),
                                tls_version="unknown",
                            )
                            new_peer_reliability = PeerReliability(response_peer.host)
                            self.crawl_store.maybe_add_peer(new_peer, new_peer_reliability)
```

**File:** chia/seeder/crawler.py (L342-345)
```python
            if request.height >= self.minimum_height:
                if self.crawl_store is not None:
                    await self.crawl_store.peer_connected_hostname(peer_info.host, True, tls_version)
            self.with_peak.add(peer_info)
```

**File:** chia/seeder/crawl_store.py (L155-165)
```python
    async def peer_connected(self, peer: PeerRecord, tls_version: str) -> None:
        now = uint64(time.time())
        age_timestamp = int(max(peer.last_try_timestamp, peer.connected_timestamp))
        if age_timestamp == 0:
            age_timestamp = now - 1000
        replaced = replace(peer, connected=True, connected_timestamp=now, tls_version=tls_version)
        reliability = await self.get_peer_reliability(peer.peer_id)
        if reliability is None:
            reliability = PeerReliability(peer.peer_id)
        reliability.update(True, now - age_timestamp)
        await self.add_peer(replaced, reliability)
```

**File:** chia/seeder/crawl_store.py (L177-184)
```python
    async def peer_connected_hostname(self, host: str, connected: bool = True, tls_version: str = "unknown") -> None:
        if host not in self.host_to_records:
            return
        record = self.host_to_records[host]
        if connected:
            await self.peer_connected(record, tls_version)
        else:
            await self.peer_failed_to_connect(record)
```

**File:** chia/seeder/crawl_store.py (L317-337)
```python
    async def load_reliable_peers_to_db(self) -> None:
        peers = []
        for peer_id, reliability in self.host_to_reliability.items():
            if reliability.is_reliable():
                peers.append(peer_id)
        self.reliable_peers = len(peers)
        log.warning("Deleting old good_peers from DB...")
        cursor = await self.crawl_db.execute(
            "DELETE from good_peers",
        )
        await cursor.close()
        log.warning(" - Done deleting old good_peers...")
        log.warning("Saving new good_peers to DB...")
        for peer_id in peers:
            cursor = await self.crawl_db.execute(
                "INSERT OR REPLACE INTO good_peers VALUES(?)",
                (peer_id,),
            )
            await cursor.close()
        await self.crawl_db.commit()
        log.warning(" - Done saving new good_peers to DB...")
```

**File:** chia/seeder/crawl_store.py (L368-377)
```python
    async def get_good_peers(self) -> list[str]:  # This is for the DNS server
        cursor = await self.crawl_db.execute(
            "SELECT * from good_peers",
        )
        rows = await cursor.fetchall()
        await cursor.close()
        result = [row[0] for row in rows]
        if len(result) > 0:
            random.shuffle(result)  # mix up the peers
        return result
```

**File:** chia/seeder/peer_record.py (L93-96)
```python
    def is_reliable(self) -> bool:
        if self.tries > 0 and self.tries <= 3 and self.successes * 2 >= self.tries:
            return True
        if self.stat_2h.reliability > 0.85 and self.stat_2h.count > 2:
```
