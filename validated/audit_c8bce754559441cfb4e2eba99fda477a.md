### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Enables Queue Exhaustion and Unauthorized MPC Signature Receipt — (`crates/contract/src/lib.rs`, `crates/contract/src/dto_mapping.rs`)

---

### Summary

`verify_foreign_transaction()` builds its pending-request map key from `(ForeignChainRpcRequest, domain_id, payload_version)` — with no caller identity. Any unprivileged account can submit the identical request and be queued under the same key. Because the fan-out queue is hard-capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`, an attacker who pre-fills that queue blocks every subsequent legitimate caller with `PendingRequestQueueFull`. Simultaneously, every attacker-controlled slot in the queue receives the same MPC-signed `VerifyForeignTransactionResponse` that the legitimate bridge caller expected to receive exclusively.

---

### Finding Description

**Root cause — caller identity is silently dropped**

`args_into_verify_foreign_tx_request` converts the user-supplied args into the stored request key: [1](#0-0) 

The resulting `VerifyForeignTransactionRequest` struct contains only `request`, `domain_id`, and `payload_version`: [2](#0-1) 

No `predecessor_id`, no tweak, no caller-binding of any kind. Compare this with `sign()`, which derives a `tweak` from `(predecessor_id, path)` and embeds it in `SignatureRequest`, making every sign-request key caller-specific: [3](#0-2) 

**Attack surface — the bounded fan-out queue**

The fan-out queue is capped at 128 entries per key: [4](#0-3) 

When the cap is reached, `env::panic_str` is called with `PendingRequestQueueFull`, reverting the caller's transaction entirely.

**The `verify_foreign_transaction` entry point does not bind the caller** [5](#0-4) 

`env::predecessor_account_id()` is logged but never incorporated into the request key or the yield callback args.

**Developers acknowledged the tension but left the gap open**

The comment in `pending_requests.rs` explicitly notes that the cap was added to prevent a gas-exhaustion attack on `respond*`, but the cap itself creates the targeted-queue-fill vector: [6](#0-5) 

The unit test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` confirms that different callers sharing the same queue is intentional design — but it does not account for a malicious actor deliberately filling the queue: [7](#0-6) 

---

### Impact Explanation

**Request-lifecycle manipulation (Medium, per allowed scope):**

An attacker who submits 128 `verify_foreign_transaction` calls for a specific foreign transaction (e.g., a Bitcoin `tx_id` used in an Omnibridge inbound flow) before the legitimate bridge service submits its call causes the legitimate call to revert with `PendingRequestQueueFull`. The bridge release for that transaction is permanently blocked for the duration of the request's on-chain lifetime (until it times out), breaking the production safety invariant that a valid foreign-chain event can always be attested.

**Unauthorized MPC signature receipt (escalation path to High):**

Every attacker-controlled slot in the queue receives the same `VerifyForeignTransactionResponse` — the MPC threshold signature over `SHA-256(borsh(ForeignTxSignPayload{request, observed_values}))`: [8](#0-7) 

The signature is not bound to any caller identity. If the consuming bridge contract uses this signature to release funds to whoever presents it (a common pattern for bridge attestation contracts), the attacker can claim the bridged assets that Alice initiated. This crosses into the High impact tier (invalid bridge execution / double-spend conditions).

---

### Likelihood Explanation

- Each `verify_foreign_transaction` call costs 1 yoctonear deposit plus ~7 Tgas. Filling 128 slots costs negligible NEAR.
- NEAR transactions are visible in the mempool before finalization, giving the attacker the same observation window as the Yield/Cauldron scenario in M-01.
- The attack is fully permissionless — no privileged role, no collusion, no TEE access required.
- The attacker is economically motivated: they receive the MPC signature and can use it against bridge contracts.

---

### Recommendation

1. **Bind the request key to the caller.** Include `predecessor_id` (or a hash of it) in `VerifyForeignTransactionRequest`, mirroring how `SignatureRequest` uses a caller-derived tweak. This makes each caller's request key unique, eliminating both the queue-fill attack and the unauthorized-signature-receipt issue.

2. **If caller-agnostic fan-out is intentional** (e.g., to allow multiple bridge services to share one MPC computation), add a per-caller rate limit so a single account cannot consume more than a small fraction of the 128-slot queue for any given request key.

3. **Bridge contracts** consuming `VerifyForeignTransactionResponse` should bind the signature to a specific beneficiary address at the application layer, so possession of the MPC signature alone is insufficient to claim funds.

---

### Proof of Concept

```
1. Alice (bridge service) submits:
     verify_foreign_transaction({ request: Bitcoin(tx_id=X, confirmations=6, extractors=[BlockHash]),
                                   domain_id: 0, payload_version: V1 })
   This enqueues a yield under key K = (Bitcoin(tx_id=X,...), 0, V1).

2. Eve observes Alice's pending NEAR transaction in the mempool.

3. Eve submits 128 identical verify_foreign_transaction calls with the same args.
   Each costs ~1 yoctonear + gas. Total cost: negligible.
   Eve's 128 yields are queued under the same key K.

4. Alice's transaction is processed. push_pending_yield finds queue.len() == 128,
   calls env::panic_str("Pending-request queue is full"), reverting Alice's tx.
   Alice's bridge release is blocked.

5. MPC nodes observe K, verify the Bitcoin transaction, and call respond_verify_foreign_tx.
   resolve_yields_for drains all 128 of Eve's queued yields, delivering the
   VerifyForeignTransactionResponse (payload_hash + MPC signature) to each of Eve's callbacks.

6. Eve holds the MPC threshold signature over SHA-256(borsh({Bitcoin(tx_id=X,...), [BlockHash_value]})).
   Eve presents this signature to the bridge contract to claim Alice's bridged funds.
``` [9](#0-8) [10](#0-9) [11](#0-10)

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

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
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

**File:** crates/contract/src/lib.rs (L749-754)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/lib.rs (L3209-3255)
```rust
    fn verify_foreign_transaction__should_queue_duplicates_from_different_callers() {
        // Given: two different callers will submit the same foreign-tx verification request.
        let mut rng = rand::rngs::StdRng::from_seed([42u8; 32]);
        let (context, mut contract, secret_key) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut rng);
        register_supported_chains(&mut contract, [dtos::ForeignChain::Bitcoin]);
        let SharedSecretKey::Secp256k1(secret_key) = secret_key else {
            unreachable!();
        };

        let request_args = VerifyForeignTransactionRequestArgs {
            domain_id: DomainId::default().0.into(),
            payload_version: ForeignTxPayloadVersion::V1,
            request: dtos::ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
                tx_id: [7u8; 32].into(),
                confirmations: 2.into(),
                extractors: vec![BitcoinExtractor::BlockHash],
            }),
        };
        let request = args_into_verify_foreign_tx_request(request_args.clone());

        // When: caller alice submits the request.
        let alice = AccountId::from_str("alice.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(alice.clone())
                .predecessor_account_id(alice)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args.clone());

        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
        let bob = AccountId::from_str("bob.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(bob.clone())
                .predecessor_account_id(bob)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args);

        // Then: both yields are queued under the single (caller-agnostic) request key.
```

**File:** crates/contract/src/pending_requests.rs (L24-59)
```rust
/// Maximum number of concurrent yield-resume promises that can be queued for a single
/// request key (i.e. the number of duplicate submissions whose responses fan out from
/// one MPC reply).
///
/// The ceiling is needed because `respond*` drains the entire queue in one call: every
/// queued yield triggers a host-side `promise_yield_resume`, paid for out of the
/// responder's 300 TGas budget. Without a cap, an attacker could enqueue enough
/// duplicates to make `respond*` run out of gas and strand every queued caller.
///
/// 128 is validated empirically by the sandbox test
/// `test_contract_request_duplicate_requests_fan_out`, which fills the queue to this
/// cap across all four signature schemes and confirms `respond*` drains it inside its
/// 300 TGas budget.
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L30-48)
```rust
fn build_signature_request(
    request: &VerifyForeignTxRequest,
    foreign_tx_payload: &dtos::ForeignTxSignPayload,
) -> anyhow::Result<SignatureRequest> {
    let payload_hash: [u8; ECDSA_PAYLOAD_SIZE_BYTES] =
        foreign_tx_payload.compute_msg_hash()?.into();
    let payload_bytes: BoundedVec<u8, ECDSA_PAYLOAD_SIZE_BYTES, ECDSA_PAYLOAD_SIZE_BYTES> =
        payload_hash.into();

    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
}
```
