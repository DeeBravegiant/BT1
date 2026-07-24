### Title
Token-2022 MetadataPointer Mint-Metadata Binding Missing — Attacker Registers Arbitrary Name/Symbol on NEAR for Their Own Mint - (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

`parse_metadata_account` in the Token-2022 third-party metadata path verifies that the supplied metadata account's **address** matches the mint's `MetadataPointer` and that the account is **owned by MetaplexID**, but never checks that `metadata.mint == self.mint.key()`. An unprivileged attacker can create a Token-2022 mint with a `MetadataPointer` extension pointing to any existing Metaplex metadata account (e.g., USDC's), call `log_metadata`, and cause a `LogMetadataPayload` to be emitted over Wormhole that carries the attacker's mint pubkey paired with a legitimate token's name and symbol. NEAR then registers the attacker's mint under that stolen identity.

---

### Finding Description

In `log_metadata.rs`, the Token-2022 branch reads the `MetadataPointer` extension from the mint and, when the pointer targets a non-self address, delegates to `parse_metadata_account`:

```
} else if metadata_pointer.metadata_address.0 != Pubkey::default() {
    // Third-party metadata
    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
``` [1](#0-0) 

`parse_metadata_account` performs exactly two checks:

1. The passed-in account's runtime key equals the pointer address.
2. The account is owned by `MetaplexID`.

```rust
require_keys_eq!(metadata.key(), address, ErrorCode::InvalidTokenMetadataAddress);
if metadata.owner == &MetaplexID {
    let data = metadata.try_borrow_data()?;
    let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
    Ok((metadata.name.clone(), metadata.symbol.clone()))
``` [2](#0-1) 

There is **no check** that `metadata.mint == self.mint.key()`. The `MplMetadata` struct (deserialized from the Metaplex account) contains a `mint` field at bytes 1–33 of the account data (after the key byte), as confirmed by the test helper that manually serializes it:

```rust
// update_authority
data.extend_from_slice(update_authority.as_ref());
// mint
data.extend_from_slice(mint.as_ref());
``` [3](#0-2) 

The `metadata` account in the `LogMetadata` struct is declared `Option<UncheckedAccount<'info>>` with no on-chain constraints whatsoever:

```rust
/// CHECK: may be unitialized
pub metadata: Option<UncheckedAccount<'info>>,
``` [4](#0-3) 

The resulting payload uses the attacker's mint pubkey as the token identifier but the stolen name/symbol:

```rust
let payload = LogMetadataPayload {
    token: self.mint.key(),   // attacker's mint M
    name: name.trim_end_matches('\0').to_string(),   // T's name
    symbol: symbol.trim_end_matches('\0').to_string(), // T's symbol
    decimals: self.mint.decimals,
}
``` [5](#0-4) 

Note: the classic SPL token path is **not** affected because the Metaplex PDA is derived deterministically from `self.mint.key()`, making cross-mint spoofing impossible there. [6](#0-5) 

---

### Impact Explanation

The Wormhole message emitted carries `(mint=M, name="USD Coin", symbol="USDC", decimals=6)`. NEAR's bridge processes this message and registers mint M as a Solana token with USDC's identity. Any user who subsequently bridges M receives a NEAR-side wrapped token labeled "USD Coin / USDC" that is backed only by M (worthless attacker-controlled tokens), not by real USDC. This directly violates the invariant that the name/symbol in `LogMetadataPayload` must belong to the mint being registered, constituting **asset-identity divergence that breaks backing guarantees**.

---

### Likelihood Explanation

The attack requires only standard Token-2022 operations: creating a mint with a `MetadataPointer` extension is permissionless and costs only rent. The target Metaplex metadata account (e.g., USDC's) is already on-chain at a known, stable address. No privileged access, leaked keys, or external compromise is needed. The entire attack is executable in a single transaction.

---

### Recommendation

In `parse_metadata_account`, after deserializing the `MplMetadata`, add a binding check:

```rust
let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
require_keys_eq!(
    metadata.mint,
    self.mint.key(),
    ErrorCode::TokenMetadataMintMismatch,
);
Ok((metadata.name.clone(), metadata.symbol.clone()))
```

This ensures the Metaplex metadata account actually describes the mint being registered, closing the cross-mint spoofing path entirely.

---

### Proof of Concept

1. Let `T` = USDC mint on Solana. Its Metaplex metadata account `meta_T` is at the well-known PDA `[b"metadata", MetaplexID, T]` and contains `name="USD Coin", symbol="USDC"`.
2. Attacker creates a Token-2022 mint `M` with a `MetadataPointer` extension whose `metadata_address` is set to `meta_T`.
3. Attacker calls `log_metadata` with `mint=M`, `token_program=Token-2022`, and passes `meta_T` as the optional `metadata` account.
4. Execution path: `process()` reads `MetadataPointer` → `metadata_address == meta_T` (not `M`, not `default`) → calls `parse_metadata_account(meta_T)`.
5. `parse_metadata_account`: `meta_T.key() == meta_T` ✓; `meta_T.owner == MetaplexID` ✓; deserializes → returns `("USD Coin", "USDC")`. No mint-binding check fires.
6. `LogMetadataPayload { token: M, name: "USD Coin", symbol: "USDC", decimals: 6 }` is posted to Wormhole.
7. NEAR relayer picks up the VAA and registers `M` as `"USD Coin / USDC"` on NEAR.

A unit test using the existing `create_metaplex_metadata_account` helper (which writes an arbitrary `mint` field into the account data) can reproduce this in the Mollusk harness by creating `meta_T` with `mint=T` but passing it alongside a different `mint=M` in the instruction — the current code will accept it and return T's name/symbol.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L47-48)
```rust
    /// CHECK: may be unitialized
    pub metadata: Option<UncheckedAccount<'info>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L78-86)
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

**File:** solana/programs/bridge_token_factory/tests/mollusk/helpers.rs (L371-374)
```rust
    // update_authority
    data.extend_from_slice(update_authority.as_ref());
    // mint
    data.extend_from_slice(mint.as_ref());
```
