### Title
Excess Deposit Permanently Locked in Address Registrar on Successful Registration - (File: runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs)

### Summary
The `register()` function in the address registrar contract uses a `<` check (`given_deposit < required_deposit`) that permits callers to attach more NEAR than the required storage cost. On a successful registration (no address collision), the excess deposit above `required_deposit` is silently retained by the contract with no refund path, permanently burning the caller's tokens.

### Finding Description
The `register()` function computes the exact storage cost for the mapping entry and validates only that the caller sent *at least* that amount:

```rust
let required_deposit =
    NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
let given_deposit = env::attached_deposit();
if given_deposit < required_deposit {
    env::panic_str(...);
}
```

On the success path (`Entry::Vacant`), the function stores the mapping and returns without issuing any refund receipt for the surplus. The collision path (`Entry::Occupied`) correctly refunds the full `given_deposit`, but the success path does not:

```rust
Entry::Vacant(entry) => {
    // ... stores mapping, no refund of excess
    Some(address)
}
Entry::Occupied(entry) => {
    let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
    env::promise_batch_action_transfer(refund_promise, given_deposit);
    None
}
```

Because `storage_byte_cost()` is a dynamic protocol parameter and the required amount depends on the variable-length `account_id`, callers who send a convenient round number (e.g., 1 mNEAR) instead of the exact computed amount will silently overpay. The excess is credited to the address registrar contract's balance with no mechanism for the caller to recover it.

### Impact Explanation
Any unprivileged user who calls `register()` with `given_deposit > required_deposit` permanently loses the difference. The excess NEAR is credited to the address registrar contract's account balance and is irrecoverable by the caller. The corrupted value is the caller's on-chain NEAR balance: it is reduced by `given_deposit` rather than the correct `required_deposit`, with the delta locked in the contract forever.

### Likelihood Explanation
Moderate. The `storage_byte_cost()` is a protocol-level dynamic value, and the required deposit depends on the byte-length of the account ID being registered. Callers using wallets or scripts that round up to a safe amount (a common pattern when the exact cost is not known) will routinely overpay. The function is publicly callable by any ETH-wallet user.

### Recommendation
On the success path, compute the surplus and issue a refund transfer to the caller:

```rust
Entry::Vacant(entry) => {
    let address = format!("0x{}", hex::encode(address));
    entry.insert(account_id);
    // Refund any excess deposit
    let surplus = given_deposit.as_yoctonear()
        .saturating_sub(required_deposit.as_yoctonear());
    if surplus > 0 {
        let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
        env::promise_batch_action_transfer(
            refund_promise,
            NearToken::from_yoctonear(surplus),
        );
    }
    Some(address)
}
```

Alternatively, require an exact deposit (`given_deposit == required_deposit`) and reject any overpayment.

### Proof of Concept
1. Compute `required_deposit` for a 10-byte account ID: `storage_byte_cost * (20 + 10)` yoctoNEAR.
2. Call `register("alice.near")` with `attached_deposit = required_deposit + 1_000_000_000_000_000_000_000` (1 mNEAR extra).
3. The `if given_deposit < required_deposit` check passes.
4. The `Entry::Vacant` branch executes, stores the mapping, and returns `Some(address)`.
5. No refund receipt is generated.
6. The caller's balance is reduced by the full `given_deposit`; the address registrar contract retains the 1 mNEAR surplus permanently. [1](#0-0) [2](#0-1)

### Citations

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L50-61)
```rust
        let required_deposit =
            NearToken::from_yoctonear(env::storage_byte_cost().as_yoctonear() * bytes_to_store);
        let given_deposit = env::attached_deposit();
        // The caller must pay for the storage cost of registering.
        if given_deposit < required_deposit {
            let message = format!(
                "Insufficient deposit to cover storage cost. Given={} Expected={}",
                given_deposit.as_yoctonear(),
                required_deposit.as_yoctonear(),
            );
            env::panic_str(&message);
        }
```

**File:** runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs (L63-85)
```rust
        let address = account_id_to_address(&account_id);

        match self.addresses.entry(address) {
            Entry::Vacant(entry) => {
                let address = format!("0x{}", hex::encode(address));
                let log_message = format!("Added entry {} -> {}", address, account_id);
                entry.insert(account_id);
                env::log_str(&log_message);
                Some(address)
            }
            Entry::Occupied(entry) => {
                let log_message = format!(
                    "Address collision between {} and {}. Keeping the former.",
                    entry.get(),
                    account_id
                );
                env::log_str(&log_message);
                // Transfer the deposit back to the caller since no storage was updated.
                let refund_promise = env::promise_batch_create(&env::predecessor_account_id());
                env::promise_batch_action_transfer(refund_promise, given_deposit);
                None
            }
        }
```
