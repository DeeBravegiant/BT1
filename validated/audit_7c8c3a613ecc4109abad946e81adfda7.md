### Title
Caller-Agnostic Queue Key in `verify_foreign_transaction` Enables Targeted Request-Lifecycle DoS - (File: `crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`, `crates/contract/src/dto_mapping.rs`)

---

### Summary

The `verify_foreign_transaction()` contract method uses a **caller-agnostic** map key (`VerifyForeignTransactionRequest`) that does not include the submitting account's identity. Combined with the hard cap of 128 concurrent yields per key (`MAX_PENDING_REQUEST_FAN_OUT`), any unprivileged attacker can pre-fill the queue for a known foreign-chain transaction ID, causing every subsequent legitimate submission for that transaction to panic with `PendingRequestQueueFull` and receive no response. The attacker can sustain this indefinitely at negligible cost, permanently blocking bridge users from obtaining the MPC-signed attestation they need to release funds.

---

### Finding Description

**Root cause — caller-agnostic queue key**

`args_into_verify_foreign_tx_request()` converts the user-supplied `VerifyForeignTransactionRequestArgs` into a `VerifyForeignTransactionRequest` that contains only `(request, domain_id, payload_version)` — the caller's `predecessor_account_id` is silently dropped:

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

The resulting struct is used directly as the map key in `pending_verify_foreign_tx_requests`:

```rust
// crates/near-mpc-contract-interface/src/types/foreign_chain.rs  lines 124-128
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [2](#0-1) 

This is explicitly acknowledged in the test suite:

> "a different account would today be blocked from receiving a response by alice's submission"
> "both yields are queued under the single (caller-agnostic) request key" [3](#0-2) 

**Contrast with `sign()`**

`sign()` avoids this by folding the caller's identity into the key via `derive_tweak(predecessor_id, path)`, so each caller gets an independent queue slot:

```rust
// crates/contract/src/lib.rs  lines 379-384
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,   // ← caller included
    &request.path,
);
``` [4](#0-3) 

**The cap and its panic behaviour**

`push_pending_yield` enforces a hard cap of 128 entries per key. When the cap is reached it calls `env::panic_str`, which reverts the entire transaction — the NEAR yield promise is never created and the caller receives no response:

```rust
// crates/contract/src/pending_requests.rs  lines 37, 50-58
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

let queue = requests.entry(request).or_default();
if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
    env::panic_str(
        &RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }
            .to_string(),
    );
}
``` [5](#0-4) 

---

### Impact Explanation

The primary production use case for `verify_foreign_transaction` is the Omnibridge inbound flow: a user locks funds on a foreign chain (Bitcoin, Ethereum, etc.) and calls `verify_foreign_transaction` to obtain an MPC-signed attestation that the transaction finalized. That attestation is then used to release the equivalent funds on NEAR.

If an attacker keeps the queue full for a specific `(chain, tx_id, domain_id)` tuple, the bridge user's call always panics and they never receive the attestation. Their locked foreign-chain funds cannot be released. The attacker can sustain this indefinitely because:

1. MPC nodes eventually call `respond_verify_foreign_tx`, which drains the entire queue — all 128 attacker yields receive the response.
2. The attacker immediately re-submits 128 new calls before the victim can retry.
3. Cost per cycle: 128 × 1 yoctoNEAR deposit + gas ≈ negligible.

This matches the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

---

### Likelihood Explanation

- The attack requires no privileged access — `verify_foreign_transaction` is a public, permissionless method.
- Foreign-chain transaction IDs are public; an attacker can observe any pending bridge deposit on-chain and compute the exact `VerifyForeignTransactionRequest` key before the victim submits.
- The deposit cost (1 yoctoNEAR per call) is negligible; 128 calls cost less than a fraction of a cent.
- The attacker recovers the deposit when yields time out (NEAR yield-resume on timeout is a no-op, but the deposit is returned to the caller).
- No threshold collusion, TEE bypass, or network-level DoS is required.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the approach used by `sign()`. Concretely:

1. Add a `caller: AccountId` field to `VerifyForeignTransactionRequest` (or derive a `tweak` from `(predecessor_id, derivation_path)` as `sign()` does).
2. Populate it from `env::predecessor_account_id()` inside `verify_foreign_transaction()` before calling `args_into_verify_foreign_tx_request()`.

This makes each caller's queue slot independent, so an attacker cannot fill another user's slot. The fan-out behaviour for *the same caller* submitting duplicates is preserved.

---

### Proof of Concept

```
// Attacker knows victim will verify Bitcoin tx_id = [0xAB; 32]
// on domain_id = 2, payload_version = V1.

// Step 1: Attacker submits 128 identical calls from 128 sybil accounts
//         (or the same account 128 times — the key is caller-agnostic).
for i in 0..128 {
    call verify_foreign_transaction({
        request: Bitcoin { tx_id: [0xAB; 32], confirmations: 1, extractors: [BlockHash] },
        domain_id: 2,
        payload_version: V1,
    }) with deposit 1 yoctoNEAR from attacker_account_i;
}
// Queue for VerifyForeignTransactionRequest { Bitcoin([0xAB;32]), 2, V1 } is now full (128).

// Step 2: Victim submits their legitimate call.
call verify_foreign_transaction({ same args }) from victim_account;
// → env::panic_str("Pending-request queue is full for this request key (limit: 128).")
// → Transaction reverts. Victim's yield is never created. No response ever arrives.
// → Victim's bridge deposit on Bitcoin is permanently locked.

// Step 3: MPC nodes respond, draining the queue.
//         Attacker's 128 yields all receive the verification result.

// Step 4: Attacker immediately re-fills the queue (repeat from Step 1).
//         Victim can never get through.
```

The `push_pending_yield` panic path is exercised by the existing unit test `add_signature_request__should_panic_when_pending_queue_is_full` (line 3301), confirming the revert behaviour is real and not hypothetical. [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L3242-3255)
```rust
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
