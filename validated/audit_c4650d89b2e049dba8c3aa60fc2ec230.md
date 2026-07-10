### Title
Caller-Agnostic `verify_foreign_transaction` Request Key Enables Queue-Filling DoS Against Bridge Users - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

### Summary
The `verify_foreign_transaction` endpoint stores pending requests under a key (`VerifyForeignTransactionRequest`) that does **not** include the caller's account ID. Because the fan-out queue for each key is capped at 128 entries, any unprivileged NEAR account can fill the queue for a specific foreign transaction by submitting 128 identical requests, permanently blocking legitimate bridge users from submitting that same request until the queue is drained — and then immediately refilling it.

### Finding Description
`SignatureRequest` (used by `sign()`) encodes the caller identity into its map key via a tweak derived from `(predecessor_id, path)`: [1](#0-0) 

This means two different callers signing the same payload with the same path produce **different** map keys and cannot interfere with each other's queue slots.

`VerifyForeignTransactionRequest`, the map key for `pending_verify_foreign_tx_requests`, contains only the chain-level request data — no caller identity: [2](#0-1) 

The contract itself acknowledges this design in a test comment: [3](#0-2) 

The `verify_foreign_transaction` handler converts args to this caller-agnostic key at line 549 without ever reading `env::predecessor_account_id()` into the key: [4](#0-3) 

The fan-out queue is capped at `MAX_PENDING_REQUEST_FAN_OUT = 128` to bound the gas cost of `respond*` draining it: [5](#0-4) 

When the cap is reached, `push_pending_yield` panics with `PendingRequestQueueFull`, causing the caller's transaction to revert: [6](#0-5) 

### Impact Explanation
An attacker who knows a bridge user is about to call `verify_foreign_transaction` for a specific foreign transaction (e.g., a Bitcoin tx_id) can submit 128 identical requests for that same tx_id first, filling the queue. The legitimate user's subsequent call panics with `PendingRequestQueueFull`. The attacker can continuously refill the queue after each MPC drain cycle, keeping the bridge user's funds locked on the foreign chain indefinitely. This is a request-lifecycle manipulation that breaks the bridge's production safety invariant: a user who has finalized a foreign-chain transaction cannot get it verified and their NEAR-side bridge operation cannot complete.

**Impact: Medium** — request-lifecycle manipulation that breaks production bridge accounting invariants without requiring network-level DoS or operator misconfiguration.

### Likelihood Explanation
**Likelihood: Medium** — the attack requires only a NEAR account and 128 × 1 yoctonear deposits (negligible cost). The attacker must observe the target tx_id on-chain (trivially public) and submit before or concurrently with the victim. No privileged access, threshold collusion, or TEE compromise is needed. The attacker has economic incentive if they are a competing bridge operator or are shorting assets that depend on the bridge completing.

### Recommendation
Include `env::predecessor_account_id()` in the `VerifyForeignTransactionRequest` key, mirroring how `sign()` encodes caller identity into the `SignatureRequest` tweak. Alternatively, add a per-caller nonce or timestamp field so that two different callers for the same foreign tx_id produce distinct map keys and cannot consume each other's queue slots.

### Proof of Concept
1. Alice observes Bitcoin tx_id `[0xAA; 32]` finalized and prepares to call `verify_foreign_transaction({Bitcoin([0xAA;32]), domain_id: 0, V1})`.
2. Mallory (any NEAR account) submits the identical `verify_foreign_transaction` call 128 times in rapid succession, filling `pending_verify_foreign_tx_requests[{Bitcoin([0xAA;32]), 0, V1}]` to the `MAX_PENDING_REQUEST_FAN_OUT` cap.
3. Alice's call panics: `PendingRequestQueueFull { limit: 128 }`.
4. MPC nodes eventually respond, draining the queue. Mallory immediately resubmits 128 requests.
5. Alice's bridge funds remain locked on Bitcoin indefinitely.

The test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` at line 3208 of `crates/contract/src/lib.rs` explicitly demonstrates that two different callers share the same queue slot — confirming the root cause is present in production code. [7](#0-6)

### Citations

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/contract/src/lib.rs (L549-556)
```rust
        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
        );
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
