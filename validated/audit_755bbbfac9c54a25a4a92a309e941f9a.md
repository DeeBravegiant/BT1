### Title
Token-2022 Third-Party Metadata Pointer Allows Cross-Token Metadata Spoofing in `log_metadata` — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

`parse_metadata_account` verifies that the supplied metadata account's key matches the address stored in the mint's `MetadataPointer` extension, and that the account is owned by Metaplex, but **never checks that the deserialized metadata's `mint` field equals `self.mint.key()`**. Because the attacker controls the `MetadataPointer` value (they created the Token-2022 mint), they can point it at any existing Metaplex metadata account — including one belonging to a completely different, legitimate token — and `log_metadata` will emit a Wormhole VAA binding their mint to that token's name and symbol.

---

### Finding Description

There are two code paths in `process()`:

**Classic SPL path (safe):** [1](#0-0) 

The expected address is computed via `find_program_address([METADATA_SEED, MetaplexID, mint_pubkey], MetaplexID)` — a PDA that is cryptographically bound to `self.mint.key()`. An attacker cannot forge a different token's PDA for this mint, so this path is safe.

**Token-2022 third-party metadata pointer path (vulnerable):** [2](#0-1) 

Here the `address` argument to `parse_metadata_account` is `metadata_pointer.metadata_address.0` — a value the attacker freely chose when they initialized their Token-2022 mint. They can set it to the Metaplex metadata PDA of any existing token (e.g., USDC, wBTC).

Inside `parse_metadata_account`, the only checks are: [3](#0-2) 

1. `require_keys_eq!(metadata.key(), address)` — passes because the attacker passes the exact account they pointed to.
2. `metadata.owner == &MetaplexID` — passes because any legitimate Metaplex metadata account is owned by Metaplex.

There is **no** `require_keys_eq!(metadata.mint, self.mint.key())` check. The deserialized `MplMetadata` struct contains a `mint` field that identifies which mint the metadata belongs to, but it is never consulted.

The resulting payload: [4](#0-3) 

emits `token = attacker_mint` paired with `name`/`symbol` stolen from the victim token.

---

### Impact Explanation

On NEAR, the bridge registers canonical asset identity from these Wormhole `LogMetadata` messages. An attacker can register their worthless Token-2022 mint under the name and symbol of any established token (USDC, wETH, etc.), creating a counterfeit asset identity on NEAR. This breaks the invariant that a token's on-chain name/symbol corresponds to its actual mint, enabling impersonation attacks and asset-identity confusion across the bridge.

This falls under: **High — Asset-identity divergence that breaks backing guarantees or sends value to the wrong party.**

---

### Likelihood Explanation

The attack requires only:
- Creating a Token-2022 mint with a `MetadataPointer` extension pointing to a victim token's Metaplex metadata PDA (a standard, permissionless on-chain operation).
- Calling the public `log_metadata` instruction with that mint and the victim's metadata account.

No privileged access, no key compromise, no guardian collusion. Fully executable by any unprivileged actor.

---

### Recommendation

After deserializing the Metaplex metadata in `parse_metadata_account`, add a mint-binding check:

```rust
if metadata.owner == &MetaplexID {
    let data = metadata.try_borrow_data()?;
    let mpl = MplMetadata::try_deserialize(&mut data.as_ref())?;
    // ADD THIS:
    require_keys_eq!(
        mpl.mint,
        self.mint.key(),
        ErrorCode::InvalidTokenMetadataAddress,
    );
    Ok((mpl.name.clone(), mpl.symbol.clone()))
}
```

This mirrors the implicit binding that the classic SPL path achieves via PDA derivation, and closes the gap for the Token-2022 third-party metadata pointer case.

---

### Proof of Concept

1. Let `mint_B` be any existing SPL token with a Metaplex metadata account at `meta_B = find_program_address([METADATA_SEED, MetaplexID, mint_B], MetaplexID)` containing `name="USD Coin", symbol="USDC"`.
2. Attacker creates `mint_A` as a Token-2022 mint with `MetadataPointer` extension where `metadata_address = meta_B`.
3. Attacker calls `log_metadata` with `mint = mint_A`, `metadata = meta_B` (Token B's Metaplex account), `token_program = Token-2022`.
4. In `process()`: Token-2022 branch is taken → `metadata_pointer.metadata_address.0 == meta_B != mint_A` → third-party path → `parse_metadata_account(meta_B)`.
5. `require_keys_eq!(meta_B.key(), meta_B)` ✓, `meta_B.owner == MetaplexID` ✓, no mint check → returns `("USD Coin", "USDC")`.
6. Emitted Wormhole VAA: `token = mint_A, name = "USD Coin", symbol = "USDC"`.
7. NEAR receives and registers `mint_A` as "USD Coin / USDC", breaking canonical asset identity.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L78-89)
```rust
        require_keys_eq!(
            metadata.key(),
            address,
            ErrorCode::InvalidTokenMetadataAddress,
        );
        if metadata.owner == &MetaplexID {
            let data = metadata.try_borrow_data()?;
            let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
            Ok((metadata.name.clone(), metadata.symbol.clone()))
        } else {
            Ok((String::default(), String::default()))
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L104-106)
```rust
                } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
                    // Third-party metadata
                    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L117-127)
```rust
            self.parse_metadata_account(
                Pubkey::find_program_address(
                    &[
                        METADATA_SEED,
                        MetaplexID.as_ref(),
                        &self.mint.key().to_bytes(),
                    ],
                    &MetaplexID,
                )
                .0,
            )?
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L130-136)
```rust
        let payload = LogMetadataPayload {
            token: self.mint.key(),
            name: name.trim_end_matches('\0').to_string(),
            symbol: symbol.trim_end_matches('\0').to_string(),
            decimals: self.mint.decimals,
        }
        .serialize_for_near(())?;
```
