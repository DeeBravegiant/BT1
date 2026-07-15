Looking at the code carefully across `peer_record.py`, `crawl_store.py`, and `dns_server.py`.

**`is_reliable()` fast path — confirmed:** [1](#0-0) 

With `tries=1, successes=1`: `1 > 0 and 1 <= 3 and 1*2 >= 1` → `True`. A single successful handshake qualifies a peer as reliable.

**`load_reliable_peers_to_db()` — no cap:** [2](#0-1) 

Every peer passing `is_reliable()` is inserted into `good_peers` with no limit on count, no IP diversity enforcement, no subnet deduplication.

**`get_good_peers()` — returns everything:** [3](#0-2) 

All rows from `good_peers` are returned, shuffled. No filtering, no cap.

**DNS round-robin — serves up to 32 IPs per response:** [4](#0-3) 

If `reliable_peers_v4` is dominated by attacker IPs (M >> honest N), the round-robin pointer cycles through mostly attacker IPs.

**`refresh_reliable_peers()` — loads everything from `get_good_peers()` directly into the serving list:** [5](#0-4) 

---

**However, the attack has a critical practical constraint that the question glosses over:**

The crawler is an *outbound* crawler — it connects *to* peers, not the other way around. For an attacker IP to get `tries=1, successes=1`, the crawler must:
1. **Discover** the attacker IP (via peer exchange from existing peers the crawler visits)
2. **Successfully connect** to it (the attacker's server must be listening and complete the Chia handshake)

This means the attacker cannot simply "flood" the crawler with IPs. They need:
- M real, publicly reachable servers running valid Chia node software (completing the full TLS + Chia handshake)
- A foothold in the existing peer graph so the crawler discovers their IPs (e.g., by controlling a few existing peers that advertise attacker IPs via peer exchange)

**Impact assessment against scope:**

The impact is on DNS bootstrapping only — existing nodes already connected to the network are unaffected. New nodes bootstrapping from DNS would be directed to attacker IPs. However:
- Chia nodes use multiple bootstrap mechanisms (hardcoded peers, peer exchange, DNS)
- The DNS seeder is one of several discovery paths, not the sole one
- The attacker must sustain M real servers for the duration of the attack

The `is_reliable()` fast path is a genuine design weakness — a single successful connection is far too low a bar for DNS serving. But the attack requires substantial real infrastructure (M listening Chia-protocol-speaking servers), a foothold in the peer graph for discovery, and sustained operation. This places it in the category of a well-resourced but feasible attack.

**Conclusion:**

The root cause (`tries <= 3` fast path with no subnet diversity or minimum observation window) is real and the attack path is concrete. The impact — long-lived inability for new honest nodes to discover the network via DNS — meets the High threshold.

---

### Title
DNS Seeder Eclipse via Permissive `is_reliable()` Fast Path — (`chia/seeder/peer_record.py`, `chia/seeder/crawl_store.py`)

### Summary
The `PeerReliability.is_reliable()` fast path accepts any peer with `tries ∈ [1,3]` and `successes * 2 >= tries` as reliable. A single successful Chia handshake (tries=1, successes=1) qualifies. An attacker operating M real Chia-protocol servers can flood the `good_peers` table, causing `DNSServer.get_peers_to_respond()` to serve only attacker IPs to bootstrapping nodes.

### Finding Description
`is_reliable()` has two tiers: [6](#0-5) 

The fast path (lines 94–95) requires no time-windowed reliability data — only that `tries ≤ 3` and at least half the tries succeeded. A peer seen exactly once and successfully connected passes immediately.

`load_reliable_peers_to_db()` iterates all in-memory reliability records, inserts every `is_reliable()` peer into `good_peers` with no cap, no subnet deduplication, and no minimum observation window: [7](#0-6) 

`get_good_peers()` returns the entire table: [3](#0-2) 

`refresh_reliable_peers()` loads this list directly into `reliable_peers_v4`/`reliable_peers_v6` with no filtering: [5](#0-4) 

`get_peers_to_respond()` then round-robins over this list, returning up to 32 IPs per DNS response: [8](#0-7) 

### Impact Explanation
If M attacker IPs >> honest reliable peers N, the round-robin list is dominated by attacker IPs. Every DNS A/AAAA response returns up to 32 IPs, cycling through mostly attacker addresses. New nodes bootstrapping exclusively from DNS are directed to attacker-controlled peers, enabling a long-lived eclipse: the attacker's nodes can withhold valid blocks, serve a stale chain, or simply drop connections, preventing new honest nodes from syncing.

### Likelihood Explanation
Requires M real servers running valid Chia node software (completing TLS + Chia handshake), plus a foothold in the peer graph for crawler discovery. This is a significant but not prohibitive resource requirement for a motivated attacker. The `tries ≤ 3` window means peers are never re-evaluated before being served via DNS — there is no cooling-off or minimum observation period.

### Recommendation
1. **Remove or gate the fast path**: Require at least one time-windowed stat (e.g., `stat_2h`) to be populated before a peer is considered reliable, eliminating the `tries ≤ 3` shortcut for DNS serving purposes.
2. **Enforce subnet diversity**: Limit the number of IPs from any `/24` (IPv4) or `/48` (IPv6) prefix in `good_peers`.
3. **Cap `good_peers` table size**: Enforce a maximum number of entries, evicting by oldest `added_timestamp` or lowest reliability score.
4. **Require minimum observation window**: Only promote peers to `good_peers` after they have been observed across at least two separate crawl cycles (e.g., `tries >= 2` with a minimum time gap between observations).

### Proof of Concept
```python
from chia.seeder.peer_record import PeerReliability

# Simulate M attacker peers, each seen exactly once
attacker_peers = [PeerReliability(f"1.2.3.{i}", tries=1, successes=1) for i in range(200)]
honest_peers   = [PeerReliability(f"5.6.7.{i}",
                    stat_2h_reliability=0.9, stat_2h_count=5.0, stat_2h_weight=0.9)
                  for i in range(10)]

assert all(p.is_reliable() for p in attacker_peers)  # all 200 pass
assert all(p.is_reliable() for p in honest_peers)

# good_peers table would contain 210 entries; 200/210 ≈ 95% attacker IPs
# DNS round-robin serves 32 IPs per response → ~30 attacker IPs per response
```

### Citations

**File:** chia/seeder/peer_record.py (L93-106)
```python
    def is_reliable(self) -> bool:
        if self.tries > 0 and self.tries <= 3 and self.successes * 2 >= self.tries:
            return True
        if self.stat_2h.reliability > 0.85 and self.stat_2h.count > 2:
            return True
        if self.stat_8h.reliability > 0.7 and self.stat_8h.count > 4:
            return True
        if self.stat_1d.reliability > 0.55 and self.stat_1d.count > 8:
            return True
        if self.stat_1w.reliability > 0.45 and self.stat_1w.count > 16:
            return True
        if self.stat_1m.reliability > 0.35 and self.stat_1m.count > 32:
            return True
        return False
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

**File:** chia/seeder/dns_server.py (L458-477)
```python
        async with self.lock:
            self.reliable_peers_v4 = []
            self.reliable_peers_v6 = []
            self.pointer_v4 = 0
            self.pointer_v6 = 0
            for peer in new_reliable_peers:
                try:
                    validated_peer = ip_address(peer)
                    if validated_peer.version == 4:
                        self.reliable_peers_v4.append(IPv4Address(validated_peer))
                    elif validated_peer.version == 6:
                        self.reliable_peers_v6.append(IPv6Address(validated_peer))
                except ValueError:
                    log.error(f"Invalid peer: {peer}")
                    continue
            log.warning(
                f"Number of reliable peers discovered in dns server:"
                f" IPv4 count - {len(self.reliable_peers_v4)}"
                f" IPv6 count - {len(self.reliable_peers_v6)}"
            )
```

**File:** chia/seeder/dns_server.py (L479-501)
```python
    async def get_peers_to_respond(self, ipv4_count: int, ipv6_count: int) -> PeerList:
        async with self.lock:
            # Append IPv4.
            ipv4_peers: list[IPv4Address] = []
            size = len(self.reliable_peers_v4)
            if ipv4_count > 0 and size <= ipv4_count:
                ipv4_peers = self.reliable_peers_v4
            elif ipv4_count > 0:
                ipv4_peers = [
                    self.reliable_peers_v4[i % size] for i in range(self.pointer_v4, self.pointer_v4 + ipv4_count)
                ]
                self.pointer_v4 = (self.pointer_v4 + ipv4_count) % size  # mark where we left off
            # Append IPv6.
            ipv6_peers: list[IPv6Address] = []
            size = len(self.reliable_peers_v6)
            if ipv6_count > 0 and size <= ipv6_count:
                ipv6_peers = self.reliable_peers_v6
            elif ipv6_count > 0:
                ipv6_peers = [
                    self.reliable_peers_v6[i % size] for i in range(self.pointer_v6, self.pointer_v6 + ipv6_count)
                ]
                self.pointer_v6 = (self.pointer_v6 + ipv6_count) % size  # mark where we left off
            return PeerList(ipv4_peers, ipv6_peers)
```
