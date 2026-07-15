Let me look at the additions merkle set structure and how the coin list is used after validation.

### Title
Wallet Accepts Fabricated Coin State via Puzzle-Hash-Level Merkle Proof Without Verifying Specific Coin Inclusion — (`chia/wallet/util/wallet_sync_utils.py`)

---

### Summary

`validate_additions` proves that *some* coins with a given puzzle hash are included in a block, but never checks that the *specific coin* from the `CoinState` being validated is among those coins. A malicious peer can exploit this to make the wallet accept a completely fabricated coin as blockchain-proven, inflating the wallet's XCH/CAT/NFT balance.

---

### Finding Description

The additions merkle set in a Chia block encodes, for each puzzle hash PH, two leaves:
- `PH` itself
- `hash_coin_ids([all coin IDs with PH])`

This is confirmed in block creation: [1](#0-0) 

When the wallet validates a received `CoinState`, it calls `request_and_validate_additions` with `coin_state.coin.puzzle_hash`: [2](#0-1) 

`request_and_validate_additions` sends a `RequestAdditions` for the puzzle hash, receives a `RespondAdditions`, and passes the peer-supplied `additions_res.coins` and `additions_res.proofs` directly to `validate_additions`: [3](#0-2) 

`validate_additions` in the proof path verifies:
1. `hash_coin_ids([c.name() for c in coin_list])` is in the merkle tree
2. `coin_ph` (the puzzle hash) is in the merkle tree [4](#0-3) 

**What it never checks**: that `coin_state.coin` is actually present in the returned `coin_list`. After `validate_additions` returns `True`, `validate_received_state_from_peer` unconditionally returns `True` and the coin state is added to the wallet's database: [5](#0-4) 

---

### Impact Explanation

A malicious peer can:

1. Find any real block H that contains **coin A** with puzzle hash PH (one of the wallet's puzzle hashes — e.g., from a prior legitimate payment).
2. Fabricate **coin B** = `Coin(fake_parent, PH, large_amount)` — coin B does not exist on-chain.
3. Send `CoinState(coin_B, None, H)` to the wallet, claiming coin B was created at height H.
4. When the wallet sends `RequestAdditions(H, header_hash, [PH])`, respond with `coin_list=[coin_A]` and a **valid** merkle proof for coin A.
5. `validate_additions` computes `hash_coin_ids([coin_A.name()])`, confirms it is in the merkle tree (it is — coin A is real), and returns `True`.
6. The wallet adds coin B to its coin store, inflating the displayed balance by `large_amount`.

The wallet's `validate_block_inclusion` check (line 1583) also passes because block H is a real block in the canonical chain. No cryptographic check is ever performed to confirm coin B itself is in the block.

The result is wallet balance inflation for XCH, CATs, NFTs, or any coin type tracked by puzzle hash. The wallet cannot spend the fake coin (the full node would reject it), but the corrupted coin store causes incorrect balance display and can disrupt transaction construction.

---

### Likelihood Explanation

- Requires only a malicious peer connection — no keys, no admin access, no broken crypto.
- The precondition (a block containing any coin with the target puzzle hash) is trivially satisfied for any wallet that has ever received funds, or by the attacker sending a tiny payment to the target puzzle hash first.
- The attack is fully local-testable with a patched peer handler.

---

### Recommendation

After `validate_additions` returns `True`, verify that `coin_state.coin` is present in the returned `coin_list` for the matching puzzle hash entry. Concretely, in `request_and_validate_additions` (or in `validate_received_state_from_peer` after the call), check:

```python
# After validate_additions returns True:
coin_found = any(
    coin_state.coin in coin_list
    for (ph, coin_list) in additions_res.coins
    if ph == coin_state.coin.puzzle_hash
)
if not coin_found:
    return False
```

This closes the gap between "some coin with this puzzle hash is in the block" and "the specific coin we are validating is in the block."

---

### Proof of Concept

```python
# Setup: block H contains coin_A with puzzle_hash PH (real, on-chain)
# Attacker fabricates coin_B with same PH but different parent/amount

coin_A = Coin(real_parent, PH, uint64(100))
coin_B = Coin(fake_parent, PH, uint64(999_999_999_999))  # fabricated

# Build real merkle set for block H (contains only coin_A under PH)
leafs = [PH, hash_coin_ids([coin_A.name()])]
merkle_set = MerkleSet(leafs)
root = merkle_set.get_root()  # matches block H's additions_root

# Peer generates valid proof for coin_A
_, ph_proof = merkle_set.is_included_already_hashed(PH)
_, coin_list_proof = merkle_set.is_included_already_hashed(hash_coin_ids([coin_A.name()]))

# Peer returns coin_list=[coin_A] (not coin_B!) with valid proofs
result = validate_additions(
    coins=[(PH, [coin_A])],          # coin_A, not coin_B
    proofs=[(PH, ph_proof, coin_list_proof)],
    root=root,
)
assert result is True  # passes — coin_B is never checked

# Wallet now adds coin_B (fake, large amount) to its coin store
```

`validate_additions` returns `True` even though `coin_B` (the coin in the `CoinState`) is not in the block. The invariant is violated.

### Citations

**File:** chia/consensus/block_creation.py (L214-225)
```python
        for coin in tx_additions:
            if coin.puzzle_hash in puzzlehash_coin_map:
                puzzlehash_coin_map[coin.puzzle_hash].append(coin.name())
            else:
                puzzlehash_coin_map[coin.puzzle_hash] = [coin.name()]

        # Addition Merkle set contains puzzlehash and hash of all coins with that puzzlehash
        for puzzle, coin_ids in puzzlehash_coin_map.items():
            additions_merkle_items.append(puzzle)
            additions_merkle_items.append(hash_coin_ids(coin_ids))

        additions_root = bytes32(compute_merkle_set_root(additions_merkle_items))
```

**File:** chia/wallet/wallet_node.py (L1563-1578)
```python
        # get proof of inclusion
        validate_additions_result = await request_and_validate_additions(
            peer,
            peer_request_cache,
            state_block.height,
            state_block.header_hash,
            coin_state.coin.puzzle_hash,
            state_block.foliage_transaction_block.additions_root,
        )

        if validate_additions_result is None:
            return False
        if validate_additions_result is False:
            self.log.warning("Validate false 1")
            await peer.close(9999)
            return False
```

**File:** chia/wallet/wallet_node.py (L1642-1644)
```python
        peer_request_cache.add_to_states_validated(coin_state)

        return True
```

**File:** chia/wallet/util/wallet_sync_utils.py (L108-127)
```python
    else:
        if len(coins) != len(proofs):
            return False
        for (coin_ph, coin_list), (proof_ph, puzzle_hash_proof, coin_list_proof) in zip(coins, proofs):
            if coin_ph != proof_ph:
                return False
            if len(coin_list) == 0:
                # Verify exclusion proof for puzzle hash
                if not confirm_not_included_already_hashed(root, coin_ph, puzzle_hash_proof):
                    return False
                continue
            if coin_list_proof is None:
                return False
            # Verify inclusion proof for coin list
            if not confirm_included_already_hashed(root, hash_coin_ids([c.name() for c in coin_list]), coin_list_proof):
                return False
            # Verify inclusion proof for puzzle hash
            if not confirm_included_already_hashed(root, coin_ph, puzzle_hash_proof):
                return False
    return True
```

**File:** chia/wallet/util/wallet_sync_utils.py (L197-224)
```python
async def request_and_validate_additions(
    peer: WSChiaConnection,
    peer_request_cache: PeerRequestCache,
    height: uint32,
    header_hash: bytes32,
    puzzle_hash: bytes32,
    additions_root: bytes32,
) -> bool | None:
    if peer_request_cache.in_additions_in_block(header_hash, puzzle_hash):
        return True
    additions_request = RequestAdditions(height, header_hash, [puzzle_hash])
    additions_res: RespondAdditions | RejectAdditionsRequest | None = await peer.call_api(
        FullNodeAPI.request_additions, additions_request
    )
    if additions_res is None or isinstance(additions_res, RejectAdditionsRequest):
        log.info(
            f"Failed to obtain additions for height {height} header hash {header_hash} "
            f"puzzle hash {puzzle_hash} from peer {peer.peer_node_id} / {peer.peer_info.host} "
            f"version {peer.version} response: {additions_res}"
        )
        return None
    result: bool = validate_additions(
        additions_res.coins,
        additions_res.proofs,
        additions_root,
    )
    peer_request_cache.add_to_additions_in_block(header_hash, puzzle_hash, height)
    return result
```
