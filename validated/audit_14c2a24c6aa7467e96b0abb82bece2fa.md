Let me examine the relevant code in detail.

Now let me check the NEAR side to understand how `LogMetadata` messages are processed and what the actual impact is.

Now I have all the information needed to make a definitive assessment. Let me trace the exact exploit path through the code.

**Exploit trace:**

**Step 1 — Attacker's mint passes the `log_metadata` account constraint:**

The only constraint on `mint` is: [1](#0-0) 

The attacker's Token-2022 mint satisfies `!mint.mint_authority.contains(authority.key)` because the attacker (not the bridge authority) holds mint authority.

**Step 2 — The "third-party metadata" branch is taken:** [2](#0-1) 

When `metadata_pointer.metadata_address.0` ≠ attacker's mint key AND ≠ `Pubkey::default()`, `parse_metadata_account` is called with the pointer value — which the attacker set to USDC's Metaplex PDA.

**Step 3 — `parse_metadata_account` has no binding check between the metadata account and the mint:** [3](#0-2) 

The only check is `require_keys_eq!(metadata.key(), address, ...)` — i.e., the passed account's key must equal the pointer stored in the attacker's mint. Since the attacker controls the pointer, they pass USDC's Metaplex PDA as both the pointer value and the `metadata` account. The check passes. There is **no verification that `MplMetadata.mint == self.mint.key()`**. The Metaplex metadata account contains a `mint` field identifying which mint it belongs to, but this field is never read.

**Step 4 — Spoofed payload is posted:** [4](#0-3) 

`LogMetadataPayload { token: attacker_mint, name: "USD Coin", symbol: "USDC", decimals: X }` is posted via Wormhole. Guardians sign it as a valid VAA.

**Step 5 — NEAR receives and registers the spoofed identity:** [5](#0-4) 

The VAA is parsed into a `LogMetadataMessage` with `token_address = attacker_mint` and `name = "USD Coin"`, `symbol = "USDC"`. NEAR's registry now maps the attacker's mint pubkey to USDC's identity.

---

### Title
Token-2022 `metadata_pointer` indirection allows spoofing any token's name/symbol in NEAR's registry — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary
`LogMetadata::process` follows a Token-2022 mint's `metadata_pointer` extension to a third-party Metaplex account and reads name/symbol from it, but never verifies that the Metaplex account's `mint` field matches the Token-2022 mint being logged. An attacker who controls a Token-2022 mint can set its `metadata_pointer` to any existing Metaplex metadata PDA (e.g., USDC's), pass that account as the optional `metadata` UncheckedAccount, and cause the bridge to emit a Wormhole message associating the attacker's mint pubkey with the legitimate token's name and symbol.

### Finding Description
In `parse_metadata_account`, the only integrity check is:

```rust
require_keys_eq!(
    metadata.key(),
    address,          // = metadata_pointer.metadata_address.0
    ErrorCode::InvalidTokenMetadataAddress,
);
```

`address` is read directly from the attacker-controlled `metadata_pointer` extension of the attacker's mint. The attacker sets this pointer to USDC's Metaplex PDA, then passes USDC's Metaplex account as `metadata`. The key equality check trivially passes. The code then deserializes the Metaplex account and returns its `name`/`symbol` fields without ever checking `MplMetadata.mint == self.mint.key()`.

The resulting `LogMetadataPayload` carries `token = attacker_mint` alongside `name = "USD Coin"` and `symbol = "USDC"`, which Wormhole guardians sign as a valid VAA. NEAR's registry then maps the attacker's mint to USDC's identity.

### Impact Explanation
This is a **High** severity asset-identity and token-mapping divergence. The attacker can:
1. Register their worthless Token-2022 mint as "USD Coin (USDC)" in NEAR's cross-chain token registry.
2. Trigger `deploy_token` on EVM/other chains to deploy a wrapped token bearing USDC's name and symbol but backed by the attacker's mint.
3. Mint arbitrary amounts of their Token-2022 token, bridge them as "USDC", and sell them to users who believe they hold a legitimate asset.

This breaks the backing guarantee: wrapped "USDC" on NEAR/EVM would be backed by an unbacked attacker-controlled mint rather than real USDC. Real USDC transfers are unaffected (they are keyed by the canonical mint pubkey), so this does not directly drain the bridge vault, but it enables large-scale fraud against users who interact with the spoofed token.

### Likelihood Explanation
The attack requires no privileged access. Any account can create a Token-2022 mint, set its `metadata_pointer` to any arbitrary pubkey, and call `log_metadata`. The Metaplex metadata accounts for major tokens (USDC, USDT, wBTC, etc.) are all publicly readable on-chain. The exploit is fully permissionless and locally reproducible.

### Recommendation
In `parse_metadata_account`, after deserializing the Metaplex account, add a binding check:

```rust
let metadata_account = MplMetadata::try_deserialize(&mut data.as_ref())?;
require_keys_eq!(
    metadata_account.mint,
    self.mint.key(),
    ErrorCode::InvalidTokenMetadataAddress,
);
Ok((metadata_account.name.clone(), metadata_account.symbol.clone()))
```

This ensures the Metaplex metadata account actually belongs to the mint being logged, regardless of what the `metadata_pointer` extension says.

### Proof of Concept
1. Create a Token-2022 mint `attacker_mint` with `metadata_pointer` = `find_program_address(["metadata", MetaplexID, USDC_MINT], MetaplexID)` (USDC's Metaplex PDA).
2. Call `log_metadata` with `mint = attacker_mint`, `metadata = Some(usdc_metaplex_pda_account)`, `token_program = Token-2022`.
3. In `process()`: pointer ≠ attacker_mint key → "Third-party metadata" branch → `parse_metadata_account(usdc_metaplex_pda)`.
4. `metadata.key() == usdc_metaplex_pda` ✓; `metadata.owner == MetaplexID` ✓; returns `("USD Coin", "USDC")`.
5. Wormhole message emitted: `{ token: attacker_mint, name: "USD Coin", symbol: "USDC", decimals: X }`.
6. Assert: emitted payload name/symbol match USDC's metadata, not the attacker mint's own identity.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L72-90)
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

**File:** near/omni-prover/wormhole-omni-prover-proxy/src/parsed_vaa.rs (L230-248)
```rust
impl TryInto<LogMetadataMessage> for ParsedVAA {
    type Error = String;

    fn try_into(self) -> Result<LogMetadataMessage, String> {
        let parsed_payload: LogMetadataWh = borsh::from_slice(&self.payload).map_err(stringify)?;

        if parsed_payload.payload_type != ProofKind::LogMetadata {
            return Err("Invalid proof kind".to_owned());
        }

        let chain_kind = parsed_payload.token_address.get_chain();
        Ok(LogMetadataMessage {
            token_address: parsed_payload.token_address,
            name: parsed_payload.name,
            symbol: parsed_payload.symbol,
            decimals: parsed_payload.decimals,
            emitter_address: OmniAddress::new_from_slice(chain_kind, &self.emitter_address)?,
        })
    }
```
