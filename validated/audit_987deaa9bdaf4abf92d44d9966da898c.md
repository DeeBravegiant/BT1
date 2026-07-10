### Title
Caller-Agnostic Queue Key in `verify_foreign_transaction` Enables Targeted Request-Lifecycle Freeze — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` function queues pending requests under a key derived solely from the foreign-chain transaction parameters, with no caller identity component. Combined with the hard `MAX_PENDING_REQUEST_FAN_OUT` cap enforced on every queue entry, an unprivileged attacker can saturate the queue for any specific foreign-tx verification at near-zero cost, causing every subsequent legitimate submission of that same request to panic and revert — permanently blocking the victim's yield from ever being resumed.

---

### Finding Description

**Caller-agnostic queue key.** The test `verify_foreign_transaction__should_queue_duplicates_from_different_callers` explicitly documents and confirms the design: alice and bob submitting identical `VerifyForeignTransactionRequestArgs` are both inserted under the *same* `pending_verify_foreign_tx_requests` map key. [1](#0-0) 

The comment in that test also acknowledges the historical risk: *"a different account would today be blocked from receiving a response by alice's submission."* [2](#0-1) 

**Hard queue cap.** The test `add_signature_request__should_panic_when_pending_queue_is_full` proves that once a queue entry reaches `MAX_PENDING_REQUEST_FAN_OUT` elements, any further `add_*_request` call panics with `"Pending-request queue is full"` and the transaction reverts. [3](#0-2) 

**Near-zero attack cost.** The only barrier to calling `verify_foreign_transaction` is a deposit of exactly 1 yoctoNEAR (`MINIMUM_SIGN_REQUEST_DEPOSIT`). [4](#0-3) 

**Attack path:**

1. Attacker monitors the target foreign chain (e.g., Bitcoin) and identifies a transaction `tx_id` that a victim intends to verify through the MPC contract.
2. Attacker submits `MAX_PENDING_REQUEST_FAN_OUT` identical `verify_foreign_transaction` calls referencing that `tx_id`, paying `MAX_PENDING_REQUEST_FAN_OUT × 1 yoctoNEAR` total.
3. The queue for that request key is now full.
4. Victim's `verify_foreign_transaction` call panics — the NEAR yield is never created, so the victim's callback is never resumed.
5. As MPC nodes drain the attacker's queued yields (by responding), the attacker re-fills the queue before the victim can retry, sustaining the freeze indefinitely.

This is structurally identical to the `safeApprove` analog: an external actor pre-sets shared state (queue occupancy) to a value that causes a required invariant check (queue not full) to fail for any subsequent legitimate caller.

---

### Impact Explanation

`verify_foreign_transaction` is a production entrypoint used to attest that a specific foreign-chain transaction occurred and to extract values from it (e.g., `BlockHash`, amounts). Downstream contracts or bridge flows that depend on the MPC-signed attestation to release or credit funds will be permanently stalled for the targeted `tx_id`. Because the queue key is caller-agnostic, a single attacker can freeze *any* specific foreign-tx verification without needing to know or impersonate the victim — only the on-chain foreign-tx parameters are required. This constitutes **request-lifecycle manipulation that breaks production safety/accounting invariants** (allowed Medium impact).

---

### Likelihood Explanation

- All foreign-chain transaction IDs are publicly observable on their respective chains before any NEAR submission occurs.
- The attacker needs only `MAX_PENDING_REQUEST_FAN_OUT` transactions at 1 yoctoNEAR each — economically negligible.
- No privileged access, key material, or threshold collusion is required.
- The attack is sustainable: the attacker can re-fill the queue each time MPC nodes drain it.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `verify_foreign_transaction` request key, mirroring the design of `SignatureRequest` (which already embeds the predecessor). [5](#0-4) 

Alternatively, enforce a per-account rate limit or require a meaningful deposit (not 1 yoctoNEAR) for `verify_foreign_transaction` to raise the cost of queue saturation above the economic benefit of the attack.

---

### Proof of Concept

```
// Setup: target tx_id = [7u8; 32], domain_id = default, confirmations = 2
let request_args = VerifyForeignTransactionRequestArgs {
    domain_id: DomainId::default().0.into(),
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: [7u8; 32].into(),
        confirmations: 2.into(),
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
};

// Attacker fills the queue (MAX_PENDING_REQUEST_FAN_OUT times, 1 yoctoNEAR each)
for _ in 0..MAX_PENDING_REQUEST_FAN_OUT {
    contract.verify_foreign_transaction(request_args.clone()); // attacker account
}

// Victim submits the same request → PANICS: "Pending-request queue is full"
contract.verify_foreign_transaction(request_args); // victim account
// Victim's yield is never created; their callback is never resumed.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L100-104)
```rust
/// Minimum deposit required for sign requests
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);

/// Minimum deposit required for CKD requests
const MINIMUM_CKD_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
```

**File:** crates/contract/src/lib.rs (L3160-3165)
```rust
        let signature_request = SignatureRequest::new(
            DomainId::default(),
            payload.clone(),
            &context.predecessor_account_id,
            &path,
        );
```

**File:** crates/contract/src/lib.rs (L3209-3263)
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
        assert_eq!(
            contract
                .pending_verify_foreign_tx_requests
                .get(&request)
                .map(|q| q.len()),
            Some(2),
            "duplicate foreign-tx requests from different callers should fan out",
        );
```

**File:** crates/contract/src/lib.rs (L3300-3345)
```rust
    #[test]
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
    }
```
