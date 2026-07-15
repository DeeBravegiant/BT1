Looking at the code path carefully across `wallet_state_manager.py` and `cr_cat_drivers.py`:

**Tracing the attack path:**

**Step 1 — Entry via `handle_cat`**

The function is invoked during wallet sync when a coin state is received whose parent is a CAT coin. [1](#0-0) 

**Step 2 — Hint-based ownership check (Guard 1)**

The hint from the coin spend is looked up in the victim's derivation store. If found, execution continues. [2](#0-1) 

This is bypassable: victim puzzle hashes are public on-chain. The attacker sets the hint in their `CREATE_COIN` condition to any known victim puzzle hash.

**Step 3 — CRCAT detection via `get_next_from_coin_spend`**

When the coin's puzzle hash doesn't match a standard CAT, `CRCAT.get_next_from_coin_spend` is called on the parent spend. [3](#0-2) 

In the non-CR-CAT parent path, `authorized_providers` and `proofs_checker` are read directly from a **REMARK opcode-1 condition** in the inner puzzle output — which the attacker fully controls since they own the parent CAT coin being spent: [4](#0-3) 

**Step 4 — Inner puzzle hash check (Guard 2)**

The code checks that `crcat.inner_puzzle_hash` is in the victim's derivation store or matches the pending approval state: [5](#0-4) 

The attacker sets `new_inner_puzzle_hash` in the REMARK condition to the same known victim puzzle hash used as the hint. This passes the guard.

**Step 5 — Irreversible conversion at line 1306**

No existing CRCAT wallet matches the `asset_id`, but a CATWallet does. `convert_to_cr` is called with attacker-controlled `authorized_providers` and `proofs_checker`: [6](#0-5) 

`convert_to_cr` overwrites the wallet record in the database, changing the type from `CAT` to `CRCAT` and embedding the attacker's credential parameters: [7](#0-6) 

---

### Title
Attacker-Controlled CATWallet-to-CRCATWallet Conversion via Crafted Coin Spend — (`chia/wallet/wallet_state_manager.py`)

### Summary
An unprivileged attacker who holds any amount of a CAT token (same `asset_id` as the victim's `CATWallet`) can craft a valid on-chain CAT spend that causes the victim's wallet to irreversibly convert its `CATWallet` to a `CRCATWallet` with attacker-specified `authorized_providers` and `proofs_checker`, without any user action.

### Finding Description
`handle_cat` in `wallet_state_manager.py` processes incoming coin states during sync. When a coin's puzzle hash does not match a standard CAT but `CRCAT.get_next_from_coin_spend` succeeds, the wallet proceeds to convert an existing `CATWallet` (matching by `asset_id`) to a `CRCATWallet` using the `authorized_providers` and `proofs_checker` extracted from the parent spend.

In the non-CR-CAT parent path of `get_next_from_coin_spend`, these two critical fields are read from a **REMARK (opcode 1) condition** in the inner puzzle output:

```python
for condition in conditions.as_iter():
    if condition.at("f") == Program.to(1):
        new_inner_puzzle_hash = bytes32(condition.at("rf").as_atom())
        authorized_providers_as_prog = condition.at("rrf")
        proofs_checker = condition.at("rrrf")
        break
``` [8](#0-7) 

The attacker controls the inner puzzle of their own CAT spend and can emit any REMARK condition. The two guards present are:

1. **Hint check** (line 1248): The hint must resolve to a derivation record. Bypassed by using any known victim puzzle hash as the hint — these are public on-chain.
2. **Inner puzzle hash check** (lines 1280–1289): `crcat.inner_puzzle_hash` must be in the victim's derivation store. Bypassed by setting `new_inner_puzzle_hash` in the REMARK condition to the same known victim puzzle hash.

There is **no signature check, no user confirmation, and no validation** that the `authorized_providers` or `proofs_checker` are legitimate or match any existing wallet policy before `convert_to_cr` is called.

### Impact Explanation
`convert_to_cr` permanently overwrites the wallet database record, changing the wallet type from `WalletType.CAT` to `WalletType.CRCAT` and embedding the attacker's credential parameters. The victim's existing CAT coins become unspendable through the wallet because the wallet now generates CRCAT spend bundles requiring a VC from the attacker-controlled provider. This is a permanent, irreversible wallet state corruption achievable by any party holding any amount of the same CAT token.

### Likelihood Explanation
The attacker only needs to:
- Hold any nonzero amount of the target CAT token (publicly tradeable)
- Know one victim puzzle hash (public on-chain)
- Craft a single valid CAT spend with a REMARK condition and a `CREATE_COIN` output hinted to the victim

No privileged access, leaked keys, or broken cryptography is required.

### Recommendation
Before calling `convert_to_cr`, validate that the `authorized_providers` and `proofs_checker` in the detected CRCAT match a user-approved policy, or require explicit user confirmation before any wallet type conversion. At minimum, wallet type conversions should never be triggered automatically by externally-observed coin spends without user consent. The conversion logic at lines 1297–1312 should be gated behind an explicit user action or a pre-registered allowlist of trusted CR parameters.

### Proof of Concept
1. Victim has a `CATWallet` for `asset_id = X` with a known puzzle hash `P` (observed on-chain).
2. Attacker acquires any amount of CAT-X tokens.
3. Attacker crafts a CAT-X inner puzzle that outputs:
   - `(1 P [attacker_provider] attacker_proofs_checker)` — REMARK condition
   - `(51 crcat_puzzle_hash amount [P])` — CREATE_COIN with hint `P`, where `crcat_puzzle_hash = construct_cat_puzzle(CAT_MOD, X, construct_cr_layer([attacker_provider], attacker_proofs_checker, P))`
4. Attacker broadcasts and confirms this spend on-chain.
5. Victim's wallet syncs, receives the hinted coin state, calls `handle_cat`.
6. Guards at lines 1248 and 1280 both pass (hint = `P`, inner puzzle hash = `P`, both in victim's derivation store).
7. `CRCATWallet.convert_to_cr` is called with `authorized_providers = [attacker_provider]`.
8. Victim's `CATWallet` is now a `CRCATWallet` requiring attacker-controlled credentials; existing CAT funds are unspendable via the wallet.

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1228-1236)
```python
    async def handle_cat(
        self,
        parent_data: CATCoinData,
        parent_coin_state: CoinState,
        coin_state: CoinState,
        coin_spend: CoinSpend,
        peer: WSChiaConnection,
        fork_height: uint32 | None,
    ) -> WalletIdentifier | None:
```

**File:** chia/wallet/wallet_state_manager.py (L1246-1252)
```python
        hinted_coin = compute_spend_hints_and_additions(coin_spend)[0][coin_state.coin.name()]
        assert hinted_coin.hint is not None, f"hint missing for coin {hinted_coin.coin}"
        derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(hinted_coin.hint)

        if derivation_record is None:
            self.log.info(f"Received state for the coin that doesn't belong to us {coin_state}")
            return None
```

**File:** chia/wallet/wallet_state_manager.py (L1258-1275)
```python
            if cat_puzzle.get_tree_hash() != coin_state.coin.puzzle_hash:
                # Check if it is a special type of CAT
                uncurried_puzzle_reveal = uncurry_puzzle(coin_spend.puzzle_reveal)
                if uncurried_puzzle_reveal.mod != CAT_MOD:
                    return None
                revocation_layer_match = match_revocation_layer(uncurry_puzzle(uncurried_puzzle_reveal.args.at("rrf")))
                if revocation_layer_match is not None:
                    wallet_type = RCATWallet
                else:
                    try:
                        next_crcats = CRCAT.get_next_from_coin_spend(coin_spend)

                    except ValueError:
                        return None

                    crcat = next(crc for crc in next_crcats if crc.coin == coin_state.coin)

                    wallet_type = CRCATWallet
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

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L345-361)
```python
        if potential_cr_layer.uncurry()[0].uncurry()[0] != CREDENTIAL_RESTRICTION:
            # If the previous spend is not a CR-CAT:
            # we look for a remark condition that tells us the authorized_providers and proofs_checker
            inner_solution: Program = solution.at("f")
            if conditions is None:
                conditions = potential_cr_layer.run(inner_solution)
            for condition in conditions.as_iter():
                if condition.at("f") == Program.to(1):
                    new_inner_puzzle_hash = bytes32(condition.at("rf").as_atom())
                    authorized_providers_as_prog: Program = condition.at("rrf")
                    proofs_checker: Program = condition.at("rrrf")
                    break
            else:
                raise ValueError(
                    "Previous spend was not a CR-CAT, nor did it properly remark the CR params"
                )  # pragma: no cover
            authorized_providers = [bytes32(p.as_atom()) for p in authorized_providers_as_prog.as_iter()]
```

**File:** chia/wallet/vc_wallet/cr_cat_wallet.py (L169-193)
```python
    @classmethod
    async def convert_to_cr(
        cls,
        cat_wallet: CATWallet,
        authorized_providers: list[bytes32],
        proofs_checker: ProofsChecker,
    ) -> None:
        replace_self = cls()
        replace_self.standard_wallet = cat_wallet.standard_wallet
        replace_self.log = logging.getLogger(cat_wallet.get_name())
        replace_self.log.info(f"Converting CAT wallet {cat_wallet.id()} to CR-CAT wallet")
        replace_self.wallet_state_manager = cat_wallet.wallet_state_manager
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
