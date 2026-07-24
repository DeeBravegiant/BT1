### Title
Missing Recipient Binding in `finalize_transfer` Allows Token Redirection to Attacker — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

### Summary

The Solana `finalize_transfer` instruction accepts a caller-supplied `recipient` account that is never verified against the `recipient` field encoded in the NEAR-MPC-signed `FinalizeTransferPayload`. Because the instruction is permissionless and signed payloads are public (emitted as events by the NEAR bridge), an attacker can replay any valid signed payload while substituting their own address as `recipient`, redirecting the unlocked or minted tokens to themselves.

### Finding Description

The `FinalizeTransfer` Anchor accounts struct declares `recipient` as a bare `UncheckedAccount` with no constraint tying it to the payload:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
```

The `token_account` — the destination for all token movements — is derived solely from this caller-supplied `recipient`:

```rust
#[account(
    init_if_needed,
    payer = common.payer,
    associated_token::mint = mint,
    associated_token::authority = recipient,   // ← caller-controlled
    token::token_program = token_program,
)]
pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

Inside `FinalizeTransfer::process`, the nonce is consumed and tokens are transferred/minted to `self.token_account` (i.e., the ATA of the caller-supplied `recipient`) without any assertion that `self.recipient.key() == data.recipient`:

```rust
pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
    UsedNonces::use_nonce(data.destination_nonce, ...)?;   // nonce burned here

    if let Some(vault) = &self.vault {
        transfer_checked(... to: self.token_account.to_account_info() ...)?;
    } else {
        ...
        mint_to(... to: self.token_account.to_account_info() ...)?;
    }
    ...
}
```

The NEAR MPC signature covers the full payload including the intended `recipient`. The signature is verified before `process` is called, but the binding between the verified payload's `recipient` and the account that actually receives tokens is never enforced.

### Impact Explanation

**Critical.** An attacker who observes a valid signed payload (emitted as an on-chain event by the NEAR bridge's `sign_transfer_callback`) can call `finalize_transfer` before the legitimate relayer, passing their own address as `recipient`. The nonce is consumed, the tokens are minted or unlocked to the attacker's ATA, and the legitimate recipient receives nothing. The transfer is permanently unclaimable by the intended recipient because the nonce is already marked used.

This satisfies: *Unauthorized release/withdrawal of locked or wrapped bridge assets through settlement failure* and *Irreversible fund lock / permanently unclaimable user value*.

### Likelihood Explanation

**High.** The instruction is fully permissionless — no role or signer check gates it. Signed payloads are public (broadcast as NEAR events). The attacker only needs to monitor the NEAR chain for `SignTransferCallback` events and front-run the relayer on Solana, which is straightforward given Solana's public mempool and the latency window between NEAR signing and Solana finalization.

### Recommendation

Add an explicit constraint in the `FinalizeTransfer` accounts struct binding the passed `recipient` to the value in the signed payload:

```rust
#[account(
    constraint = recipient.key().to_bytes() == data.payload.recipient @ ErrorCode::InvalidRecipient
)]
pub recipient: UncheckedAccount<'info>,
```

Alternatively, derive the `token_account` directly from `data.payload.recipient` rather than from the caller-supplied `recipient` account.

### Proof of Concept

1. Alice initiates a NEAR→Solana transfer. The NEAR bridge signs a `FinalizeTransferPayload` with `recipient = Alice_pubkey` and emits the signature.
2. Bob observes the signed payload on-chain.
3. Bob calls `finalize_transfer` with the identical `SignedPayload` but passes `Bob_pubkey` as the `recipient` account.
4. Anchor derives `token_account` as Bob's ATA (via `associated_token::authority = recipient`).
5. `process` verifies the MPC signature (valid, since the payload is unchanged), marks the nonce used, and transfers/mints tokens to Bob's ATA.
6. Alice's nonce is consumed; she can never claim her tokens. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L50-51)
```rust
    /// CHECK: this can be any type of account
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L73-80)
```rust
    #[account(
        init_if_needed,
        payer = common.payer,
        associated_token::mint = mint,
        associated_token::authority = recipient,
        token::token_program = token_program,
    )]
    pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L89-116)
```rust
impl FinalizeTransfer<'_> {
    pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;

        if let Some(vault) = &self.vault {
            // Native version. We have a proof of token registration by vault existence
            transfer_checked(
                CpiContext::new_with_signer(
                    self.token_program.to_account_info(),
                    TransferChecked {
                        from: vault.to_account_info(),
                        to: self.token_account.to_account_info(),
                        authority: self.authority.to_account_info(),
                        mint: self.mint.to_account_info(),
                    },
                    &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]],
                ),
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
                self.mint.decimals,
            )?;
```
