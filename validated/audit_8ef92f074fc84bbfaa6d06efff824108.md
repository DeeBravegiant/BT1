Let me verify the exact call chain and any guards I might have missed.

### Title
Attacker-Controlled `hidden_puzzle_hash` Injection via On-Chain Puzzle Reveal Backdoors Victim's RCATWallet — (`chia/wallet/wallet_state_manager.py`, `chia/wallet/cat_wallet/r_cat_wallet.py`)

---

### Summary

`WalletStateManager.handle_cat` extracts `hidden_puzzle_hash` directly from the on-chain puzzle reveal of a parent CAT spend and passes it, without any validation, to `RCATWallet.convert_to_revocable`. An unprivileged attacker who controls a CAT coin for the target TAIL can craft a spend whose inner puzzle is wrapped in a revocation layer curried with an arbitrary `hidden_puzzle_hash`. When the victim's wallet syncs this spend, it converts the victim's plain `CATWallet` into an `RCATWallet` whose `cat_info.hidden_puzzle_hash` is the attacker's value. All future RCAT coins received by the victim will embed the attacker's backdoor, and the attacker can spend them via the hidden path.

---

### Finding Description

**Step 1 — Attacker-controlled extraction in `handle_cat`**

In `wallet_state_manager.py`, when the wallet receives a coin whose puzzle hash does not match the standard CAT puzzle, it uncurries the *parent* coin's puzzle reveal and calls `match_revocation_layer`:

```python
# wallet_state_manager.py:1260-1263
uncurried_puzzle_reveal = uncurry_puzzle(coin_spend.puzzle_reveal)
if uncurried_puzzle_reveal.mod != CAT_MOD:
    return None
revocation_layer_match = match_revocation_layer(uncurry_puzzle(uncurried_puzzle_reveal.args.at("rrf")))
```

`match_revocation_layer` (vc_drivers.py:187-189) simply reads the first curried argument of the revocation layer — the `hidden_puzzle_hash` — with no validation:

```python
def match_revocation_layer(uncurried_puzzle: UncurriedPuzzle) -> tuple[bytes32, bytes32] | None:
    if uncurried_puzzle.mod == REVOCATION_LAYER:
        return bytes32(uncurried_puzzle.args.at("rf").as_atom()), ...
```

`coin_spend.puzzle_reveal` is the puzzle of the *parent* coin being spent — fully attacker-controlled.

**Step 2 — Unvalidated value passed to `convert_to_revocable`**

At lines 1313-1318, if the victim has a plain `CATWallet` for the same TAIL, the code calls:

```python
success = await RCATWallet.convert_to_revocable(
    found_cat_wallet,
    hidden_puzzle_hash=revocation_layer_match[0],  # attacker-controlled
)
```

**Step 3 — `convert_to_revocable` has no guard on the value**

The only guard in `convert_to_revocable` (r_cat_wallet.py:165) is:

```python
if not await cat_wallet.lineage_store.is_empty():
    return False
```

If the victim's lineage store is empty (they created the wallet but haven't received coins yet), the function proceeds to store the attacker-controlled value:

```python
# r_cat_wallet.py:174-176
replace_self.cat_info = cls.wallet_info_type(
    cat_wallet.cat_info.limitations_program_hash, None, hidden_puzzle_hash  # attacker value
)
```

It then deletes all existing derivation records and regenerates them:

```python
# r_cat_wallet.py:187-189
await cat_wallet.wallet_state_manager.puzzle_store.delete_wallet(cat_wallet.id())
result = await cat_wallet.wallet_state_manager.create_more_puzzle_hashes()
await result.commit(cat_wallet.wallet_state_manager)
```

**Step 4 — All future puzzle hashes embed the attacker's backdoor**

`puzzle_for_pk` (r_cat_wallet.py:192-194) uses `self.cat_info.hidden_puzzle_hash` for every derivation:

```python
def puzzle_for_pk(self, pubkey: G1Element) -> Program:
    inner_puzzle = create_revocation_layer(
        self.cat_info.hidden_puzzle_hash, self.standard_wallet.puzzle_hash_for_pk(pubkey)
    )
```

Every RCAT coin the victim subsequently receives will have `create_revocation_layer(attacker_hash, victim_p2_hash)` as its inner puzzle. The attacker, knowing the hidden puzzle behind `attacker_hash`, can spend any such coin via the hidden path without the victim's key.

---

### Impact Explanation

This is unauthorized coin control over all future RCAT coins received by the victim wallet. The attacker can drain any RCAT coin sent to the victim after the conversion, without ever touching the victim's private key. The victim's wallet will display the coins as received and spendable, but the attacker holds a parallel spend capability via the hidden path.

---

### Likelihood Explanation

Preconditions are realistic:
- The victim must have a plain `CATWallet` for the target TAIL with an empty lineage store. This is the normal state for any user who has added a CAT wallet in anticipation of receiving tokens but has not yet received any.
- The attacker only needs to hold any CAT coin for the same TAIL (or mint one if the TAIL allows it) and craft a spend with a revocation-layer-wrapped inner puzzle.
- The attack is fully on-chain and requires no interaction from the victim beyond having a syncing wallet.

---

### Recommendation

`handle_cat` must not trust `hidden_puzzle_hash` extracted from the on-chain puzzle reveal. The fix should derive the expected `hidden_puzzle_hash` from the wallet's own key material (e.g., a deterministic derivation from the wallet's master key or a fixed, well-known value for the RCAT scheme), and reject any spend whose revocation layer does not match that expected value. Alternatively, `convert_to_revocable` should validate that the supplied `hidden_puzzle_hash` matches a locally-derived, trusted value before proceeding with the wallet conversion.

---

### Proof of Concept

1. Victim creates a `CATWallet` for TAIL `T` (lineage store is empty).
2. Attacker holds a CAT coin for TAIL `T`. Attacker crafts a spend where the inner puzzle is `create_revocation_layer(attacker_hash, attacker_p2_hash)`.
3. The spend output is a coin with puzzle `CAT(T, create_revocation_layer(attacker_hash, victim_p2_hash))`, with hint set to `victim_p2_hash`.
4. Attacker submits the spend bundle; full node confirms it.
5. Victim's wallet syncs: `handle_cat` is called with the parent spend. Line 1263 extracts `attacker_hash` from the puzzle reveal. Line 1314-1317 calls `convert_to_revocable(found_cat_wallet, hidden_puzzle_hash=attacker_hash)`.
6. `convert_to_revocable` passes the lineage-empty check, stores `attacker_hash` in `cat_info`, deletes derivation records, and regenerates them via `puzzle_for_pk` — all using `attacker_hash`.
7. Victim receives a subsequent RCAT coin. Its puzzle is `CAT(T, create_revocation_layer(attacker_hash, victim_p2_hash))`.
8. Attacker spends the coin via the hidden path using the hidden puzzle behind `attacker_hash`. Spend succeeds without the victim's signature.

**Assertion**: `rcat_wallet.cat_info.hidden_puzzle_hash == attacker_hash` after step 6; hidden-path spend in step 8 succeeds on-chain. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1260-1263)
```python
                uncurried_puzzle_reveal = uncurry_puzzle(coin_spend.puzzle_reveal)
                if uncurried_puzzle_reveal.mod != CAT_MOD:
                    return None
                revocation_layer_match = match_revocation_layer(uncurry_puzzle(uncurried_puzzle_reveal.args.at("rrf")))
```

**File:** chia/wallet/wallet_state_manager.py (L1313-1318)
```python
                        elif wallet_type is RCATWallet:
                            success = await RCATWallet.convert_to_revocable(
                                found_cat_wallet,
                                # too complicated for mypy but semantics guarantee this not to be None
                                hidden_puzzle_hash=revocation_layer_match[0],  # type: ignore[index]
                            )
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L165-176)
```python
        if not await cat_wallet.lineage_store.is_empty():
            cat_wallet.log.error("Received a revocable CAT to a CAT wallet that already has CATs")
            return False
        replace_self = cls()
        replace_self.standard_wallet = cat_wallet.standard_wallet
        replace_self.log = logging.getLogger(cat_wallet.get_name())
        replace_self.log.info(f"Converting CAT wallet {cat_wallet.id()} to R-CAT wallet")
        replace_self.wallet_state_manager = cat_wallet.wallet_state_manager
        replace_self.lineage_store = cat_wallet.lineage_store
        replace_self.cat_info = cls.wallet_info_type(
            cat_wallet.cat_info.limitations_program_hash, None, hidden_puzzle_hash
        )
```

**File:** chia/wallet/cat_wallet/r_cat_wallet.py (L192-197)
```python
    def puzzle_for_pk(self, pubkey: G1Element) -> Program:
        inner_puzzle = create_revocation_layer(
            self.cat_info.hidden_puzzle_hash, self.standard_wallet.puzzle_hash_for_pk(pubkey)
        )
        cat_puzzle: Program = construct_cat_puzzle(CAT_MOD, self.cat_info.limitations_program_hash, inner_puzzle)
        return cat_puzzle
```

**File:** chia/wallet/vc_wallet/vc_drivers.py (L187-191)
```python
def match_revocation_layer(uncurried_puzzle: UncurriedPuzzle) -> tuple[bytes32, bytes32] | None:
    if uncurried_puzzle.mod == REVOCATION_LAYER:
        return bytes32(uncurried_puzzle.args.at("rf").as_atom()), bytes32(uncurried_puzzle.args.at("rrf").as_atom())
    else:
        return None  # pragma: no cover
```
