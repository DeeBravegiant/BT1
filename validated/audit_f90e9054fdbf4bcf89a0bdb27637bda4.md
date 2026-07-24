### Title
Token-2022 MetadataPointer Cross-Mint Identity Hijack in `log_metadata` — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

`LogMetadata::parse_metadata_account` does not verify that the deserialized Metaplex metadata account's `mint` field matches the Token-2022 mint being registered. An attacker can create a Token-2022 mint whose `MetadataPointer` extension points to a legitimate token's Metaplex metadata PDA, causing the bridge to emit a Wormhole message that binds the attacker's mint address to a stolen name/symbol. NEAR then deploys a wrapped token for the attacker's mint under the impersonated identity.

---

### Finding Description

**Root cause — missing mint-binding check in `parse_metadata_account`**

For classic SPL tokens, the code derives the Metaplex PDA deterministically from the mint key: [1](#0-0) 

This cryptographically binds the metadata to the mint — no other mint can claim that PDA.

For Token-2022 mints with a third-party `MetadataPointer`, the code instead trusts whatever address the mint authority wrote into the extension: [2](#0-1) 

Inside `parse_metadata_account`, the only checks are:

1. The passed `metadata` account key equals the address stored in `MetadataPointer` — attacker controls both.
2. The account is owned by `MetaplexID`. [3](#0-2) 

There is **no check** that `metadata.mint == self.mint.key()`. The `MplMetadata` struct (imported as `anchor_spl::metadata::MetadataAccount`) carries a `mint` field recording which mint the metadata belongs to, but it is never consulted.

**Concrete attack steps**

1. Attacker creates a Token-2022 mint (Mint A) with the `MetadataPointer` extension set to the canonical Metaplex PDA of a legitimate token (e.g., USDC's PDA = `find_program_address([b"metadata", MetaplexID, usdc_mint], MetaplexID)`).
2. Attacker calls `log_metadata` passing Mint A as `mint` and USDC's Metaplex metadata account as `metadata`.
3. `parse_metadata_account` is called with `address = usdc_metaplex_pda`. Both checks pass: the account key matches, and the account is owned by `MetaplexID`.
4. `MplMetadata::try_deserialize` reads USDC's `name = "USD Coin"` and `symbol = "USDC"`.
5. The Wormhole message is posted with `token = Mint_A_pubkey`, `name = "USD Coin"`, `symbol = "USDC"`. [4](#0-3) 

**NEAR side processing**

NEAR's `deploy_token_callback` receives the `LogMetadata` VAA, verifies the emitter is the registered Solana factory, and calls `deploy_token_internal` with `token_address = Mint_A` and `BasicMetadata { name: "USD Coin", symbol: "USDC", decimals: ... }`: [5](#0-4) 

NEAR now has a wrapped token for Mint A registered under the "USD Coin"/"USDC" identity. The backing guarantee is broken: the wrapped token claims to represent USDC but is actually redeemable only for Mint A tokens, which the attacker controls and can mint arbitrarily.

---

### Impact Explanation

- **Asset-identity divergence**: The bridge registers an attacker-controlled mint under a legitimate token's name/symbol. Any user who receives the wrapped "USDC" on NEAR and bridges it back to Solana receives worthless Mint A tokens, not real USDC.
- **Unbacked supply**: Because the attacker controls Mint A's mint authority (the constraint only rejects mints whose authority is the bridge's own authority PDA), they can mint unlimited Mint A tokens, bridge them to NEAR as "USDC", and sell the wrapped tokens to users who trust the displayed name/symbol.
- **Blocking / confusion**: If the attacker registers the fake identity before the legitimate USDC is registered, downstream UIs and integrations that key on name/symbol will be confused.

This falls squarely under **High — asset-identity, token-mapping divergence that breaks backing guarantees**.

---

### Likelihood Explanation

- No privileged access required. Anyone can create a Token-2022 mint and set its `MetadataPointer` to any address.
- All legitimate Metaplex metadata PDAs are public and readable on-chain.
- The call to `log_metadata` is fully permissionless (no signer constraint beyond paying fees).
- The attack is a single transaction on Solana followed by a standard Wormhole relay.

---

### Recommendation

After deserializing `MplMetadata`, assert that the metadata's `mint` field matches the mint being registered:

```rust
if metadata.owner == &MetaplexID {
    let data = metadata.try_borrow_data()?;
    let mpl = MplMetadata::try_deserialize(&mut data.as_ref())?;
    // ADD THIS CHECK:
    require_keys_eq!(
        mpl.mint,
        self.mint.key(),
        ErrorCode::InvalidTokenMetadataAddress,
    );
    Ok((mpl.name.clone(), mpl.symbol.clone()))
}
```

This mirrors the implicit binding that `find_program_address` already enforces for classic SPL tokens, and closes the gap for the Token-2022 third-party metadata path.

---

### Proof of Concept

Using the existing Mollusk test harness in `solana/programs/bridge_token_factory/tests/mollusk/`:

1. Create a Token-2022 mint (Mint A) with a `MetadataPointer` extension pointing to the Metaplex PDA of a different mint (Mint B).
2. Construct a `create_metaplex_metadata_account` for Mint B with `name = "USD Coin"`, `symbol = "USDC"` (owned by `metaplex_id()`).
3. Call `log_metadata` with Mint A as `mint` and Mint B's metadata account as `metadata`.
4. Assert the emitted Wormhole payload contains `token = Mint_A` but `name = "USD Coin"` and `symbol = "USDC"` — demonstrating the identity mismatch.

The helper `create_metaplex_metadata_account` already exists in `tests/mollusk/helpers.rs` (lines 361–411) and accepts an arbitrary `mint` field, making this directly reproducible without any external dependencies. [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L1159-1178)
```rust
        let Ok(ProverResult::LogMetadata(metadata)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        let chain = metadata.emitter_address.get_chain();
        require!(
            self.factories.get(&chain) == Some(metadata.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        self.deploy_token_internal(
            chain,
            &metadata.token_address,
            BasicMetadata {
                name: metadata.name,
                symbol: metadata.symbol,
                decimals: metadata.decimals,
            },
            attached_deposit,
        )
```

**File:** solana/programs/bridge_token_factory/tests/mollusk/helpers.rs (L361-411)
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
    // Data.name (borsh String: u32 len + bytes)
    data.extend_from_slice(&(name.len() as u32).to_le_bytes());
    data.extend_from_slice(name.as_bytes());
    // Data.symbol (borsh String)
    data.extend_from_slice(&(symbol.len() as u32).to_le_bytes());
    data.extend_from_slice(symbol.as_bytes());
    // Data.uri (borsh String, empty)
    data.extend_from_slice(&0u32.to_le_bytes());
    // Data.seller_fee_basis_points
    data.extend_from_slice(&0u16.to_le_bytes());
    // Data.creators: None
    data.push(0);
    // primary_sale_happened: false
    data.push(0);
    // is_mutable: true
    data.push(1);
    // edition_nonce: None
    data.push(0);
    // token_standard: None
    data.push(0);
    // collection: None
    data.push(0);
    // uses: None
    data.push(0);
    // collection_details: None
    data.push(0);
    // programmable_config: None
    data.push(0);

    let rent = Rent::default();
    Account {
        lamports: rent.minimum_balance(data.len()),
        data,
        owner: metaplex,
        executable: false,
        rent_epoch: 0,
    }
```
