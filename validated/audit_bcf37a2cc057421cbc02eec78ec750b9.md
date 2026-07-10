### Title
Attacker Can Saturate `verify_foreign_transaction` Fan-Out Queue to Permanently Block Legitimate Bridge Users - (File: `crates/contract/src/pending_requests.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a **caller-agnostic** request key for its fan-out queue. An attacker can fill this queue to `MAX_PENDING_REQUEST_FAN_OUT = 128` by submitting 128 identical requests at negligible cost (128 yoctonear ≈ $0), causing all subsequent legitimate submissions of the same request to panic with `PendingRequestQueueFull`. The attacker can repeat this indefinitely after each queue drain, creating a sustained, essentially free DOS on any specific foreign-transaction verification.

---

### Finding Description

`pending_requests::push_pending_yield` enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` on the fan-out queue for each request key. When the queue is full, new submissions panic:

```rust
// crates/contract/src/pending_requests.rs
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

pub(crate) fn push_pending_yield<K>(...) {
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

For `sign()` and `request_app_private_key()`, the request key includes the caller's `predecessor_account_id`, so an attacker can only saturate their own queue. However, `VerifyForeignTransactionRequest` is **caller-agnostic** — it does not include the submitter's account ID. This is confirmed by the contract's own unit test:

```rust
// crates/contract/src/lib.rs (line ~3242)
// And: caller bob submits the identical request — a different account would today
// be blocked from receiving a response by alice's submission.
...
// Then: both yields are queued under the single (caller-agnostic) request key.
assert_eq!(
    contract
        .pending_verify_foreign_tx_requests
        .get(&request)
        .map(|q| q.len()),
    Some(2),
    "duplicate foreign-tx requests from different callers should fan out",
);
```

The attack flow:
1. Attacker observes (or anticipates) a target `verify_foreign_transaction` request for a specific foreign-chain tx.
2. Attacker submits 128 identical `verify_foreign_transaction` requests (1 yoctonear deposit each = 128 yoctonear total ≈ $0).
3. Queue for that request key is now full.
4. Legitimate user submits the same request → `env::panic_str("PendingRequestQueueFull { limit: 128 }")` → transaction fails.
5. MPC nodes eventually respond, draining the queue (attacker's 128 slots also get resolved — no loss to attacker).
6. Attacker immediately re-submits 128 requests.
7. Repeat indefinitely.

---

### Impact Explanation

**Medium.** This is a request-lifecycle manipulation that breaks a production safety invariant: legitimate bridge users cannot get their foreign-chain deposits verified. In bridge scenarios, `verify_foreign_transaction` is the on-chain proof step that unlocks funds on NEAR after a deposit on a foreign chain (Bitcoin, Ethereum, etc.). If an attacker can continuously block a specific foreign-tx verification, the bridge cannot release the corresponding NEAR-side funds, effectively locking user assets. The attacker does not need to profit directly — a competing bridge operator or a malicious actor targeting a specific large deposit has clear economic motivation.

---

### Likelihood Explanation

High. The attack requires:
- No special privileges, no participant role, no TEE access.
- No threshold collusion.
- Only a NEAR account and 128 yoctonear per drain cycle (essentially free).
- The target request parameters are observable on-chain once the first legitimate submission lands.

The attacker can sustain the attack indefinitely because the cost per cycle is negligible and the queue refills instantly after each `respond_verify_foreign_tx` drain.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the approach used by `SignatureRequest` and `CKDRequest`. This ensures each caller has an independent queue slot and an attacker cannot saturate the queue for other users. Alternatively, enforce a per-caller sub-limit within the shared queue so that no single account can occupy more than a bounded fraction of the 128 slots.

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(Bitcoin tx 0xABCD...) — 1 yoctonear deposit.
   → Queue for 0xABCD... = [Alice's yield]

2. Attacker submits 128 × verify_foreign_transaction(Bitcoin tx 0xABCD...) — 128 yoctonear total.
   → Queue for 0xABCD... = [Alice's yield, Attacker×127] (full at 128)

3. Alice retries → panic: PendingRequestQueueFull { limit: 128 }

4. MPC nodes call respond_verify_foreign_tx(0xABCD...) → all 128 yields drained.

5. Attacker immediately submits 128 more requests → queue full again.

6. Alice retries → panic: PendingRequestQueueFull { limit: 128 }

7. Repeat indefinitely at ~128 yoctonear per cycle.
```

**Root cause**: `VerifyForeignTransactionRequest` omits the caller's account ID from its key, making the fan-out queue a shared resource across all callers for the same foreign transaction. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/contract/src/pending_requests.rs (L24-60)
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
}
```

**File:** crates/contract/src/lib.rs (L691-754)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/lib.rs (L3208-3263)
```rust
    #[test]
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
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
```
