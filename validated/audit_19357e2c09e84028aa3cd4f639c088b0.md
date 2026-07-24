### Title
Token-2022 Metadata Pointer Allows Attacker to Bind Arbitrary Token's Name/Symbol to Attacker-Controlled Mint in LogMetadata VAA — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

In `LogMetadata::process`, when the mint uses Token-2022 and its `MetadataPointer` extension points to a third-party address (i.e., not the mint itself and not `Pubkey::default()`), the code calls `parse_metadata_account` with the raw pointer address — with **no check that the pointer is the canonical Metaplex PDA for the mint being registered**. An unprivileged attacker can create a Token-2022 mint whose `metadata_pointer` points to USDC's (or any other token's) Metaplex metadata PDA, then call `log_metadata` passing that PDA as the `metadata` account. The resulting Wormhole VAA will carry `token = attacker_mint`, `name = "USD Coin"`, `symbol = "USDC"`, causing NEAR to deploy a wrapped token with a legitimate token's identity bound to the attacker's worthless mint.

---

### Finding Description

**Vulnerable branch — Token-2022 third-party metadata pointer:** [1](#0-0) 

```rust
if let Ok(metadata_pointer) = mint_with_extension.get_extension::<MetadataPointer>() {
    if metadata_pointer.metadata_address.0 == self.mint.key() {
        // Embedded metadata — safe, reads from the mint itself
        ...
    } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
        // Third-party metadata — NO canonical PDA check here
        self.parse_metadata_account(metadata_pointer.metadata_address.0)?
    }
```

`parse_metadata_account` only verifies that the caller-supplied `metadata` account key matches the pointer address stored in the mint extension: [2](#0-1) 

```rust
fn parse_metadata_account(&self, address: Pubkey) -> Result<(String, String)> {
    let metadata = self.metadata.as_ref()...to_account_info();
    require_keys_eq!(metadata.key(), address, ErrorCode::InvalidTokenMetadataAddress);
    if metadata.owner == &MetaplexID {
        let data = metadata.try_borrow_data()?;
        let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
        Ok((metadata.name.clone(), metadata.symbol.clone()))
    } else { ... }
}
```

This check only confirms the caller passed the account that the mint's pointer field names — it does **not** confirm that the pointer is the canonical Metaplex PDA derived from the attacker's own mint pubkey.

**Contrast with the safe classic-SPL path**, which always derives the canonical PDA from `self.mint.key()`: [3](#0-2) 

```rust
self.parse_metadata_account(
    Pubkey::find_program_address(
        &[METADATA_SEED, MetaplexID.as_ref(), &self.mint.key().to_bytes()],
        &MetaplexID,
    ).0,
)?
```

The Token-2022 branch has no equivalent derivation/binding check.

**The VAA payload** then encodes the attacker's mint pubkey alongside the stolen name/symbol: [4](#0-3) 

```rust
let payload = LogMetadataPayload {
    token: self.mint.key(),   // attacker's mint
    name: name...,            // USDC's name
    symbol: symbol...,        // USDC's symbol
    decimals: self.mint.decimals,
}.serialize_for_near(())?;
self.common.post_message(payload)?;
``` [5](#0-4) 

---

### Impact Explanation

NEAR receives a guardian-signed VAA asserting that the attacker's mint address has the name "USD Coin" and symbol "USDC". The token deployer on NEAR uses this metadata to deploy a wrapped NEP-141 token: [6](#0-5) 

The deployed token's `FungibleTokenMetadata` will carry the spoofed name/symbol: [7](#0-6) 

This creates a NEAR-side token that is indistinguishable by name/symbol from the real USDC bridge token, but is backed by the attacker's worthless mint. Users, wallets, and DEX UIs that display `name`/`symbol` for token identification will see "USD Coin / USDC" for a token that has no real backing. This is a concrete asset-identity divergence that breaks the backing guarantee and enables phishing/social-engineering attacks against bridge users.

---

### Likelihood Explanation

The attack requires only:
1. Creating a Token-2022 mint (permissionless, costs ~0.01 SOL)
2. Setting its `metadata_pointer` extension to any existing Metaplex PDA (e.g., USDC's)
3. Calling `log_metadata` with that PDA as the `metadata` account

No privileged access, no key compromise, no guardian collusion. Fully executable on-chain by any unprivileged actor.

---

### Recommendation

In the Token-2022 third-party metadata pointer branch, enforce that the pointer address equals the canonical Metaplex PDA derived from the mint being registered — exactly as the classic-SPL path does:

```rust
} else if metadata_pointer.metadata_address.0 != Pubkey::default() {
    let canonical = Pubkey::find_program_address(
        &[METADATA_SEED, MetaplexID.as_ref(), &self.mint.key().to_bytes()],
        &MetaplexID,
    ).0;
    require_keys_eq!(
        metadata_pointer.metadata_address.0,
        canonical,
        ErrorCode::InvalidTokenMetadataAddress,
    );
    self.parse_metadata_account(canonical)?
}
```

This ensures a Token-2022 mint can only reference its own Metaplex PDA, not another token's.

---

### Proof of Concept

1. Derive USDC's Metaplex PDA: `find_program_address([b"metadata", MetaplexID, usdc_mint], MetaplexID)` → `usdc_meta_pda`.
2. Create a new Token-2022 mint with `MetadataPointer` extension set to `usdc_meta_pda`.
3. Call `log_metadata` with `mint = attacker_mint`, `metadata = usdc_meta_pda`.
4. `process()` enters the `else if metadata_pointer.metadata_address.0 != Pubkey::default()` branch.
5. `parse_metadata_account(usdc_meta_pda)` is called; `require_keys_eq!(usdc_meta_pda, usdc_meta_pda)` passes.
6. USDC's `name = "USD Coin"`, `symbol = "USDC"` are read.
7. The emitted VAA payload contains `token = attacker_mint`, `name = "USD Coin"`, `symbol = "USDC"`.
8. Assert: VAA `name` == "USD Coin" and `symbol` == "USDC" while `token` == `attacker_mint` ≠ `usdc_mint`. Invariant violated.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L72-89)
```rust
    fn parse_metadata_account(&self, address: Pubkey) -> Result<(String, String)> {
        let metadata = self
            .metadata
            .as_ref()
            .ok_or_else(|| error!(ErrorCode::TokenMetadataNotProvided))?
            .to_account_info();
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L98-106)
```rust
            if let Ok(metadata_pointer) = mint_with_extension.get_extension::<MetadataPointer>() {
                if metadata_pointer.metadata_address.0 == self.mint.key() {
                    // Embedded metadata
                    let metadata =
                        mint_with_extension.get_variable_len_extension::<TokenMetadata>()?;
                    (metadata.name, metadata.symbol)
                } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
                    // Third-party metadata
                    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L117-128)
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
        };
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L130-138)
```rust
        let payload = LogMetadataPayload {
            token: self.mint.key(),
            name: name.trim_end_matches('\0').to_string(),
            symbol: symbol.trim_end_matches('\0').to_string(),
            decimals: self.mint.decimals,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;
```

**File:** solana/programs/bridge_token_factory/src/state/message/log_metadata.rs (L8-13)
```rust
pub struct LogMetadataPayload {
    pub token: Pubkey,
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}
```

**File:** near/omni-bridge/src/lib.rs (L200-203)
```rust
#[ext_contract(ext_deployer)]
pub trait TokenDeployer {
    fn deploy_token(&self, account_id: AccountId, metadata: BasicMetadata) -> Promise;
}
```

**File:** near/omni-token/src/lib.rs (L67-77)
```rust
                b"m".to_vec(),
                Some(&FungibleTokenMetadata {
                    spec: FT_METADATA_SPEC.to_string(),
                    name: metadata.name,
                    symbol: metadata.symbol,
                    icon: None,
                    reference: None,
                    reference_hash: None,
                    decimals: metadata.decimals,
                }),
            ),
```
