The code is clear enough to analyze without additional lookups. Let me trace the exact exploit path.

The `grep_search` confirms there is **no `metadata.mint` check anywhere** in the production Solana bridge code. The vulnerability is valid.

---

### Title
Missing `metadata.mint` Validation in `parse_metadata_account` Enables Cross-Token Identity Spoofing via Token-2022 MetadataPointer — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

`parse_metadata_account` deserializes a Metaplex metadata account and returns its `name`/`symbol` without verifying that `metadata.mint` equals the mint being registered. For Token-2022 mints, the metadata address is taken directly from the mint's `MetadataPointer` extension with no constraint that it must be the canonical Metaplex PDA for that mint. An unprivileged attacker can create a Token-2022 mint whose `MetadataPointer` points to a victim token's Metaplex PDA, then call `log_metadata` to emit a Wormhole payload that carries the attacker's mint address paired with the victim's name and symbol.

---

### Finding Description

In `process()`, the Token-2022 branch reads the metadata address directly from the mint's `MetadataPointer` extension and passes it to `parse_metadata_account`: [1](#0-0) 

`parse_metadata_account` then performs two checks:

1. The supplied `metadata` account key equals the address from the pointer — [2](#0-1) 
2. The account owner is `MetaplexID` — [3](#0-2) 

After passing both checks it returns `metadata.name` and `metadata.symbol` with **no check that `metadata.mint` equals `self.mint.key()`**: [4](#0-3) 

Contrast this with the classic SPL path, where the address is derived as `find_program_address([METADATA_SEED, MetaplexID, mint.key()], MetaplexID)`, cryptographically binding the metadata account to the specific mint: [5](#0-4) 

The Token-2022 path has no equivalent binding.

The `metadata` field in `LogMetadata` is declared `Option<UncheckedAccount<'info>>` with the comment `/// CHECK: may be unitialized` and carries zero on-chain constraints: [6](#0-5) 

---

### Impact Explanation

The resulting Wormhole payload is:

```
token:   attacker's mint A   ← correct mint address
name:    victim token B's name
symbol:  victim token B's symbol
decimals: attacker's mint A's decimals
``` [7](#0-6) 

On NEAR, the attacker's mint is registered under the victim's human-readable identity. This is asset-identity divergence: users and UIs that discover tokens by name/symbol will interact with the attacker's mint believing it to be the victim token, breaking backing guarantees and potentially routing value to the wrong token.

---

### Likelihood Explanation

The attack requires only:
- Creating a Token-2022 mint (permissionless, costs ~0.01 SOL)
- Setting `MetadataPointer` to any existing Metaplex PDA (done at mint init time, no special privilege)
- Calling the public `log_metadata` instruction

No privileged role, leaked key, or external dependency compromise is needed. Any existing token with a Metaplex metadata account is a potential victim.

---

### Recommendation

After deserializing the Metaplex account, assert that its `mint` field matches the mint being registered:

```rust
if metadata.owner == &MetaplexID {
    let data = metadata.try_borrow_data()?;
    let mpl = MplMetadata::try_deserialize(&mut data.as_ref())?;
    require_keys_eq!(
        mpl.mint,
        self.mint.key(),
        ErrorCode::InvalidTokenMetadataAddress,
    );
    Ok((mpl.name.clone(), mpl.symbol.clone()))
}
```

This mirrors the implicit binding that the classic SPL path achieves through PDA derivation.

---

### Proof of Concept

```rust
// 1. Attacker creates Token-2022 mint A with MetadataPointer → victim_metaplex_pda
//    victim_metaplex_pda = find_program_address(
//        [METADATA_SEED, MetaplexID, victim_mint_B], MetaplexID)
//    This PDA is owned by MetaplexID and contains victim B's name/symbol.

// 2. Attacker calls log_metadata:
//    accounts.mint     = mint_A  (attacker's Token-2022 mint)
//    accounts.metadata = victim_metaplex_pda

// 3. Inside process():
//    metadata_pointer.metadata_address.0 == victim_metaplex_pda  (set by attacker)
//    → calls parse_metadata_account(victim_metaplex_pda)

// 4. Inside parse_metadata_account():
//    require_keys_eq!(metadata.key(), victim_metaplex_pda)  ✓ (attacker passed it)
//    metadata.owner == MetaplexID                           ✓ (it's a real Metaplex PDA)
//    // NO check: metadata.mint == mint_A
//    returns (victim_B_name, victim_B_symbol)               ← spoofed

// 5. Wormhole payload emitted:
//    { token: mint_A, name: "VictimToken", symbol: "VTK", decimals: X }
//    → NEAR registers mint_A under victim's identity
```

The unit test in `helpers.rs` shows `create_metaplex_metadata_account` serializes the `mint` field at a fixed offset in the account data — confirming the `mint` field is present and readable, but `parse_metadata_account` never reads it. [8](#0-7)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L47-48)
```rust
    /// CHECK: may be unitialized
    pub metadata: Option<UncheckedAccount<'info>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L78-82)
```rust
        require_keys_eq!(
            metadata.key(),
            address,
            ErrorCode::InvalidTokenMetadataAddress,
        );
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L83-83)
```rust
        if metadata.owner == &MetaplexID {
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L85-86)
```rust
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

**File:** solana/programs/bridge_token_factory/tests/mollusk/helpers.rs (L361-374)
```rust
pub fn create_metaplex_metadata_account(
    update_authority: &Pubkey,
    mint: &Pubkey,
    name: &str,
    symbol: &str,
) -> Account {
    let metaplex = metaplex_id();
    let mut data = Vec::with_capacity(256);
    // Key: MetadataV1 = 4
    data.push(4);
    // update_authority
    data.extend_from_slice(update_authority.as_ref());
    // mint
    data.extend_from_slice(mint.as_ref());
```
