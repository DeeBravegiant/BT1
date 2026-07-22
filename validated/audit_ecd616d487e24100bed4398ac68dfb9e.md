Let me search the sequencer codebase for patterns analogous to the bug - specifically tolerance/threshold checks where a divisor could be zero or near-zero, causing incorrect rejection or acceptance.

### Title
Zero-Tip Fee Escalation Bypass in `increased_enough` Allows Replacement Without Tip Increase - (File: `crates/apollo_mempool/src/mempool.rs`)

### Summary

The `increased_enough` function in the mempool's fee escalation logic computes the required minimum incoming value as `existing_value + ceil(existing_value * percentage / 100)`. When `existing_value == 0` (i.e., the existing transaction has `tip = 0`), the required increase collapses to `0 + 0 = 0`, so `incoming_value >= 0` is unconditionally true. Any replacement transaction — including one with `tip = 0` — passes the tip escalation check, violating the stated invariant that "any positive percentage always demands a strictly higher fee, even for small values."

### Finding Description

The `increased_enough` function at `crates/apollo_mempool/src/mempool.rs` lines 821–836:

```rust
fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
    let percentage = u128::from(self.config.static_config.fee_escalation_percentage);
    let Some(escalation_qualified_value) = existing_value
        .checked_mul(percentage)
        .map(|scaled| scaled.div_ceil(100))
        .and_then(|required_increase| existing_value.checked_add(required_increase))
    else {
        return false;
    };
    incoming_value >= escalation_qualified_value
}
``` [1](#0-0) 

When `existing_value = 0`:
- `0.checked_mul(percentage)` → `Some(0)`
- `0.div_ceil(100)` → `0` (required increase = 0)
- `0.checked_add(0)` → `Some(0)` (escalation_qualified_value = 0)
- `incoming_value >= 0` → **always `true`**

`should_replace_tx` calls `increased_enough` for both `tip` and `max_l2_gas_price`: [2](#0-1) 

`tip = 0` transactions are explicitly valid in the mempool (no minimum tip is enforced). The test `test_fee_escalation_valid_replacement_minimum_values` uses `tip: 0` as the existing transaction and asserts that `tip: 1` is the minimum valid replacement — but it never asserts that `tip: 0` is **rejected**: [3](#0-2) 

Because `increased_enough(0, 0)` returns `true`, a replacement with `tip = 0` also passes the tip dimension check. The only remaining gate is the `max_l2_gas_price` check, which still requires a gas price increase. So an attacker holding a `tip = 0` transaction can replace it with a different `tip = 0` transaction (different calldata, different hash) as long as the gas price increases by the required percentage.

The `fee_escalation_percentage` config field is validated to be in `[1, 100]`: [4](#0-3) 

So the percentage is always positive, yet the invariant stated in the code comment — "any positive percentage always demands a strictly higher fee, even for small values" — is broken for the zero case.

### Impact Explanation

The mempool accepts a replacement transaction that does not increase the tip, violating the fee escalation invariant. An attacker with a `tip = 0` transaction in the mempool can:

1. Repeatedly replace it with a different `tip = 0` transaction (different calldata/hash) by only paying the gas price escalation cost.
2. Effectively update transaction content without ever paying a higher tip, bypassing the anti-spam purpose of tip escalation.
3. Cause the mempool to accept transactions it should reject under the documented fee escalation policy.

This maps to: **High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

### Likelihood Explanation

`tip = 0` is a valid transaction field — no gateway or mempool check enforces a minimum tip. The `fee_escalation_percentage` is always ≥ 1 per config validation, so the code comment's invariant is expected to hold for all configurations. The zero-tip path is reachable by any user submitting a standard V3 transaction with `tip = 0`, which is a common pattern for transactions that do not need priority ordering.

### Recommendation

In `increased_enough`, enforce a minimum required increase of 1 when `existing_value == 0` and `percentage > 0`:

```rust
fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
    let percentage = u128::from(self.config.static_config.fee_escalation_percentage);
    let Some(escalation_qualified_value) = existing_value
        .checked_mul(percentage)
        .map(|scaled| scaled.div_ceil(100))
        .map(|required_increase| required_increase.max(1))  // always require at least +1
        .and_then(|required_increase| existing_value.checked_add(required_increase))
    else {
        return false;
    };
    incoming_value >= escalation_qualified_value
}
```

This ensures that even when `existing_value = 0`, the replacement must provide `incoming_value >= 1`, consistent with the stated invariant.

### Proof of Concept

```rust
#[test]
fn test_zero_tip_bypass() {
    // Existing transaction with tip=0.
    let existing_tx = tx!(tx_hash: 0, tip: 0, max_l2_gas_price: 10);
    let mempool = MempoolTestContentBuilder::new()
        .with_pool([existing_tx])
        .with_fee_escalation_percentage(10)
        .build_full_mempool();

    // Replacement with tip=0 (no tip increase at all).
    // increased_enough(0, 0) == true because 0 >= 0+ceil(0*10/100)=0.
    // Gas price increases from 10 to 12 (satisfies gas price escalation).
    let zero_tip_replacement = add_tx_input!(tx_hash: 1, tip: 0, max_l2_gas_price: 12);

    // This should be REJECTED (tip did not increase) but is ACCEPTED.
    validate_and_add_tx_and_verify_replacement_in_pool(mempool, zero_tip_replacement);
}
```

The replacement with `tip = 0` passes `increased_enough(0, 0)` for the tip dimension and `increased_enough(10, 12)` for the gas price dimension, so `should_replace_tx` returns `true` and the replacement is admitted — despite the tip not increasing at all.

### Citations

**File:** crates/apollo_mempool/src/mempool.rs (L807-819)
```rust
    fn should_replace_tx(
        &self,
        existing_tx: &TransactionReference,
        incoming_tx: &TransactionReference,
    ) -> bool {
        let [existing_tip, incoming_tip] =
            [existing_tx, incoming_tx].map(|tx| u128::from(tx.tip.0));
        let [existing_max_l2_gas_price, incoming_max_l2_gas_price] =
            [existing_tx, incoming_tx].map(|tx| tx.max_l2_gas_price.0);

        self.increased_enough(existing_tip, incoming_tip)
            && self.increased_enough(existing_max_l2_gas_price, incoming_max_l2_gas_price)
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L821-836)
```rust
    fn increased_enough(&self, existing_value: u128, incoming_value: u128) -> bool {
        let percentage = u128::from(self.config.static_config.fee_escalation_percentage);

        // Round up the required increase so any positive percentage always demands a strictly
        // higher fee, even for small values.
        let Some(escalation_qualified_value) = existing_value
            .checked_mul(percentage)
            .map(|scaled| scaled.div_ceil(100))
            .and_then(|required_increase| existing_value.checked_add(required_increase))
        else {
            // Overflow occurred during calculation; reject the transaction.
            return false;
        };

        incoming_value >= escalation_qualified_value
    }
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L845-859)
```rust
#[rstest]
fn test_fee_escalation_valid_replacement_minimum_values() {
    // Setup.
    let min_gas_price = 1;
    let tx = tx!(tx_hash: 0, tip: 0, max_l2_gas_price: min_gas_price);
    let mempool = MempoolTestContentBuilder::new()
        .with_pool([tx])
        .with_fee_escalation_percentage(10)
        .build_full_mempool();

    // Test and assert: smallest replacement that clears the minimum bump on both bounds.
    let valid_replacement_input =
        add_tx_input!(tx_hash: 1, tip: 1, max_l2_gas_price: min_gas_price + 1);
    validate_and_add_tx_and_verify_replacement_in_pool(mempool, valid_replacement_input);
}
```

**File:** crates/apollo_mempool_config/src/config.rs (L65-66)
```rust
    #[validate(range(min = 1, max = 100))]
    pub fee_escalation_percentage: u8, // E.g., 10 for a 10% increase.
```
