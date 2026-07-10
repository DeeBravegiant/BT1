### Title
Unchecked Promise Result on Excess-Deposit Refund Transfers Silently Traps User Funds — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `require_deposit()` helper and `propose_update()` both schedule NEAR token refunds using `Promise::new(...).transfer(diff).detach()`. The `.detach()` call is the NEAR-SDK analog of Solidity's unchecked `token.transfer()`: the promise is fired and its result is permanently discarded. If the transfer receipt fails, the excess deposit is silently retained by the contract with no error, no event, and no recovery path for the user.

---

### Finding Description

In `require_deposit()`, every user-facing entry point (`sign`, `request_app_private_key`, `verify_foreign_transaction`) calls:

```rust
Promise::new(predecessor.clone()).transfer(diff).detach();
``` [1](#0-0) 

In `propose_update()`, the same pattern is used for governance participants:

```rust
Promise::new(proposer).transfer(diff).detach();
``` [2](#0-1) 

`.detach()` in NEAR SDK explicitly means "do not propagate the promise result back to this contract." If the transfer receipt fails — because the recipient account was deleted in the one-block window between the call receipt and the refund receipt — the runtime returns the tokens to the **contract's own balance**, not to the user. The contract has no callback, no error log, and no mechanism to detect or recover from the failure. The excess deposit is permanently absorbed into the contract's balance.

This is structurally identical to M-06: a safer pattern (chaining a `#[callback_result]` handler or using a pull-based refund) exists and is used elsewhere in the codebase (e.g., `resolve_verification` chains `refund_deposit` inside a verified callback), but the push-transfer path uses the unchecked variant. [3](#0-2) 

---

### Impact Explanation

**Medium — Balance and request-lifecycle accounting invariant broken.**

The contract's documented invariant is that any deposit above the minimum is refunded to the caller. When `.detach()` silently swallows a failed transfer, the excess deposit is permanently locked in the contract balance with no on-chain evidence. Because `require_deposit` is invoked on every `sign`, `request_app_private_key`, and `verify_foreign_transaction` call, this affects the entire user-facing request surface. Accumulated unrefunded deposits distort the contract's balance accounting and are unrecoverable without a privileged migration.

---

### Likelihood Explanation

**Low-to-medium.** In NEAR, a transfer receipt to an account that no longer exists fails at the runtime level. The predecessor account must exist at call time, but NEAR allows `delete_account` in a separate transaction that can land in the same or the immediately following block. A contract-based caller (e.g., a DeFi integration or a batch-call proxy) that self-destructs after dispatching a sign request — a pattern used in MEV bots and atomic-execution contracts on other chains — would trigger this silently. The one-block window is narrow but is a real, attacker-reachable condition, not a theoretical one.

---

### Recommendation

Replace the fire-and-forget push refund with either:

1. **Callback-verified push**: chain a `#[callback_result]` handler after the transfer promise so a failed refund is detected and re-queued or logged.
2. **Pull-based refund**: record the owed refund in contract storage and expose a `claim_refund()` method, eliminating the race entirely.

The second pattern is already used in the TEE attestation path (`refund_deposit` called inside `resolve_verification` before `promise_yield_resume`), so the safer idiom is established in this codebase.

---

### Proof of Concept

1. A contract-based caller `C` calls `sign(domain_id, payload, path)` attaching `2 yoctoNEAR` (minimum is `1 yoctoNEAR`).
2. `require_deposit` computes `diff = 1 yoctoNEAR` and schedules `Promise::new(C).transfer(1).detach()`.
3. In the same block (or the next), `C` executes `delete_account(beneficiary)`.
4. The transfer receipt targeting `C` fails at the NEAR runtime because `C` no longer exists.
5. The `1 yoctoNEAR` is credited back to the MPC contract's balance.
6. No callback fires; no log is emitted; the user's excess deposit is permanently lost.
7. Repeated across many callers, the contract silently accumulates unaccounted NEAR, breaking the balance invariant with no on-chain audit trail. [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L110-140)
```rust
/// Checks that the caller attached at least `minimum_deposit` and refunds any excess.
///
/// A non-zero deposit is required so that the transaction must be signed by a
/// full-access key (or a function-call access key whose `deposit` allowance is
/// explicitly set). This prevents a **malicious frontend** from silently
/// submitting signature requests on behalf of a user via a restricted
/// function-call access key, because such keys cannot attach deposits by
/// default. In other words, requiring a deposit ensures the user (or their
/// full-access key) explicitly authorised the call.
///
/// See the "Deposit requirement" section in the contract README for more
/// details.
fn require_deposit(minimum_deposit: NearToken, predecessor: &AccountId) {
    let deposit = env::attached_deposit();
    match deposit.checked_sub(minimum_deposit) {
        None => {
            env::panic_str(
                &InvalidParameters::InsufficientDeposit {
                    attached: deposit.as_yoctonear(),
                    required: minimum_deposit.as_yoctonear(),
                }
                .to_string(),
            );
        }
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
        }
    }
```

**File:** crates/contract/src/lib.rs (L1327-1331)
```rust
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```
