### Title
`claim_fee` Restricted to Trusted Relayers Causes Permanent Fee Lock — (`near/omni-bridge/src/lib.rs`)

### Summary
The `claim_fee` function on the NEAR bridge is gated by the `#[trusted_relayer]` macro, which restricts callers to accounts that are currently registered as trusted relayers. A second check inside the callback further requires the caller to be the exact `fee_recipient` embedded in the proof. Together, these two restrictions mean that if a relayer earns fees but subsequently loses trusted-relayer status (e.g., stake slashed, registration expired, or key lost), their accrued fees are permanently unclaimable — there is no admin rescue path and no other account can trigger delivery on their behalf.

### Finding Description

`claim_fee` is the sole mechanism for a relayer to collect bridge fees:

```rust
#[payable]
#[trusted_relayer]
#[pause(except(roles(Role::DAO)))]
pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
``` [1](#0-0) 

The `#[trusted_relayer]` attribute rejects any caller that is not currently in the trusted-relayer set before the function body executes. Inside the callback, a second hard gate is applied:

```rust
require!(
    fee_recipient == *predecessor_account_id,
    BridgeError::OnlyFeeRecipientCanClaim.as_ref()
);
``` [2](#0-1) 

The `fee_recipient` is extracted from the prover result and must exactly match the caller:

```rust
let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
    env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
});
``` [3](#0-2) 

The combined effect is that fees can only be claimed by an account that is **simultaneously** (a) a currently-registered trusted relayer and (b) the exact `fee_recipient` address embedded in the proof. No DAO override, no admin rescue, and no third-party trigger exists.

### Impact Explanation

If a relayer earns fees on a transfer (fee > 0 stored in `transfer_message`) but later loses trusted-relayer status — through stake slashing, voluntary de-registration, or key loss — the fees stored in the bridge contract become permanently unclaimable. Because `fee_recipient == predecessor_account_id` also blocks any other trusted relayer from triggering delivery on the original recipient's behalf, there is no recovery path. This is an irreversible fund lock of protocol fee value, matching the Critical impact tier: *"Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows."*

### Likelihood Explanation

Trusted-relayer status is tied to staking. Slashing, voluntary withdrawal, or key compromise are all realistic operational events. Any relayer who processes a transfer and is later removed from the trusted set before calling `claim_fee` triggers the lock. The window between earning fees and claiming them can be arbitrarily long, making this a plausible real-world scenario.

### Recommendation

Remove the `#[trusted_relayer]` guard from `claim_fee`. Because the fee destination is fixed by the `fee_recipient` field inside the cryptographically-verified proof, anyone can safely trigger the function — fees will always be routed to the correct recipient regardless of who initiates the call. The `fee_recipient == predecessor_account_id` check in the callback should also be relaxed to allow any caller to trigger delivery to the proof-embedded recipient, mirroring the fix applied in the analogous OrderBook.sol PR 315.

### Proof of Concept

1. Relayer R processes a cross-chain transfer and is recorded as `fee_recipient` in the signed proof with `fee > 0`.
2. Before R calls `claim_fee`, R's stake is slashed and R is removed from the trusted-relayer set.
3. R calls `claim_fee` → rejected by `#[trusted_relayer]` before any logic executes.
4. A different trusted relayer T calls `claim_fee` with the same proof → passes `#[trusted_relayer]`, but the callback's `require!(fee_recipient == *predecessor_account_id)` fails because `T ≠ R`.
5. No other entry point exists to release the fees. The fee balance remains locked in the bridge contract indefinitely. [1](#0-0) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1058-1068)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1083-1090)
```rust
        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
```
