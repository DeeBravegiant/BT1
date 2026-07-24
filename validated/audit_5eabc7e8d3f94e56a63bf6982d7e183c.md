The exploit path is concrete. Let me verify the `MplMetadata` struct fields to confirm the missing `mint` check.

The exploit is confirmed. The `MplMetadata` struct layout (confirmed by the test helper at line 374: `data.extend_from_slice(mint.as_ref())`) contains a `mint` field that is never validated against `self.mint.key()` in `parse_metadata_account`. The classic SPL path is safe because the PDA is derived from the mint's own pubkey, but the Token-2022 MetadataPointer path has no such binding.

---

### Title
Token-2022 MetadataPointer Allows Metadata Spoofing to Register Attacker's Mint with Victim Token's Identity — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary

`LogMetadata::parse_metadata_account` reads `name` and `symbol` from a Metaplex metadata account without verifying that the metadata's embedded `mint` field matches the mint being registered. An attacker can create a Token-2022 mint with a `MetadataPointer` extension pointing to the Metaplex PDA of a legitimate high-value token (e.g., wBTC), then call `log_metadata` to emit a Wormhole VAA that binds the attacker's mint pubkey to wBTC's name, symbol, and decimals. NEAR then deploys a token with wBTC's identity mapped to the attacker's worthless mint, enabling the attacker to bridge unlimited self-minted tokens and receive wBTC-labeled tokens on NEAR.

### Finding Description

The `process()` function handles Token-2022 mints by reading the `MetadataPointer` extension and, when the pointer is non-zero and non-self, delegating to `parse_metadata_account` with the pointer's target address: [1](#0-0) 

`parse_metadata_account` performs two checks: (1) the passed account key matches the pointer address, and (2) the account is owned by `MetaplexID`. It then deserializes and returns `name` and `symbol` with **no check that `metadata.mint == self.mint.key()`**: [2](#0-1) 

The Metaplex `MetadataAccount` layout contains a `mint` field at bytes 33–64 (confirmed by the test helper's serialization): [3](#0-2) 

This field is never read or validated in `parse_metadata_account`. The resulting `LogMetadataPayload` uses the attacker's mint pubkey as `token` but wBTC's `name`/`symbol`/`decimals`: [4](#0-3) 

By contrast, the classic SPL token path is safe because the Metaplex PDA is derived deterministically from the mint's own pubkey, making it impossible to substitute another token's metadata: [5](#0-4) 

The only constraint on the `mint` account is that the bridge authority is not the mint authority — the attacker's mint trivially satisfies this: [6](#0-5) 

The `metadata` account is declared `UncheckedAccount` with no Anchor constraints beyond what `parse_metadata_account` manually enforces: [7](#0-6) 

### Impact Explanation

After the spoofed VAA is accepted by NEAR, a token with wBTC's name/symbol is deployed and mapped to the attacker's mint. The attacker controls the mint authority of their Token-2022 mint and can issue unlimited tokens. Each bridge transfer locks attacker-minted tokens in the vault and mints wBTC-labeled tokens on NEAR. These tokens are entirely unbacked by real wBTC. Users who receive or purchase these wBTC-labeled tokens on NEAR suffer direct financial loss. If the real wBTC has not yet been registered, the attacker can also preempt its registration, permanently poisoning the token mapping for wBTC on NEAR.

### Likelihood Explanation

The attack requires no privileges, no leaked keys, and no colluding parties. The attacker only needs to:
1. Create a Token-2022 mint (permissionless, costs ~0.01 SOL)
2. Set a `MetadataPointer` extension to wBTC's existing Metaplex PDA (a public, on-chain account)
3. Call `log_metadata` (permissionless public instruction)

All three steps are executable by any Solana wallet with minimal SOL for rent. The wBTC Metaplex PDA is a stable, publicly known address.

### Recommendation

In `parse_metadata_account`, after deserializing the `MplMetadata` account, add a check that the metadata's `mint` field matches the mint being registered:

```rust
let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
require_keys_eq!(
    metadata.mint,
    self.mint.key(),
    ErrorCode::InvalidTokenMetadataAddress,
);
Ok((metadata.name.clone(), metadata.symbol.clone()))
```

This mirrors the implicit binding that the classic SPL path achieves through PDA derivation.

### Proof of Concept

```rust
// 1. Derive wBTC's Metaplex PDA (public, on-chain)
let wbtc_mint = Pubkey::from_str("3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh").unwrap(); // wBTC mainnet
let (wbtc_metaplex_pda, _) = Pubkey::find_program_address(
    &[b"metadata", MetaplexID.as_ref(), wbtc_mint.as_ref()],
    &MetaplexID,
);

// 2. Create attacker's Token-2022 mint with MetadataPointer = wBTC's Metaplex PDA
//    spl_token_2022::instruction::initialize_mint_close_authority + initialize_metadata_pointer
//    metadata_pointer.metadata_address = wbtc_metaplex_pda
//    decimals = 8 (matching wBTC)
//    mint_authority = attacker (NOT bridge authority → passes constraint)

// 3. Call log_metadata:
//    accounts: [authority_pda, attacker_mint, wbtc_metaplex_pda, vault_pda, ...]
//    parse_metadata_account(wbtc_metaplex_pda):
//      - key check passes (attacker passed wbtc_metaplex_pda)
//      - owner check passes (MetaplexID owns it)
//      - deserializes → name="Wrapped Bitcoin", symbol="wBTC"
//      - NO mint field check
//    LogMetadataPayload { token: attacker_mint, name: "Wrapped Bitcoin", symbol: "wBTC", decimals: 8 }

// 4. Wormhole VAA emitted → NEAR deploys "Wrapped Bitcoin (wBTC)" mapped to attacker_mint

// 5. Attacker mints 1,000,000 tokens of attacker_mint (controls mint authority)
//    Bridges to NEAR → receives 1,000,000 wBTC-labeled tokens
//    Sells on NEAR DEX at wBTC price
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L47-48)
```rust
    /// CHECK: may be unitialized
    pub metadata: Option<UncheckedAccount<'info>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L83-86)
```rust
        if metadata.owner == &MetaplexID {
            let data = metadata.try_borrow_data()?;
            let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
            Ok((metadata.name.clone(), metadata.symbol.clone()))
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

**File:** solana/programs/bridge_token_factory/tests/mollusk/helpers.rs (L372-374)
```rust
    data.extend_from_slice(update_authority.as_ref());
    // mint
    data.extend_from_slice(mint.as_ref());
```
