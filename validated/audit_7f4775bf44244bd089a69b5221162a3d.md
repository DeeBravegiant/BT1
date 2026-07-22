### Title
`Declare` Transactions Bypass `max_l2_gas_amount` Gateway Admission Check — (`crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

The `validate_resource_bounds` function in the stateless transaction validator enforces a `max_l2_gas_amount` ceiling on `Invoke` and `DeployAccount` transactions but explicitly skips that check for `Declare` transactions. An attacker can submit a `Declare` transaction with `l2_gas.max_amount = u64::MAX` and it will be admitted through the gateway and into the mempool, violating the operator-configured admission policy.

---

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
    // ← no check at all
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
``` [1](#0-0) 

The production configuration sets `max_l2_gas_amount = 1_210_000_000` (1.21 billion): [2](#0-1) 

For `Invoke` and `DeployAccount` transactions, any `l2_gas.max_amount` exceeding this value is rejected at the gateway. For `Declare` transactions, the branch is a no-op — the check is entirely absent. The TODO comment is the developers' own acknowledgment that this gap exists and has not been resolved.

The top-level `validate` dispatcher confirms that all three transaction types pass through `validate_resource_bounds`, but only `Invoke` and `DeployAccount` are subject to the ceiling: [3](#0-2) 

The test suite explicitly documents this asymmetry: `test_invalid_max_l2_gas_amount` is parameterised over `[DeployAccount, Invoke]` only — `Declare` is intentionally absent: [4](#0-3) 

---

### Impact Explanation

A `Declare` transaction carrying `l2_gas.max_amount = u64::MAX` (18 446 744 073 709 551 615) passes every stateless check and is forwarded to the mempool. The gateway's operator-configured admission ceiling — the only place this bound is enforced before sequencing — is silently bypassed. Once in the mempool the transaction proceeds to the batcher and blockifier; the blockifier only checks that *actual* gas consumed does not exceed the *declared* maximum, so a declared maximum of `u64::MAX` never triggers a fee-bound failure. The transaction executes and the user pays only for actual gas consumed, but the admission invariant ("no transaction with `l2_gas.max_amount > max_l2_gas_amount` enters the pipeline") is broken for the entire `Declare` type.

This matches the **High** impact tier: *Mempool/gateway/RPC admission accepts invalid transactions before sequencing.*

---

### Likelihood Explanation

Any unprivileged user who can submit a `Declare` transaction can trigger this. No special role, key, or peer relationship is required. The attacker only needs to set `resource_bounds.l2_gas.max_amount` to any value above `1_210_000_000` in an otherwise valid `Declare` transaction. The `check_declare_permissions` guard (which enforces `block_declare` and `authorized_declarer_accounts`) runs before `stateless_tx_validator.validate`, so it does not compensate for the missing bound check. [5](#0-4) 

---

### Recommendation

Remove the `Declare`-specific early-return in `validate_resource_bounds` and apply the same ceiling uniformly:

```rust
// Before (broken):
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(...);
}

// After (fixed):
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If `Declare` transactions legitimately require a higher ceiling (e.g., because Sierra compilation is gas-intensive), introduce a separate `max_l2_gas_amount_declare` config key rather than removing the check entirely.

---

### Proof of Concept

1. Construct a valid `RpcDeclareTransaction::V3` with `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)` and a well-formed Sierra class.
2. POST it to the gateway's `/add_transaction` endpoint.
3. Observe that the gateway returns a transaction hash (success) rather than `MaxGasAmountTooHigh`.
4. Confirm the transaction appears in the mempool.

The existing test `valid_l2_gas_amount_on_declare` already demonstrates this: it passes a `max_amount` of `200` against a config limit of `100` for a `Declare` transaction and asserts `Ok(())`: [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L78-85)
```rust
        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }
```

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L173-201)
```rust
#[rstest]
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    let tx_validator = StatelessTransactionValidator { config };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L260-271)
```rust
fn test_invalid_max_l2_gas_amount(
    #[case] rpc_tx_args: RpcTransactionArgs,
    #[case] expected_error: StatelessTransactionValidatorError,
    #[values(TransactionType::DeployAccount, TransactionType::Invoke)] tx_type: TransactionType,
) {
    let tx_validator =
        StatelessTransactionValidator { config: DEFAULT_VALIDATOR_CONFIG.to_owned() };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_eq!(tx_validator.validate(&tx).unwrap_err(), expected_error);
}
```

**File:** crates/apollo_gateway/src/gateway.rs (L228-236)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
```
