The code confirms this is a real vulnerability. Let me trace the exact path.

**`is_trusted_peer` in `chia/util/network.py`:** [1](#0-0) 

```python
def is_trusted_peer(
    host: str, node_id: bytes32, trusted_peers: dict[str, Any], trusted_cidrs: list[str], testing: bool = False
) -> bool:
    return (
        (not testing and is_localhost(host)) or node_id.hex() in trusted_peers or is_trusted_cidr(host, trusted_cidrs)
    )
```

In production (`testing=False`), **any peer whose IP is `127.0.0.1`, `localhost`, `::1`, or `0:0:0:0:0:0:0:1` is unconditionally trusted** — no explicit `trusted_peers` config entry required. [2](#0-1) 

**`new_peak_wallet` in `chia/wallet/wallet_node_api.py`:** [3](#0-2) 

When `is_trusted(peer)` returns `True`, the wallet:
1. Collects all untrusted peers
2. Fetches a timestamp from the attacker-controlled peer
3. If `is_timestamp_in_sync` passes, calls `wallet_peers.ensure_is_closed()` and `untrusted_peer.close()` for **every** non-trusted peer

The attacker controls the timestamp returned in step 2, making step 3 trivially reachable.

---

### Title
Automatic localhost trust in `is_trusted_peer` allows unprivileged local process to disconnect all legitimate wallet peers — (`chia/util/network.py`, `chia/wallet/wallet_node_api.py`)

### Summary
In production mode, `is_trusted_peer` grants full trust to any peer connecting from localhost based solely on IP address. An unprivileged local process can connect to the wallet's peer server, send a `new_peak_wallet` message with a controlled timestamp, and cause the wallet to disconnect every legitimate untrusted full node peer and shut down peer discovery — isolating the wallet to the attacker's node.

### Finding Description
`is_trusted_peer` (line 147–148 of `chia/util/network.py`) evaluates `(not testing and is_localhost(host))` as the first condition. Since `testing` defaults to `False` and is `False` in all production call sites, any connection from `127.0.0.1`/`::1` is unconditionally trusted without any explicit operator configuration.

`new_peak_wallet` (lines 59–78 of `chia/wallet/wallet_node_api.py`) uses this trust decision to:
- Enumerate all untrusted full-node connections
- Request a timestamp from the trusted (attacker-controlled) peer
- If the timestamp is in sync, permanently close `wallet_peers` and disconnect every untrusted peer

Because the attacker supplies the timestamp via `get_timestamp_for_height_from_peer`, they can trivially satisfy `is_timestamp_in_sync`.

### Impact Explanation
After the attack, the wallet has no untrusted peers and peer discovery is permanently stopped (`wallet_peers = None`). The wallet is now exclusively synced to the attacker's node. The attacker can serve fabricated coin states, suppress incoming transactions, or present a false chain tip — corrupting wallet sync state with direct security impact (false balances, missed incoming coins, manipulated offer settlement state).

### Likelihood Explanation
Any process running on the same host as the wallet (e.g., a compromised user-space application, a malicious package in the same Python environment, or any local service) can open a TLS connection to the wallet's peer port. Chia peer TLS uses self-signed certificates; generating one requires no special privileges. The attacker needs only to: generate a certificate, connect to the wallet's peer port from localhost, and send a well-formed `NewPeakWallet` message.

### Recommendation
Remove the unconditional localhost trust from `is_trusted_peer`. Localhost should not be treated as implicitly trusted; trust must be explicitly configured via `trusted_peers` or `trusted_cidrs`. If localhost convenience is desired, it should be opt-in via explicit config, not the default production behavior.

```python
# Remove this branch entirely:
(not testing and is_localhost(host)) or ...
```

### Proof of Concept
1. Start a wallet node in production mode (non-testing).
2. Connect one or more legitimate full node peers (untrusted).
3. From a local process: generate a self-signed TLS cert, connect to the wallet's peer port on `127.0.0.1`.
4. Send a `NewPeakWallet` message with any plausible height and a timestamp within the sync window.
5. Observe: `is_trusted` returns `True` for the local connection; all legitimate peers are disconnected; `wallet_peers` is set to `None`.

### Citations

**File:** chia/util/network.py (L140-141)
```python
def is_localhost(peer_host: str) -> bool:
    return peer_host in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
```

**File:** chia/util/network.py (L144-149)
```python
def is_trusted_peer(
    host: str, node_id: bytes32, trusted_peers: dict[str, Any], trusted_cidrs: list[str], testing: bool = False
) -> bool:
    return (
        (not testing and is_localhost(host)) or node_id.hex() in trusted_peers or is_trusted_cidr(host, trusted_cidrs)
    )
```

**File:** chia/wallet/wallet_node_api.py (L59-78)
```python
        if self.wallet_node.is_trusted(peer):
            full_node_connections = self.wallet_node.server.get_connections(NodeType.FULL_NODE)
            untrusted_peers = [
                peer for peer in full_node_connections if not self.wallet_node.is_trusted(peer) and not peer.closed
            ]

            # Check for untrusted peers to avoid fetching the timestamp if not required
            if len(untrusted_peers) > 0:
                timestamp = await self.wallet_node.get_timestamp_for_height_from_peer(peak.height, peer)
            else:
                timestamp = None
            if timestamp is not None and self.wallet_node.is_timestamp_in_sync(timestamp):
                self.log.info("Connected to a synced trusted peer, disconnecting from all untrusted nodes.")
                # Stop peer discovery/connect tasks first
                if self.wallet_node.wallet_peers is not None:
                    await self.wallet_node.wallet_peers.ensure_is_closed()
                    self.wallet_node.wallet_peers = None
                # Then disconnect from all untrusted nodes
                for untrusted_peer in untrusted_peers:
                    await untrusted_peer.close()
```
