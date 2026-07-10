### Title
Near-Zero-Cost Queue Flooding Permanently Blocks `verify_foreign_transaction` for Any Target Transaction — (File: `crates/contract/src/pending_requests.rs`, `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a **caller-agnostic** request key, meaning any account can enqueue entries into the fan-out queue for a foreign transaction submitted by a different user. Because the queue cap is 128 and the cost per entry is only 1 yoctoNEAR (≈ $0), an attacker can flood the queue for any specific foreign-chain transaction at essentially zero economic cost, permanently blocking all legitimate callers from ever receiving a verification response for that transaction.

---

### Finding Description

**Root cause — caller-agnostic key for `verify_foreign_transaction`**

For `sign`, the request key is constructed with the caller's account ID as a component:

```rust
let request = SignatureRequest::new(
    request.domain_id,
    request.payload,
    &predecessor,   // caller IS part of the key
    &request.path,
);
``` [1](#0-0) 

For `verify_foreign_transaction`, the key is derived solely from the domain, chain, and payload — the caller is **not** included. The contract's own test explicitly documents this:

```rust
// And: caller bob submits the identical request — a different account would today
// be blocked from receiving a response by alice's submission.
// …
// Then: both yields are queued under the single (caller-agnostic) request key.
assert_eq!(
    contract.pending_verify_foreign_tx_requests.get(&request).map(|q| q.len()),
    Some(2),
    "duplicate foreign-tx requests from different callers should fan out",
);
``` [2](#0-1) 

**Queue cap and enforcement**

`push_pending_yield` enforces a hard cap of `MAX_PENDING_REQUEST_FAN_OUT = 128` entries per key. Once the cap is reached, every subsequent submission panics with `PendingRequestQueueFull`:

```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;

pub(crate) fn push_pending_yield<K>(…) {
    let queue = requests.entry(request).or_default();
    if queue.len() >= usize::from(MAX_PENDING_REQUEST_FAN_OUT) {
        env::panic_str(&RequestError::PendingRequestQueueFull { limit: MAX_PENDING_REQUEST_FAN_OUT }.to_string());
    }
    queue.push(YieldIndex { data_id });
}
``` [3](#0-2) 

**Cost of the attack**

Each submission requires a minimum deposit of 1 yoctoNEAR:

```rust
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
``` [4](#0-3) 

The deposit is not refunded on timeout — `pop_oldest_pending_yield` only removes the `YieldIndex` with no transfer back to the submitter: [5](#0-4) 

Filling the queue costs **128 yoctoNEAR** (≈ $0). Queued entries expire after `REQUEST_EXPIRATION_BLOCKS = 200` blocks (~4 minutes on NEAR). The attacker can continuously refill the queue at a cost of 128 yoctoNEAR per 200-block cycle — economically indistinguishable from zero. [6](#0-5) 

---

### Impact Explanation

`verify_foreign_transaction` is the on-chain gateway for verifying that a transaction occurred on a foreign chain (Bitcoin, Ethereum, etc.) before the MPC network issues a signed attestation. If an attacker targets a specific pending bridge transaction (known `tx_id`, `domain_id`, `chain`), they can:

1. Submit 128 identical `verify_foreign_transaction` requests for that transaction.
2. The queue fills; every subsequent legitimate submission by any user for that transaction is rejected with `PendingRequestQueueFull`.
3. The attacker's 128 entries time out after ~200 blocks; the attacker immediately refills.
4. The legitimate user's bridge operation is permanently stalled — their funds on the foreign chain are locked with no path to receive the MPC attestation needed to release them on NEAR.

This is a **request-lifecycle manipulation that breaks production safety/accounting invariants** — specifically the invariant that a valid, accepted foreign-transaction verification request will eventually be processed. It does not rely on network-level DoS or operator misconfiguration.

**Allowed impact match**: Medium — balance/request-lifecycle manipulation breaking production safety invariants.

---

### Likelihood Explanation

- The attacker needs only a NEAR account and ~128 yoctoNEAR.
- The target transaction's parameters (`tx_id`, `domain_id`, `chain`) are observable on-chain or from the foreign chain's mempool/block explorer.
- No threshold collusion, TEE access, or privileged role is required.
- The attack is fully automatable with a simple script that monitors the queue depth and refills it before entries expire.

---

### Recommendation

Include the caller's `predecessor_account_id` in the `VerifyForeignTransactionRequest` key, mirroring the design of `SignatureRequest`. This ensures that an attacker can only fill their own queue slot, not the queue for a request submitted by a different account. Alternatively, enforce a per-account submission rate limit or require a meaningful economic deposit (not 1 yoctoNEAR) for `verify_foreign_transaction` to make continuous queue flooding economically unsustainable — directly analogous to the recommended mitigation in the reference report (attributing a default protocol fee to make the attack economically unsustainable).

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(tx_id=X, domain=0, chain=Bitcoin)
   → queued at slot 0 under key K(tx_id=X, domain=0, chain=Bitcoin)

2. Attacker (Bob) observes Alice's pending request on-chain.

3. Bob submits 128 identical verify_foreign_transaction(tx_id=X, domain=0, chain=Bitcoin)
   requests, each with 1 yoctoNEAR deposit.
   → queue for K fills to MAX_PENDING_REQUEST_FAN_OUT = 128
   → Alice's original slot is now buried; no new slots available

4. Alice retries → PendingRequestQueueFull panic; transaction reverts.

5. After ~200 blocks, Bob's 128 entries time out (pop_oldest_pending_yield drains them).
   Bob immediately resubmits 128 new requests.

6. Alice's bridge operation is permanently blocked at a cost to Bob of
   128 yoctoNEAR per ~4-minute cycle ≈ $0/day.
``` [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L101-101)
```rust
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);
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

**File:** crates/contract/src/pending_requests.rs (L37-60)
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
}
```

**File:** crates/contract/src/pending_requests.rs (L97-111)
```rust
pub(crate) fn pop_oldest_pending_yield<K>(requests: &mut LookupMap<K, Vec<YieldIndex>>, request: &K)
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let Some(queue) = requests.get_mut(request) else {
        return;
    };
    if queue.is_empty() {
        requests.remove(request);
        return;
    }
    queue.remove(0);
    if queue.is_empty() {
        requests.remove(request);
    }
```

**File:** crates/node/src/requests/queue.rs (L33-33)
```rust
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
