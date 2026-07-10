### Title
Missing Validation in `update_config` Allows Invalid Gas Parameters to Permanently Break Signing Flow - (File: crates/contract/src/lib.rs)

### Summary

The `update_config` function in the MPC contract applies a new `Config` struct directly with no sanity or bounds checks on any of its fields. Critical gas parameters — particularly `return_signature_and_clean_state_on_success_call_tera_gas` and `key_event_timeout_blocks` — can be set to zero or other invalid values through the threshold-voted config update path, permanently breaking the signing and CKD request lifecycle for all users.

### Finding Description

The entire config update pipeline — `propose_update` → `vote_update` → `do_update` → `update_config` — contains no validation of the `Config` field values at any stage.

`update_config` is the terminal step:

```rust
#[private]
pub fn update_config(&mut self, config: dtos::Config) {
    self.config = config.into();   // no validation whatsoever
}
``` [1](#0-0) 

The `Config` struct contains 14 `u64` fields, all of which are accepted as-is: [2](#0-1) 

The `do_update` path that schedules the `update_config` call also performs no validation:

```rust
Update::Config(config) => {
    let new_config_gas_value = Gas::from_tgas(config.contract_upgrade_deposit_tera_gas);
    promise = promise.function_call(
        method_names::UPDATE_CONFIG,
        serde_json::to_vec(&(&config,)).unwrap(),
        NearToken::from_near(0),
        new_config_gas_value,
    );
}
``` [3](#0-2) 

The `sign()` method reads `return_signature_and_clean_state_on_success_call_tera_gas` directly from `self.config` and passes it as the gas budget for the yield-resume callback:

```rust
let callback_gas = Gas::from_tgas(
    self.config
        .return_signature_and_clean_state_on_success_call_tera_gas,
);
// ...
self.enqueue_yield_request(
    method_names::RETURN_SIGNATURE_AND_CLEAN_STATE_ON_SUCCESS,
    callback_args,
    callback_gas,
    move |this, id| this.add_signature_request(request, id),
);
``` [4](#0-3) 

The same field is consumed identically in `verify_foreign_transaction`: [5](#0-4) 

The `sign_call_gas_attachment_requirement_tera_gas` field is used as the minimum gas gate in `check_request_preconditions`: [6](#0-5) 

The code comment in `config.rs` itself acknowledges one dangerous boundary (`contract_upgrade_deposit_tera_gas` must be < 300) but enforces nothing: [7](#0-6) 

### Impact Explanation

**Scenario A — `return_signature_and_clean_state_on_success_call_tera_gas = 0`:**  
Every subsequent `sign()` or `verify_foreign_transaction()` call creates a yield-resume promise with 0 Tgas for the callback. The callback (`return_signature_and_clean_state_on_success`) runs out of gas immediately and fails. All pending signature requests time out via `fail_on_timeout`. The signing service is permanently broken for all users until a corrective config vote is passed — which itself requires threshold agreement and may be slow or impossible if the network is in a degraded state.

**Scenario B — `key_event_timeout_blocks = 0`:**  
All in-progress key events (DKG, resharing) immediately time out on the next block. The contract becomes stuck in `Initializing` or `Resharing` state, unable to produce signatures.

**Scenario C — `sign_call_gas_attachment_requirement_tera_gas = 0`:**  
The gas gate in `check_request_preconditions` always passes (any `prepaid_gas >= 0`). Users can submit sign requests with near-zero gas, causing the callback to fail with out-of-gas, permanently freezing those requests.

These scenarios match the allowed impact: **request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants**.

### Likelihood Explanation

The config update requires threshold participants to vote for the same `Config` value. This is a governance action, not a single-owner action. However:

1. The `Config` struct has 14 fields. Participants reviewing a proposed config update may focus on the changed field and not audit every gas value.
2. There is no on-chain feedback (no error, no event) if a gas value is set to an operationally invalid value — the update is accepted silently.
3. A malicious participant can propose a config that looks reasonable (e.g., changing only `key_event_timeout_blocks`) while embedding `return_signature_and_clean_state_on_success_call_tera_gas = 0`, relying on other participants not checking every field.
4. The comment in `config.rs` itself shows the developers are aware of at least one dangerous boundary (`< 300` for `contract_upgrade_deposit_tera_gas`) but have not enforced it, suggesting other boundaries are also unguarded.

### Recommendation

Add a `validate()` method on `Config` (or `dtos::Config`) that enforces minimum and maximum bounds on all gas fields, and call it inside `update_config` before applying the new config. At minimum:

- `return_signature_and_clean_state_on_success_call_tera_gas >= 1` (non-zero)
- `return_ck_and_clean_state_on_success_call_tera_gas >= 1`
- `fail_on_timeout_tera_gas >= 1`
- `key_event_timeout_blocks >= 1`
- `contract_upgrade_deposit_tera_gas < 300` (as the comment already documents but does not enforce)
- `sign_call_gas_attachment_requirement_tera_gas >= actual_minimum_needed`

Also validate at `propose_update` time so participants receive an immediate error rather than discovering the problem after the vote succeeds.

### Proof of Concept

1. A participant calls `propose_update` with a `Config` where `return_signature_and_clean_state_on_success_call_tera_gas = 0` and all other fields appear normal.
2. Threshold participants vote via `vote_update`. No validation occurs at any step.
3. `do_update` schedules a call to `update_config` with the invalid config.
4. `update_config` applies `self.config = config.into()` with no checks.
5. Any subsequent `sign()` call creates a yield-resume promise with `callback_gas = Gas::from_tgas(0)`.
6. The `return_signature_and_clean_state_on_success` callback receives 0 gas, panics with out-of-gas, and the request times out.
7. All signature requests are permanently broken until a corrective config vote is executed. [1](#0-0) [8](#0-7) [2](#0-1)

### Citations

**File:** crates/contract/src/lib.rs (L352-357)
```rust
        let (domain_config, predecessor) = self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::Sign,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );
```

**File:** crates/contract/src/lib.rs (L386-397)
```rust
        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_SIGNATURE_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_signature_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L544-556)
```rust
        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L2346-2349)
```rust
    #[private]
    pub fn update_config(&mut self, config: dtos::Config) {
        self.config = config.into();
    }
```

**File:** crates/contract/src/config.rs (L10-13)
```rust
/// Amount of gas to deposit when creating an internal upgrade transaction promise.
/// Note this deposit must be less than 300, as the total gas usage including the
/// initial call itself to vote for the update can not exceed 300 Tgas.
const DEFAULT_CONTRACT_UPGRADE_DEPOSIT_TERA_GAS: u64 = 50;
```

**File:** crates/contract/src/config.rs (L38-71)
```rust
/// Config for V2 of the contract.
#[near(serializers=[borsh, json])]
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Config {
    /// If a key event attempt has not successfully completed within this many blocks,
    /// it is considered failed.
    pub(crate) key_event_timeout_blocks: u64,
    /// The grace period duration for expiry of old mpc image hashes once a new one is added.
    pub(crate) tee_upgrade_deadline_duration_seconds: u64,
    /// Amount of gas to deposit for contract and config updates.
    pub(crate) contract_upgrade_deposit_tera_gas: u64,
    /// Gas required for a sign request.
    pub(crate) sign_call_gas_attachment_requirement_tera_gas: u64,
    /// Gas required for a CKD request.
    pub(crate) ckd_call_gas_attachment_requirement_tera_gas: u64,
    /// Prepaid gas for a `return_signature_and_clean_state_on_success` call.
    pub(crate) return_signature_and_clean_state_on_success_call_tera_gas: u64,
    /// Prepaid gas for a `return_ck_and_clean_state_on_success` call.
    pub(crate) return_ck_and_clean_state_on_success_call_tera_gas: u64,
    /// Prepaid gas for a `fail_on_timeout` call.
    pub(crate) fail_on_timeout_tera_gas: u64,
    /// Prepaid gas for a `clean_tee_status` call.
    pub(crate) clean_tee_status_tera_gas: u64,
    /// Prepaid gas for the reshare-time `clean_invalid_attestations` promise.
    pub(crate) clean_invalid_attestations_tera_gas: u64,
    /// Prepaid gas for a `cleanup_orphaned_node_migrations` call.
    pub(crate) cleanup_orphaned_node_migrations_tera_gas: u64,
    /// Prepaid gas for a `remove_non_participant_update_votes` call.
    pub(crate) remove_non_participant_update_votes_tera_gas: u64,
    /// Prepaid gas for a `clean_foreign_chain_data` call.
    pub(crate) clean_foreign_chain_data_tera_gas: u64,
    /// Prepaid gas for a `remove_non_participant_tee_verifier_votes` call.
    pub(crate) remove_non_participant_tee_verifier_votes_tera_gas: u64,
}
```

**File:** crates/contract/src/update.rs (L195-226)
```rust
    pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
        let entry = self.entries.remove(id)?;

        // Clear all entries as they might be no longer valid
        self.entries.clear();
        self.vote_by_participant.clear();

        let mut promise = Promise::new(env::current_account_id());
        match entry.update {
            Update::Contract(code) => {
                // deploy contract then do a `migrate` call to migrate state.
                promise = promise.deploy_contract(code).function_call(
                    method_names::MIGRATE,
                    Vec::new(),
                    NearToken::from_near(0),
                    gas,
                );
            }
            Update::Config(config) => {
                // If we vote for a new config, we should use
                // the value `contract_upgrade_deposit_tera_gas` from the config
                // as the new gas value
                let new_config_gas_value = Gas::from_tgas(config.contract_upgrade_deposit_tera_gas);
                promise = promise.function_call(
                    method_names::UPDATE_CONFIG,
                    serde_json::to_vec(&(&config,)).unwrap(),
                    NearToken::from_near(0),
                    new_config_gas_value,
                );
            }
        }
        Some(promise)
```
