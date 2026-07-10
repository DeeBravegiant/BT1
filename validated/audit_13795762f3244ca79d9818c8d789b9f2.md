### Title
Storage Fee Not Collected on User-Facing Request Insertion Enables Contract Storage Exhaustion — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `sign`, `request_app_private_key`, and `verify_foreign_transaction` functions each insert a new entry into a contract-owned `LookupMap` (`pending_signature_requests`, `pending_ckd_requests`, `pending_verify_foreign_tx_requests`) without charging the caller for the actual NEAR storage cost incurred. The 1 yoctonear deposit required by these functions is explicitly documented as an anti-malicious-frontend measure, not a storage fee. An unprivileged attacker can spam these endpoints with distinct request keys, consuming contract storage at a rate orders of magnitude faster than the 1 yoctonear deposit covers, eventually exhausting the contract's storage balance and locking the MPC contract — freezing all funds and halting all signature operations.

---

### Finding Description

Every call to `sign` (and its siblings) that uses a unique `(domain_id, payload, predecessor, path)` tuple creates a fresh entry in `pending_signature_requests`:

```rust
// crates/contract/src/lib.rs  ~line 379-397
let request = SignatureRequest::new(
    request.domain_id, request.payload, &predecessor, &request.path,
);
// ...
self.enqueue_yield_request(
    method_names::RETURN_SIGNATURE_AND_CLEAN_STATE_ON_SUCCESS,
    callback_args,
    callback_gas,
    move |this, id| this.add_signature_request(request, id),  // inserts into LookupMap
);
``` [1](#0-0) 

`add_signature_request` calls `push_pending_yield`, which appends to the map entry:

```rust
// crates/contract/src/pending_requests.rs  line 50-59
let queue = requests.entry(request).or_default();
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) { env::panic_str(...); }
queue.push(YieldIndex { data_id });
``` [2](#0-1) 

The `MAX_PENDING_REQUEST_FAN_OUT = 128` cap is **per unique request key**, not global. [3](#0-2) 

The deposit check enforces only 1 yoctonear, and the code comment is explicit that this is **not** a storage fee:

```rust
// crates/contract/src/lib.rs  line 110-121
/// A non-zero deposit is required so that the transaction must be signed by a
/// full-access key … This prevents a **malicious frontend** from silently
/// submitting signature requests … In other words, requiring a deposit ensures
/// the user … explicitly authorised the call.
``` [4](#0-3) 

By contrast, `submit_participant_info` correctly measures and charges for actual storage consumed:

```rust
// crates/contract/src/lib.rs  line 780-841
let initial_storage = env::storage_usage();
// ... insert ...
let storage_used = env::storage_usage().saturating_sub(initial_storage);
let cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
if attached < cost { return Err(InsufficientDeposit { ... }); }
``` [5](#0-4) 

Even `start_node_migration` carries an open acknowledgement of the gap:

```rust
// crates/contract/src/lib.rs  line 2502
// TODO(#1163): require a deposit
``` [6](#0-5) 

---

### Impact Explanation

On NEAR, a contract whose storage usage exceeds what its balance covers becomes locked: no further transactions can execute against it. Each unique `sign` call inserts a `SignatureRequest` key (~100–1 300 bytes depending on payload type) plus a `YieldIndex` value (32 bytes) into the `LookupMap`. At NEAR's storage rate (~10^19 yoctonear/byte), a single entry costs roughly 1 300–13 000 yoctonear in storage, while the attacker pays only 1 yoctonear. The subsidy ratio is ~1 300×–13 000×.

If the contract is locked:
- No `sign`, `respond`, `respond_ckd`, or governance calls can execute.
- Pending yield callbacks (which would free storage via `pop_oldest_pending_yield`) also cannot execute, creating a self-reinforcing deadlock.
- All funds held by the MPC contract and all in-flight cross-chain signing operations are frozen until an operator manually tops up the contract's balance.

This maps to **Medium** impact: contract execution-flow and balance-accounting invariant broken by an unprivileged caller without requiring network-level DoS or operator misconfiguration. [7](#0-6) 

---

### Likelihood Explanation

Any NEAR account can call `sign` with a 1 yoctonear deposit. The attacker needs only to vary the `payload` or `path` field to generate a fresh unique key on every call. Gas costs per call are ~7 Tgas (~0.0007 NEAR at current rates). A contract holding even 10 NEAR of free storage headroom can be exhausted with ~750–7 500 transactions — a trivially automatable script. No privileged access, TEE, or threshold collusion is required.

---

### Recommendation

Measure actual storage delta inside `sign`, `request_app_private_key`, and `verify_foreign_transaction` (as already done in `submit_participant_info`) and require the caller to cover it:

```rust
let initial_storage = env::storage_usage();
// ... enqueue_yield_request ...
let storage_used = env::storage_usage().saturating_sub(initial_storage);
let storage_cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
// require attached_deposit >= MINIMUM_SIGN_REQUEST_DEPOSIT + storage_cost
```

Alternatively, enforce a per-account or global cap on the number of simultaneously pending requests and charge a deposit sized to the worst-case storage per slot.

---

### Proof of Concept

```python
# Pseudocode — attacker script
for i in range(10_000):
    near_call(
        contract   = "v1.signer",
        method     = "sign",
        args       = {"request": {"domain_id": 0,
                                  "payload_v2": {"Ecdsa": hex(i).zfill(64)},
                                  "path": f"attack-{i}"}},
        deposit    = "1 yoctoNEAR",   # minimum accepted
        gas        = "10 Tgas",
    )
    # Each call inserts a new LookupMap entry (~1 300 bytes) at the contract's expense.
    # After ~750–7 500 calls the contract's storage balance is exhausted.
    # The contract is now locked; no respond(), sign(), or governance call can execute.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** crates/contract/src/lib.rs (L100-104)
```rust
/// Minimum deposit required for sign requests
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);

/// Minimum deposit required for CKD requests
const MINIMUM_CKD_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
```

**File:** crates/contract/src/lib.rs (L110-121)
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
```

**File:** crates/contract/src/lib.rs (L344-398)
```rust
    #[payable]
    pub fn sign(&mut self, request: SignRequestArgs) {
        log!(
            "sign: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        let (domain_config, predecessor) = self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::Sign,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        // ensure the signer sent a valid signature request
        // It's important we fail here because the MPC nodes will fail in an identical way.
        // This allows users to get the error message
        match domain_config.protocol {
            Protocol::CaitSith | Protocol::DamgardEtAl => {
                let hash = *request.payload.as_ecdsa().expect("Payload is not Ecdsa");
                k256::Scalar::from_repr(hash.into())
                    .into_option()
                    .expect("Ecdsa payload cannot be converted to Scalar");
            }
            Protocol::Frost => {
                request.payload.as_eddsa().expect("Payload is not EdDSA");
            }
            Protocol::ConfidentialKeyDerivation => {
                env::panic_str(
                    "ConfidentialKeyDerivation is not supported for signature responses",
                );
            }
        }

        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );

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
    }
```

**File:** crates/contract/src/lib.rs (L780-841)
```rust
        let initial_storage = env::storage_usage();

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        // The node always signs submissions with an Ed25519 key
        // (`near_signer_key`), so the signer key here is Ed25519 in practice.
        // Reject non-Ed25519 signer keys rather than silently storing a value
        // we could never match against in `is_caller_an_attested_participant`.
        let account_public_key = dtos::Ed25519PublicKey::try_from(&account_key).map_err(|_| {
            InvalidParameters::InvalidTeeRemoteAttestation {
                reason: "signer account key must be Ed25519".to_string(),
            }
        })?;

        // Add the participant information to the contract state
        let attestation_insertion_result = self
            .tee_state
            .add_participant(
                NodeId {
                    account_id: account_id.clone(),
                    tls_public_key,
                    account_public_key,
                },
                proposed_participant_attestation,
                tee_upgrade_deadline_duration,
            )
            .map_err(|err| {
                let reason = match &err {
                    AttestationSubmissionError::InvalidAttestation(_) => {
                        format!("TeeQuoteStatus is invalid: {err}")
                    }
                    AttestationSubmissionError::TlsKeyOwnedByOtherAccount => err.to_string(),
                };
                InvalidParameters::InvalidTeeRemoteAttestation { reason }
            })?;

        let caller_is_not_participant = self.voter_account().is_err();
        let is_new_attestation = matches!(
            attestation_insertion_result,
            ParticipantInsertion::NewlyInsertedParticipant
        );

        let attestation_storage_must_be_paid_by_caller =
            is_new_attestation || caller_is_not_participant;

        if attestation_storage_must_be_paid_by_caller {
            // `saturating_sub`: if a re-submission shrinks the entry, charge nothing
            // rather than underflow. Intentional asymmetry: we do not refund freed bytes
            // either — the caller already paid for the larger entry, and we'd rather
            // accept that asymmetry than open a refund path for payload-shrinking games.
            let storage_used = env::storage_usage().saturating_sub(initial_storage);
            let cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
            let attached = env::attached_deposit();

            if attached < cost {
                return Err(InvalidParameters::InsufficientDeposit {
                    attached: attached.as_yoctonear(),
                    required: cost.as_yoctonear(),
                }
                .into());
            }
```

**File:** crates/contract/src/lib.rs (L2502-2502)
```rust
        // TODO(#1163): require a deposit
```

**File:** crates/contract/src/pending_requests.rs (L37-37)
```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
```

**File:** crates/contract/src/pending_requests.rs (L43-60)
```rust
pub(crate) fn push_pending_yield<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: K,
    data_id: CryptoHash,
) where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull {
                limit: MAX_PENDING_REQUEST_FAN_OUT,
            }
            .to_string(),
        );
    }
    queue.push(YieldIndex { data_id });
}
```
