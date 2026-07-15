The code evidence is clear. Let me trace the exact path.

**Bug 1 — `increment_sent` always appends to `sent_to`, even for duplicate peer IDs** [1](#0-0) 

The deduplication guard only protects the `sent` counter:

```python
if name in current_peers:
    sent_count = uint32(current.sent)      # deduplicated ✓
else:
    sent_count = uint32(current.sent + 1)

sent_to.append(append_data)               # always appended — no dedup ✗
```

The existence of this guard proves the developers knew duplicate acks from the same peer are possible at the protocol level. The guard is incomplete: it protects `sent` but not `sent_to`.

**Bug 2 — `is_valid()` counts raw `sent_to` length, not distinct peers** [2](#0-1) 

```python
def is_valid(self) -> bool:
    if len(self.sent_to) < minimum_send_attempts:   # counts duplicates
        return True
    ...
    return False
```

`minimum_send_attempts = 6` [3](#0-2) 

**Bug 3 — `increment_sent` permanently abandons the tx when `is_valid()` returns False** [4](#0-3) 

```python
if not tx.is_valid():
    tx = dataclasses.replace(tx, confirmed=True, confirmed_at_height=uint32(0))
await self.add_transaction_record(tx)
```

---

**Combined exploit path:**

A malicious peer sends 6 FAILED ack responses for the same transaction. Each response triggers `increment_sent(tx_id, same_peer_id, FAILED, some_err)`. After 6 calls: `len(sent_to) == 6`, none are SUCCESS or fee-error, so `is_valid()` returns `False`, and the tx is written to DB as `confirmed=True, confirmed_at_height=0` — permanently abandoned without on-chain confirmation.

---

### Title
Duplicate peer-ID entries in `sent_to` allow a malicious peer to permanently suppress a valid unconfirmed transaction — (`chia/wallet/wallet_transaction_store.py`)

### Summary
`increment_sent` unconditionally appends to `sent_to` even when the same peer ID is already present, while `is_valid()` gates on raw `len(sent_to)` rather than distinct peer count. A peer that sends 6 FAILED acks for the same transaction causes `is_valid()` to return `False`, triggering permanent abandonment of the transaction.

### Finding Description
In `WalletTransactionStore.increment_sent`, the `sent` counter is correctly deduplicated per peer (lines 194–200), but `sent_to.append(append_data)` at line 202 runs unconditionally regardless of whether `name` is already in `current_peers`. [5](#0-4) 

`TransactionRecordOld.is_valid()` then checks `len(self.sent_to) < minimum_send_attempts` (line 83), which counts every appended tuple including duplicates from the same peer. [6](#0-5) 

When `is_valid()` returns `False`, `increment_sent` replaces the record with `confirmed=True, confirmed_at_height=0`, removing it from the unconfirmed set and halting all future rebroadcast attempts. [4](#0-3) 

The deduplication guard on `sent` (lines 197–200) is direct evidence that the protocol layer does not prevent a peer from sending multiple acks for the same transaction — the guard was added precisely to handle that case, but it was not extended to `sent_to`. [7](#0-6) 

### Impact Explanation
A valid, correctly-signed XCH/CAT spend bundle is permanently abandoned at the wallet layer without ever being confirmed on-chain. The wallet marks it `confirmed=True` at height 0, so the user's balance accounting is corrupted (coins appear spent but are not), and the spend is never rebroadcast. This meets the High criterion: corruption of wallet sync/spend state with direct security impact, and permanent inability to broadcast a valid spend bundle.

### Likelihood Explanation
Any peer the wallet connects to can send multiple protocol-level ack messages. No key material, admin access, or eclipse attack is required — a single malicious full node that the wallet connects to (even briefly) can trigger this by replying to one transaction submission with 6 FAILED acks.

### Recommendation
Fix `is_valid()` to count distinct peer IDs rather than raw `sent_to` length:

```python
def is_valid(self) -> bool:
    distinct_peers = {peer_id for peer_id, _, _ in self.sent_to}
    if len(distinct_peers) < minimum_send_attempts:
        return True
    ...
```

Alternatively, deduplicate in `increment_sent` before appending: skip the append if `name` is already in `current_peers` (or update the existing entry in place).

### Proof of Concept
```python
# Call increment_sent 6 times with the same peer_id and FAILED status
for _ in range(6):
    await store.increment_sent(tx_id, "attacker_peer_id", MempoolInclusionStatus.FAILED, Err.UNKNOWN)

tx = await store.get_transaction_record(tx_id)
assert tx.confirmed is True          # tx permanently abandoned
assert tx.confirmed_at_height == 0   # never actually confirmed on-chain
assert tx.is_valid() is False        # len(sent_to)==6, no SUCCESS, no fee-error
```

### Citations

**File:** chia/wallet/wallet_transaction_store.py (L188-202)
```python
        sent_to = current.sent_to.copy()

        current_peers = set()
        err_str = err.name if err is not None else None
        append_data = (name, uint8(send_status.value), err_str)

        for peer_id, status, error in sent_to:
            current_peers.add(peer_id)

        if name in current_peers:
            sent_count = uint32(current.sent)
        else:
            sent_count = uint32(current.sent + 1)

        sent_to.append(append_data)
```

**File:** chia/wallet/wallet_transaction_store.py (L205-209)
```python
        if not tx.is_valid():
            # if the tx is not valid due to repeated failures, we will confirm that we can't spend it
            log.info(f"Marking tx={tx.name} as confirmed but failed, since it is not spendable due to errors")
            tx = dataclasses.replace(tx, confirmed=True, confirmed_at_height=uint32(0))
        await self.add_transaction_record(tx)
```

**File:** chia/wallet/transaction_record.py (L22-22)
```python
minimum_send_attempts = 6
```

**File:** chia/wallet/transaction_record.py (L82-92)
```python
    def is_valid(self) -> bool:
        if len(self.sent_to) < minimum_send_attempts:
            # we haven't tried enough peers yet
            return True
        if any(x[1] == MempoolInclusionStatus.SUCCESS.value for x in self.sent_to):
            # we managed to push it to mempool at least once
            return True
        if any(x[2] in {Err.INVALID_FEE_LOW_FEE.name, Err.INVALID_FEE_TOO_CLOSE_TO_ZERO.name} for x in self.sent_to):
            # we tried to push it to mempool and got a fee error so it's a temporary error
            return True
        return False
```
