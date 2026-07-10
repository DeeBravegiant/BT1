### Title
`verify_foreign_transaction` Fan-Out Queue Saturation Enables Targeted Bridge-Execution DoS - (File: `crates/contract/src/pending_requests.rs`)

### Summary

The `verify_foreign_transaction` endpoint uses a request key (`VerifyForeignTransactionRequest`) that does **not** include the caller's account ID. Any unprivileged caller can therefore contribute to the same per-request fan-out queue. Because `push_pending_yield` enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` entries per key, an attacker can cheaply saturate the queue for any specific foreign-chain transaction, permanently blocking every other caller from submitting that same request until the attacker stops refilling.

### Finding Description

`MpcContract::verify_foreign_transaction` in `crates/contract/src/lib.rs` converts caller arguments into a `VerifyForeignTransactionRequest` via `args_into_verify_foreign_tx_request`:

```rust
// crates/contract/src/dto_mapping.rs  lines 840-848
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
``` [1](#0-0) 

The resulting struct contains only `(domain_id, request, payload_version)` — no caller identity:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs  lines 124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [2](#0-1) 

This contrasts sharply with `SignatureRequest`, whose map key is a `tweak` derived from `(predecessor_id, path)`, making each caller's queue independent:

```rust
// crates/near-mpc-crypto-types/src/sign.rs  lines 117-125
impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest { domain_id: domain, tweak, payload }
    }
}
``` [3](#0-2) 

Similarly, `CKDRequest` embeds an `app_id` derived from `(predecessor_id, derivation_path)`: [4](#0-3) 

The shared fan-out queue enforces a cap of 128 entries per key and panics on overflow:

```rust
// crates/contract/src/pending_requests.rs  lines 37, 51-58
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

let queue = requests.entry(request).or_default();
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }.to_string(),
    );
}
``` [5](#0-4) 

An attacker who knows a target `(tx_id, domain_id, payload_version)` — trivially obtained by watching the foreign chain for deposits to a known bridge address — submits 128 `verify_foreign_transaction` calls for that exact tuple, each with the minimum 1 yoctoNEAR deposit. The queue is now full. Every subsequent legitimate call from any account for that transaction panics with `PendingRequestQueueFull`. When MPC nodes eventually respond and drain the queue, the attacker immediately re-submits 128 calls, sustaining the block indefinitely. [6](#0-5) 

### Impact Explanation

A user who has already committed funds on a foreign chain (e.g., sent Bitcoin to a bridge address) depends on `verify_foreign_transaction` returning a valid MPC signature to release the corresponding NEAR-side assets. If the queue for their specific transaction is continuously saturated, the MPC network never processes their request. Their foreign-chain funds are already spent; the NEAR-side release is permanently blocked for as long as the attacker maintains the saturation. This is a targeted, request-lifecycle manipulation that breaks the production bridge-execution safety invariant without requiring any network-level DoS or operator misconfiguration.

### Likelihood Explanation

The attack cost per saturation cycle is 128 × 1 yoctoNEAR in deposits plus gas for 128 calls. Gas is the dominant cost; at current NEAR prices this is on the order of a few NEAR per cycle — negligible relative to the value of a bridge transaction being blocked. The attacker needs only the target `tx_id`, which is public on the foreign chain. Front-running is straightforward: monitor the NEAR mempool for pending `verify_foreign_transaction` calls and submit 128 saturating calls in the same or prior block. No privileged access, TEE compromise, or threshold collusion is required.

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the design of `SignatureRequest` and `CKDRequest`. Concretely, add a `caller: AccountId` field (set to `env::predecessor_account_id()` inside `verify_foreign_transaction`) so that each caller's queue is isolated and no single unprivileged account can saturate another caller's slot. Alternatively, enforce a per-account submission rate limit at the contract level before enqueuing.

### Proof of Concept

```
Attacker (any NEAR account):
  target_tx = BitcoinRpcRequest { tx_id: <victim's deposit tx>, ... }
  for i in 0..128:
      contract.verify_foreign_transaction(
          { domain_id: foreign_tx_domain, request: target_tx, payload_version: V1 },
          deposit = 1 yoctoNEAR
      )
  // Queue for (target_tx, domain_id, V1) is now at MAX_PENDING_REQUEST_FAN_OUT = 128

Victim (bridge contract or user):
  contract.verify_foreign_transaction(
      { domain_id: foreign_tx_domain, request: target_tx, payload_version: V1 },
      deposit = 1 yoctoNEAR
  )
  // → panics: "Pending-request queue is full for this request key (limit: 128)"
  // Victim's foreign-chain funds are committed; NEAR-side release is blocked.

// After MPC nodes respond and drain the queue, attacker re-submits 128 calls.
// Cycle repeats indefinitely at ~few NEAR per cycle cost.
```

The `add_signature_request__should_panic_when_pending_queue_is_full` unit test in `crates/contract/src/lib.rs` already confirms the panic path is reachable at exactly 128 entries: [7](#0-6)

### Citations

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-crypto-types/src/sign.rs (L117-125)
```rust
impl SignatureRequest {
    pub fn new(domain: DomainId, payload: Payload, predecessor_id: &AccountId, path: &str) -> Self {
        let tweak = crate::kdf::derive_tweak(predecessor_id, path);
        SignatureRequest {
            domain_id: domain,
            tweak,
            payload,
        }
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L17-30)
```rust
impl CKDRequest {
    pub fn new(
        app_public_key: dtos::CKDAppPublicKey,
        domain_id: DomainId,
        predecessor_id: &AccountId,
        derivation_path: &str,
    ) -> Self {
        let app_id = derive_app_id(predecessor_id, derivation_path);
        Self {
            app_public_key,
            app_id,
            domain_id,
        }
    }
```

**File:** crates/contract/src/pending_requests.rs (L37-59)
```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

/// Append a yield index to the pending-request fan-out queue for `request`.
///
/// Panics with `RequestError::PendingRequestQueueFull` if the resulting queue would
/// exceed `MAX_PENDING_REQUEST_FAN_OUT`.
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
```

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

        let requested_chain = request.request.chain();
        let supported_chains = self.get_supported_foreign_chains();
        if !supported_chains.contains(&requested_chain) {
            env::panic_str(
                &InvalidParameters::ForeignChainNotSupported {
                    requested: requested_chain,
                }
                .to_string(),
            );
        }

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
    }
```

**File:** crates/contract/src/lib.rs (L3301-3344)
```rust
    fn add_signature_request__should_panic_when_pending_queue_is_full() {
        // Given: a contract with a queue already at the fan-out cap for some request key.
        let (context, mut contract, _) = basic_setup(Curve::Secp256k1, &mut OsRng);
        let signature_request = SignatureRequest::new(
            DomainId::default(),
            Payload::from_legacy_ecdsa([3u8; 32]),
            &context.predecessor_account_id,
            "m/44'\''/60'\''/0'\''/0/0",
        );
        for i in 0..MAX_PENDING_REQUEST_FAN_OUT {
            contract.add_signature_request(signature_request.clone(), [i; 32]);
        }
        assert_eq!(
            contract
                .pending_signature_requests
                .get(&signature_request)
                .map(|q| q.len()),
            Some(usize::from(MAX_PENDING_REQUEST_FAN_OUT)),
        );

        // When: one more append is attempted.
        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            contract.add_signature_request(signature_request.clone(), [0xff; 32]);
        }));

        // Then: it panics with the typed cap-exceeded error and leaves the queue untouched.
        let err = result.expect_err("appending past the cap should panic");
        let msg = err
            .downcast_ref::<String>()
            .map(String::as_str)
            .or_else(|| err.downcast_ref::<&str>().copied())
            .unwrap_or_default();
        assert!(
            msg.contains("Pending-request queue is full"),
            "unexpected panic message: {msg}",
        );
        assert_eq!(
            contract
                .pending_signature_requests
                .get(&signature_request)
                .map(|q| q.len()),
            Some(usize::from(MAX_PENDING_REQUEST_FAN_OUT)),
            "queue should not have grown past the cap",
        );
```
