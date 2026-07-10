### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Allows Queue Saturation, Blocking Legitimate Foreign-Chain Verification - (`crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint stores pending requests under a key that contains no caller identity. Any unprivileged account can fill the per-key fan-out queue (capped at 128) for a targeted foreign transaction ID at negligible cost (1 yoctonear + gas per slot), permanently blocking legitimate users from queuing their own verification of that same transaction until all attacker-controlled yields time out.

---

### Finding Description

`SignatureRequest` (used by `sign`) embeds a `tweak` derived from `(predecessor_id, path)`, so every caller gets a distinct map key and an independent queue slot.

`VerifyForeignTransactionRequest` (used by `verify_foreign_transaction`) contains only `(request, domain_id, payload_version)` — **no caller identity**:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [1](#0-0) 

All callers submitting the same `(tx_id, chain, domain_id, payload_version)` are therefore queued under a single shared map entry in `pending_verify_foreign_tx_requests`. The codebase itself acknowledges this in a test comment:

> "caller bob submits the identical request — a different account would today be blocked from receiving a response by alice's submission." [2](#0-1) 

The fan-out queue for each key is hard-capped at `MAX_PENDING_REQUEST_FAN_OUT = 128`:

```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
``` [3](#0-2) 

When the queue is full, `push_pending_yield` panics with `PendingRequestQueueFull`, rejecting any further submissions for that key: [4](#0-3) 

The only admission cost is 1 yoctonear per call (the minimum deposit): [5](#0-4) 

An attacker submits 128 `verify_foreign_transaction` calls targeting a specific `tx_id` (from one or many accounts). Each call costs 1 yoctonear + ~7 Tgas. After 128 calls the queue is saturated. Any subsequent legitimate call for the same `tx_id` is rejected with `PendingRequestQueueFull`. The attacker's yields time out after NEAR's yield-resume window (~200 blocks), but the attacker can immediately re-fill the queue, maintaining the block indefinitely at a cost of roughly 128 × gas per ~200-block cycle. [6](#0-5) 

---

### Impact Explanation

The `verify_foreign_transaction` flow is the on-chain gateway for the Omnibridge inbound path: a user deposits funds on a foreign chain (Bitcoin, Ethereum, etc.) and must call `verify_foreign_transaction` to obtain the MPC-signed attestation needed to claim those funds on NEAR. If an attacker saturates the queue for the specific `tx_id` of the user's deposit, the user cannot queue their verification request. If the bridge contract enforces a claim deadline (a common bridge design), the user's funds are permanently lost. Even without a hard deadline, the user is blocked from claiming until the attacker stops, which is a direct financial harm.

This matches the allowed Medium impact: **"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."**

---

### Likelihood Explanation

- The target `tx_id` is publicly visible on the foreign chain the moment the deposit is broadcast.
- The attacker needs only 128 NEAR transactions (each ~7 Tgas, ~0.0007 NEAR gas cost) to fill the queue — total cost under $1 at current prices.
- No privileged access, no threshold collusion, and no TEE bypass is required.
- The attack is reachable directly through the public `verify_foreign_transaction` endpoint.
- The attacker can sustain the block indefinitely by re-filling the queue every ~200 blocks.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, analogous to how `SignatureRequest` incorporates the caller via `tweak`: [7](#0-6) 

Adding a `caller: AccountId` field to `VerifyForeignTransactionRequest` (and deriving it from `env::predecessor_account_id()` inside `verify_foreign_transaction`) gives each caller an independent queue slot, eliminating the shared-key saturation vector. The fan-out deduplication benefit (multiple callers sharing one MPC computation for the same `tx_id`) can be preserved at the node layer without exposing the shared contract-state key to abuse.

---

### Proof of Concept

```rust
// Attacker fills the queue for a specific Bitcoin tx_id
for _ in 0..128 {
    contract.verify_foreign_transaction(VerifyForeignTransactionRequestArgs {
        domain_id: foreign_tx_domain_id,
        payload_version: ForeignTxPayloadVersion::V1,
        request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
            tx_id: VICTIM_TX_ID.into(),  // publicly visible on Bitcoin
            confirmations: 1.into(),
            extractors: vec![BitcoinExtractor::BlockHash],
        }),
    });
    // cost: 1 yoctonear + ~7 Tgas each
}

// Legitimate user's call now panics with PendingRequestQueueFull
contract.verify_foreign_transaction(VerifyForeignTransactionRequestArgs {
    domain_id: foreign_tx_domain_id,
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: VICTIM_TX_ID.into(),
        confirmations: 1.into(),
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
});
// → panics: "Pending-request queue is full for this request key (limit: 128)"
```

The existing unit test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` already demonstrates that Alice and Bob share the same queue entry for identical requests, confirming the caller-agnostic key design that enables this attack. [8](#0-7)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/lib.rs (L101-104)
```rust
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);

/// Minimum deposit required for CKD requests
const MINIMUM_CKD_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
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

**File:** crates/contract/src/pending_requests.rs (L37-37)
```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
```

**File:** crates/contract/src/pending_requests.rs (L50-58)
```rust
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(
            &RequestError::PendingRequestQueueFull {
                limit: MAX_PENDING_REQUEST_FAN_OUT,
            }
            .to_string(),
        );
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
