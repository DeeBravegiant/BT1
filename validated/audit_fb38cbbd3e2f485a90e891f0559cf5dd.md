The code is all present and the claim is fully traceable. Here is my analysis:

---

**Tracing the exact call chain:**

**`is_reliable()` with tries=1, successes=1:** [1](#0-0) 

`1 > 0 and 1 <= 3 and (1*2 >= 1)` → **True**. The first branch fires immediately on the very first successful connection.

**`update()` increments tries/successes on a successful connection:** [2](#0-1) 

**`new_peak` in the crawler calls `peer_connected_hostname(host, True)` when height >= minimum_height:** [3](#0-2) 

**`peer_connected_hostname` → `peer_connected` → `reliability.update(True, age)`:** [4](#0-3) 

**`load_reliable_peers_to_db` inserts every `is_reliable()` peer into `good_peers`:** [5](#0-4) 

**`get_good_peers` reads directly from that table for the DNS server:** [6](#0-5) 

---

**Entry point reachability:**

The crawler makes **outbound** connections to peers in its list. Peers enter the list when gossiped by any existing peer with a timestamp within the last 5 days: [7](#0-6) 

An attacker only needs one existing node to gossip their IP. Once the crawler connects outbound to the attacker's node, the attacker sends `NewPeak(height >= minimum_height)` — a single unauthenticated protocol message — and the entire chain fires.

---

**The `tries <= 3` branch is the root cause.** The statistical branches (stat_2h, stat_8h, etc.) require meaningful observation counts (>2, >4, >8, etc.) before granting reliability. The first branch bypasses all of that, granting reliability after exactly 1 success. This was likely intended as a bootstrap shortcut but has no lower bound on observation quality.

---

### Title
DNS Seeder Peer List Poisoning via Single-Connection `is_reliable()` Bypass — (`chia/seeder/peer_record.py`, `chia/seeder/crawl_store.py`)

### Summary
`PeerReliability.is_reliable()` returns `True` when `tries=1, successes=1` due to an unconditional early-return branch. A single successful crawl interaction (TLS handshake + one `NewPeak` message) is sufficient to insert an attacker's IP into the DNS seeder's `good_peers` table, from which it is served to all bootstrapping nodes.

### Finding Description
`is_reliable()` contains five statistical branches requiring sustained observation, but its **first branch** short-circuits all of them:

```python
if self.tries > 0 and self.tries <= 3 and self.successes * 2 >= self.tries:
    return True
```

With `tries=1, successes=1`: `1 > 0 ∧ 1 ≤ 3 ∧ 2 ≥ 1` → `True`.

The full call chain is:
1. Attacker's IP is gossiped to the crawler by any peer (timestamp within 5 days).
2. Crawler makes outbound connection to attacker's node (`connect_task`).
3. Attacker sends `NewPeak(height >= minimum_height)`.
4. `Crawler.new_peak` → `peer_connected_hostname(host, True)` → `peer_connected` → `reliability.update(True, age)` → `tries=1, successes=1`.
5. Next `save_to_db()` call → `load_reliable_peers_to_db()` iterates all peers, calls `is_reliable()`, attacker's peer returns `True`, IP inserted into `good_peers`.
6. `get_good_peers()` returns attacker IP to DNS server, which serves it to bootstrapping nodes.

### Impact Explanation
The DNS seeder is the primary bootstrap mechanism for new Chia nodes. Attacker IPs in `good_peers` are served to every new node performing DNS bootstrap. An attacker operating multiple IPs (each requiring only one crawl interaction to qualify) can dominate the `good_peers` pool, causing new nodes to connect predominantly to attacker-controlled peers. This enables eclipse attacks resulting in long-lived inability to sync valid chain state — matching the High impact criterion.

### Likelihood Explanation
The preconditions are minimal: the attacker needs only to run a node that (a) gets gossiped to the crawler by any peer, and (b) accepts the crawler's outbound TCP connection and sends one `NewPeak` message. No key material, no privileged access, no cryptographic break required. The gossip condition is trivially satisfied by connecting to any honest node and advertising the attacker's IP.

### Recommendation
Remove or harden the `tries <= 3` early-return branch. At minimum, require `tries >= 3` (not `<= 3`) before applying the simple majority check, or require a minimum number of observations across at least one time-windowed stat bucket before granting reliability. The statistical branches already implement the correct design; the first branch undermines them entirely.

### Proof of Concept
```python
from chia.seeder.peer_record import PeerReliability

r = PeerReliability("1.2.3.4")
r.update(True, 1000)          # single successful connection
assert r.tries == 1
assert r.successes == 1
assert r.is_reliable() == True  # passes immediately
```
Then trace through `load_reliable_peers_to_db`: the peer_id `"1.2.3.4"` will appear in `good_peers` after the next `save_to_db()` cycle (~15 seconds after the crawl batch completes).

### Citations

**File:** chia/seeder/peer_record.py (L93-95)
```python
    def is_reliable(self) -> bool:
        if self.tries > 0 and self.tries <= 3 and self.successes * 2 >= self.tries:
            return True
```

**File:** chia/seeder/peer_record.py (L132-140)
```python
    def update(self, is_reachable: bool, age: int) -> None:
        self.stat_2h.update(is_reachable, age, 2 * 3600)
        self.stat_8h.update(is_reachable, age, 8 * 3600)
        self.stat_1d.update(is_reachable, age, 24 * 3600)
        self.stat_1w.update(is_reachable, age, 7 * 24 * 3600)
        self.stat_1m.update(is_reachable, age, 24 * 30 * 3600)
        self.tries += 1
        if is_reachable:
            self.successes += 1
```

**File:** chia/seeder/crawler.py (L238-258)
```python
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

**File:** chia/seeder/crawler.py (L342-344)
```python
            if request.height >= self.minimum_height:
                if self.crawl_store is not None:
                    await self.crawl_store.peer_connected_hostname(peer_info.host, True, tls_version)
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

**File:** chia/seeder/crawl_store.py (L317-336)
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
