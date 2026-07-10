### Title
Detached NEAR Transfer Promises Silently Discard Excess-Deposit Refunds, Permanently Freezing User Funds — (File: `crates/contract/src/lib.rs`)

### Summary
Three payable entry-points in the MPC contract fire excess-deposit refund Promises with `.detach()`, meaning the transfer's success or failure is never observed. If the refund Promise fails (e.g., the caller is a smart-contract account that does not accept incoming transfers), the excess NEAR is permanently locked inside the MPC contract with no withdrawal path.

### Finding Description
The analog to the ERC-20 `transferFrom()` unchecked-return-value pattern in NEAR is calling `.detach()` on a `Promise::transfer()`. A detached Promise is fire-and-forget: the runtime never propagates its outcome back to the calling function, so a failed transfer is silently swallowed.

Three locations exhibit this pattern:

**1. `require_deposit()` — line 137**
Called from every user-facing request method (`sign`, `request_app_private_key`, `verify_foreign_transaction`). [1](#0-0) 

**2. `submit_participant_info()` — line 847**
Called by prospective MPC nodes to register their TEE attestation. [2](#0-1) 

**3. `propose_update()` — line 1330**
Called by participants to propose a contract-code or config upgrade; the required deposit covers storage for the full contract binary plus 128 participant-vote slots. [3](#0-2) 

In all three cases the pattern is:
```rust
Promise::new(recipient).transfer(diff).detach();
```
`diff` is the excess above the minimum required deposit. If the Promise fails, `diff` yoctoNEAR remains in the MPC contract balance with no on-chain mechanism to reclaim it.

### Impact Explanation
Excess NEAR attached by a caller whose account cannot receive transfers (e.g., a smart-contract account without a `receive` handler, or an account that has been deleted between the call and the callback) is permanently frozen inside the MPC contract. For `propose_update`, the required deposit is proportional to the size of the uploaded contract binary (potentially hundreds of kilobytes), so the excess could be non-trivial. This breaks the production accounting invariant that every caller is made whole for any over-payment, and constitutes permanent freezing of user funds held by the MPC contract.

**Impact level: Medium** — balance/accounting invariant broken without requiring network-level DoS or operator misconfiguration.

### Likelihood Explanation
Any caller that is itself a smart-contract account (e.g., a DAO or multisig that calls `propose_update` or `sign` on behalf of its members) and does not implement a `receive`/fallback handler will silently lose its excess deposit. This is a realistic deployment pattern for governance participants. The likelihood is low-to-medium: it requires the caller to be a contract account without a transfer handler, but such accounts are common in production NEAR governance setups.

### Recommendation
Replace the fire-and-forget pattern with a chained callback that verifies the transfer succeeded, or — more idiomatically in NEAR — use `then()` to attach a failure handler that logs or re-tries the refund. At minimum, avoid `.detach()` on any Promise that moves user funds:

```rust
// Instead of:
Promise::new(recipient).transfer(diff).detach();

// Use a chained callback or return the promise so the runtime tracks it:
Promise::new(recipient).transfer(diff)
    // chain a no-op callback so failure is observable in receipts
    .then(Promise::new(env::current_account_id()).function_call(
        "on_refund_complete".to_string(), vec![], NearToken::from_yoctonear(0), Gas::from_tgas(5)
    ));
```

Alternatively, require callers to attach exactly the required deposit (no excess allowed), eliminating the refund path entirely.

### Proof of Concept

1. Deploy a NEAR smart-contract account `attacker.near` that does **not** implement a `receive` handler (i.e., any incoming transfer will fail).
2. From `attacker.near`, call `propose_update` on the MPC contract, attaching `required_deposit + 1_000_000_000_000_000_000_000_000` yoctoNEAR (1 NEAR excess).
3. The `propose_update` function computes `diff = attached - required` and fires:
   ```rust
   Promise::new(proposer).transfer(diff).detach();
   ``` [4](#0-3) 
4. The transfer Promise to `attacker.near` fails because the account has no receive handler.
5. Because `.detach()` was used, the failure receipt is never inspected by the contract.
6. The 1 NEAR excess remains in the MPC contract balance permanently — `attacker.near` has no way to recover it, and the MPC contract has no withdrawal function.
7. The same scenario applies to `require_deposit` (line 137) and `submit_participant_info` (line 847), affecting any user or node operator calling from a contract account. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L122-141)
```rust
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
}
```

**File:** crates/contract/src/lib.rs (L843-849)
```rust
            // Refund the difference if the proposer attached more than required
            if let Some(diff) = attached.checked_sub(cost)
                && diff > NearToken::from_yoctonear(0)
            {
                Promise::new(account_id).transfer(diff).detach();
            }
        }
```

**File:** crates/contract/src/lib.rs (L1298-1334)
```rust
    #[payable]
    #[handle_result]
    pub fn propose_update(
        &mut self,
        #[serializer(borsh)] args: ProposeUpdateArgs,
    ) -> Result<UpdateId, Error> {
        // Only voters can propose updates:
        let proposer = self.voter_or_panic();
        let update: Update = args.try_into()?;

        let attached = env::attached_deposit();
        let required = ProposedUpdates::required_deposit(&update);
        if attached < required {
            return Err(InvalidParameters::InsufficientDeposit {
                attached: attached.as_yoctonear(),
                required: required.as_yoctonear(),
            }
            .into());
        }

        let id = self.proposed_updates.propose(update);

        log!(
            "propose_update: signer={}, id={:?}",
            env::signer_account_id(),
            id,
        );

        // Refund the difference if the proposer attached more than required.
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }

        Ok(id)
    }
```
