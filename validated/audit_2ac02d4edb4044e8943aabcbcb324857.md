The code path is concrete and fully traceable. Let me verify the exact guard sequence in `handle_cat` before rendering a verdict.

The full call chain is confirmed. Every guard is bypassable by the attacker. Here is the complete analysis:

---

### Title
Attacker-Controlled CRCAT Spend Permanently Corrupts Victim's CATWallet via Unchecked `convert_to_cr` in `handle_cat` — (`chia/wallet/wallet_state_manager.py`)

### Summary

`WalletStateManager.handle_cat` unconditionally converts a victim's `CATWallet` to a `CRCATWallet` when it observes any on-chain CRCAT spend for the same TAIL that creates a coin hinted to the victim's address. The `authorized_providers` and `proofs_checker` written into the new `CRCATInfo` are taken verbatim from the attacker's spend bundle with no validation against any user-configured policy. After conversion, the victim's existing plain CAT coins are unspendable through the wallet and all future wallet operations require a VC from attacker-controlled providers.

### Finding Description

**Entry point — `handle_cat`** (`chia/wallet/wallet_state_manager.py`, lines 1228–1312):

Guard 1 (line 1248): the hint of the new coin must resolve to a derivation record in the victim's puzzle store. The attacker satisfies this by sending the CRCAT output to the victim's standard puzzle hash — a normal "send to address" operation. [1](#0-0) 

Guard 2 (lines 1280–1289): `crcat.inner_puzzle_hash` must be in the victim's derivation records or equal the pending-approval-state hash. Because the attacker addressed the output to the victim's standard puzzle hash, `crcat.inner_puzzle_hash` equals that puzzle hash, which is already in the derivation store. Guard passes. [2](#0-1) 

Guard 3 (lines 1292–1295): checks for an existing `CRCATWallet` with the same TAIL. The victim has a `CATWallet`, not a `CRCATWallet`, so no match is found and execution continues. [3](#0-2) 

**No further guard exists.** Lines 1297–1312 find the victim's `CATWallet` by TAIL hash and call `convert_to_cr` directly with `crcat.authorized_providers` and `crcat.proofs_checker` extracted from the attacker's spend: [4](#0-3) 

**`convert_to_cr`** (`chia/wallet/vc_wallet/cr_cat_wallet.py`, lines 169–193) writes a new `CRCATInfo` containing the attacker's providers and checker into the persistent wallet DB and replaces the in-memory wallet object — with no check against any prior user configuration: [5](#0-4) 

The `authorized_providers` and `proofs_checker` fields in `CRCATInfo` are the sole authorization policy for all future CRCAT spends: [6](#0-5) 

`CRCAT.get_next_from_coin_spend` extracts these fields directly from the puzzle reveal and solution of the attacker's spend — they are fully attacker-controlled: [7](#0-6) 

### Impact Explanation

After `convert_to_cr` completes:

- The victim's wallet DB entry is permanently overwritten with `WalletType.CRCAT` and attacker-chosen `authorized_providers` / `proofs_checker`.
- The victim's existing plain CAT coins are still on-chain but the wallet now treats them as CRCAT coins. Spend attempts will fail because the coins lack a CR layer and the victim has no VC from the attacker-controlled providers.
- `claim_pending_approval_balance` and `_generate_unsigned_spendbundle` both gate on `self.info.authorized_providers` and `self.info.proofs_checker.flags`, so all wallet-level spend paths are blocked. [8](#0-7) 

This is permanent wallet state corruption with direct security impact: the victim loses the ability to spend their CAT holdings through the wallet without manual DB surgery or seed restoration.

### Likelihood Explanation

The attacker needs only:
1. A valid CRCAT coin for the same TAIL as the victim's `CATWallet`. This is achievable whenever the TAIL permits open minting (genesis-by-coin-id, everything-with-signature with attacker key, etc.) or whenever CRCAT coins for that TAIL already circulate and the attacker can acquire any amount.
2. Knowledge of the victim's receive address (publicly observable from prior transactions or the address book).
3. A single spend bundle submitted to the mempool — no privileged access, no key compromise, no social engineering.

### Recommendation

- In `handle_cat`, before calling `convert_to_cr`, verify that no existing `CATWallet` for the same TAIL is present (the current check only looks for existing `CRCATWallet` entries). If a plain `CATWallet` exists, do **not** auto-convert it based on an observed on-chain spend; require explicit user consent (e.g., an RPC call or UI confirmation).
- Alternatively, record the user's intended `authorized_providers` and `proofs_checker` at wallet creation time and reject any conversion whose on-chain parameters do not match.
- At minimum, emit a warning and skip conversion rather than silently overwriting the wallet type and authorization policy.

### Proof of Concept

```
1. Wallet A: create a CATWallet for TAIL T (plain CAT, no CR layer).
2. Attacker: launch a CRCAT for the same TAIL T with
       authorized_providers = [attacker_did]
       proofs_checker = ProofsChecker(["attacker_flag"])
3. Attacker: spend the CRCAT, sending 1 mojo to wallet A's receive address,
   hinting the output to wallet A's standard puzzle hash.
4. Farm the block.
5. Assert: wallet A's WalletType is now CRCAT.
6. Assert: wallet A's CRCATInfo.authorized_providers == [attacker_did].
7. Assert: wallet A cannot spend its original plain CAT coins (spend attempt
   raises "No VC exists that can approve spends for CR-CAT wallet").
```

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1246-1252)
```python
        hinted_coin = compute_spend_hints_and_additions(coin_spend)[0][coin_state.coin.name()]
        assert hinted_coin.hint is not None, f"hint missing for coin {hinted_coin.coin}"
        derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(hinted_coin.hint)

        if derivation_record is None:
            self.log.info(f"Received state for the coin that doesn't belong to us {coin_state}")
            return None
```

**File:** chia/wallet/wallet_state_manager.py (L1280-1289)
```python
                if (
                    await self.puzzle_store.get_derivation_record_for_puzzle_hash(crcat.inner_puzzle_hash) is None
                    and crcat.inner_puzzle_hash
                    != construct_pending_approval_state(
                        hinted_coin.hint,
                        uint64(coin_state.coin.amount),
                    ).get_tree_hash()
                ):
                    self.log.error(f"Unknown CRCAT inner puzzle, coin ID:{crcat.coin.name().hex()}")  # pragma: no cover
                    return None  # pragma: no cover
```

**File:** chia/wallet/wallet_state_manager.py (L1291-1295)
```python
                # Check if we already have a wallet
                for wallet_info in await self.get_all_wallet_info_entries(wallet_type=WalletType.CRCAT):
                    crcat_info: CRCATInfo = CRCATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    if crcat_info.limitations_program_hash == asset_id:
                        return WalletIdentifier(wallet_info.id, WalletType(wallet_info.type))
```

**File:** chia/wallet/wallet_state_manager.py (L1297-1312)
```python
            if wallet_type in {CRCATWallet, RCATWallet}:
                # We didn't find a matching alt-CAT wallet, but maybe we have a matching CAT wallet that we can convert
                for wallet_info in await self.get_all_wallet_info_entries(wallet_type=WalletType.CAT):
                    cat_info: CATInfo = CATInfo.from_bytes(bytes.fromhex(wallet_info.data))
                    found_cat_wallet = self.wallets[wallet_info.id]
                    assert isinstance(found_cat_wallet, CATWallet)
                    if cat_info.limitations_program_hash == asset_id:
                        if wallet_type is CRCATWallet:
                            assert crcat  # again, mypy isn't this smart
                            await CRCATWallet.convert_to_cr(
                                found_cat_wallet,
                                crcat.authorized_providers,
                                ProofsChecker.from_program(uncurry_puzzle(crcat.proofs_checker)),
                            )
                            self.state_changed("converted cat wallet to cr", wallet_info.id)
                            return WalletIdentifier(wallet_info.id, WalletType(WalletType.CRCAT))
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L181-193)
```python
        replace_self.info = cls.wallet_info_type(
            cat_wallet.cat_info.limitations_program_hash, None, authorized_providers, proofs_checker
        )
        await cat_wallet.wallet_state_manager.user_store.update_wallet(
            WalletInfo(
                cat_wallet.id(), cat_wallet.get_name(), uint8(WalletType.CRCAT.value), bytes(replace_self.info).hex()
            )
        )
        updated_wallet_info = await cat_wallet.wallet_state_manager.user_store.get_wallet_by_id(cat_wallet.id())
        assert updated_wallet_info is not None
        replace_self.wallet_info = updated_wallet_info

        cat_wallet.wallet_state_manager.wallets[cat_wallet.id()] = replace_self
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L706-710)
```python
        vc: VerifiedCredential | None = await vc_wallet.get_vc_with_provider_in_and_proofs(
            self.info.authorized_providers, self.info.proofs_checker.flags
        )
        if vc is None:  # pragma: no cover
            raise RuntimeError(f"No VC exists that can approve spends for CR-CAT wallet {self.id()}")
```

**File:** chia/wallet/cat_wallet/cat_info.py (L47-51)
```python
@streamable
@dataclass(frozen=True)
class CRCATInfo(CATInfo):
    authorized_providers: list[bytes32]
    proofs_checker: ProofsChecker
```

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L363-377)
```python
        else:
            # Otherwise the info we need will be in the puzzle reveal
            cr_first_curry, self_hash_and_innerpuz = potential_cr_layer.uncurry()
            _, authorized_providers_as_prog, proofs_checker = cr_first_curry.uncurry()[1].as_iter()
            _, inner_puzzle = self_hash_and_innerpuz.as_iter()
            inner_solution = solution.at("f").at("rrrrrrf")
            if conditions is None:
                conditions = inner_puzzle.run(inner_solution)
            inner_puzzle_hash: bytes32 = inner_puzzle.get_tree_hash()
            authorized_providers = [bytes32(p.as_atom()) for p in authorized_providers_as_prog.as_iter()]
            lineage_inner_puzhash = construct_cr_layer(
                authorized_providers,
                proofs_checker,
                inner_puzzle_hash,  # type: ignore
            ).get_tree_hash_precalc(inner_puzzle_hash)
```
