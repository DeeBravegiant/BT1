### Title
Storage Deposit Permanently Locked When Attestations Are Evicted via `clean_invalid_attestations` — (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

Any account that calls `submit_participant_info` pays a storage deposit to cover the on-chain cost of persisting its attestation entry. When that entry is later evicted by `clean_invalid_attestations` (callable by anyone), the storage is freed but the deposit is **never returned** to the original submitter. The deposit is permanently absorbed into the contract's balance, breaking the accounting invariant that storage deposits are returned when the storage they cover is released.

---

### Finding Description

**Step 1 — Deposit is charged in `submit_participant_info`**

In `crates/contract/src/lib.rs` lines 826–848, after inserting the attestation into `stored_attestations`, the contract measures the storage delta and charges the caller exactly that cost:

```rust
let storage_used = env::storage_usage().saturating_sub(initial_storage);
let cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
let attached = env::attached_deposit();
if attached < cost { return Err(...); }
// only excess is refunded; the exact cost is kept
if let Some(diff) = attached.checked_sub(cost) && diff > NearToken::from_yoctonear(0) {
    Promise::new(account_id).transfer(diff).detach();
}
``` [1](#0-0) 

The `stored_attestations` map stores only `(Ed25519PublicKey → VerifiedAttestation)`. **No depositor account ID or deposit amount is recorded alongside the entry.**

**Step 2 — Eviction in `clean_invalid_attestations` issues no refund**

`TeeState::clean_invalid_attestations` (callable by any account, no access control) removes expired or whitelist-stale entries:

```rust
for tls_pk in invalid_tls_keys {
    self.stored_attestations.remove(&tls_pk);
}
``` [2](#0-1) 

Removing the entry frees the storage (the NEAR runtime credits the contract's balance), but because the depositor's identity and deposit amount were never stored, **no refund Promise is issued**. The freed NEAR remains in the contract's balance permanently.

**Step 3 — The condition that triggers deposit loss is routine**

Attestations carry an `expiry_timestamp_seconds` field. Every attestation submitted by a non-participant (or a new participant) will eventually expire. The `clean_invalid_attestations` endpoint is public and permissionless:

```
"doc": "Prunes up to `max_scan` stored attestations that fail re-verification
        (expired or referencing stale whitelists). Callable by anyone while
        the protocol is in `Running`."
``` [3](#0-2) 

The same eviction is also triggered automatically after every resharing via a promise spawned from `vote_reshared`, so the deposit loss occurs even without a direct external call. [4](#0-3) 

---

### Impact Explanation

**Medium.** Every account that submits attestation info and has its entry evicted permanently loses the storage deposit it paid. The deposit is not large per entry, but the loss is systematic: it affects every non-participant node that ever submits attestation info (they always pay the deposit per line 823–824), and it affects participant nodes whose attestations expire between re-submissions. The freed storage accrues silently to the contract's balance with no accounting trail. This breaks the production safety invariant that storage deposits are returned when the storage they cover is released. [5](#0-4) 

---

### Likelihood Explanation

**High.** Attestations expire by design (they carry `expiry_timestamp_seconds`). `clean_invalid_attestations` is permissionless and is also invoked automatically after every resharing. Any node that submits attestation info will eventually have its deposit locked. No special attacker capability is required — the loss occurs through normal protocol operation.

---

### Recommendation

Track the depositor's `AccountId` and the exact deposit amount inside the `VerifiedAttestation` (or a parallel map). In `clean_invalid_attestations` (and any other eviction path), issue a `Promise::new(depositor).transfer(deposit)` before removing the entry, mirroring the refund pattern already used in the sign-request timeout path and the attestation-verifier design.

---

### Proof of Concept

1. Non-participant account `alice.near` calls `submit_participant_info` with an attestation whose `expiry_timestamp_seconds` is 60 seconds in the future. The contract charges her deposit `D = storage_byte_cost × bytes_stored` and stores the entry.
2. 60 seconds elapse; the attestation expires.
3. Any account (including a griefing adversary) calls `clean_invalid_attestations(max_scan: 100)`.
4. `TeeState::clean_invalid_attestations` identifies alice's entry as invalid and calls `stored_attestations.remove(&alice_tls_pk)`.
5. The NEAR runtime credits the freed storage back to the contract's balance.
6. No refund is issued to `alice.near`. Deposit `D` is permanently absorbed by the contract.

The same sequence occurs automatically for every node evicted by the post-resharing `clean_invalid_attestations` promise, requiring zero adversarial action beyond the normal resharing flow.

### Citations

**File:** crates/contract/src/lib.rs (L823-824)
```rust
        let attestation_storage_must_be_paid_by_caller =
            is_new_attestation || caller_is_not_participant;
```

**File:** crates/contract/src/lib.rs (L826-848)
```rust
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

            // Refund the difference if the proposer attached more than required
            if let Some(diff) = attached.checked_sub(cost)
                && diff > NearToken::from_yoctonear(0)
            {
                Promise::new(account_id).transfer(diff).detach();
            }
```

**File:** crates/contract/src/tee/tee_state.rs (L430-432)
```rust
        for tls_pk in invalid_tls_keys {
            self.stored_attestations.remove(&tls_pk);
        }
```

**File:** crates/contract/tests/snapshots/abi__abi_has_not_changed.snap (L417-440)
```text
        "name": "clean_invalid_attestations",
        "doc": " Prunes up to `max_scan` stored attestations that fail re-verification (expired or\n referencing stale whitelists). Returns the number of entries removed. Callable by\n anyone while the protocol is in `Running`.",
        "kind": "call",
        "params": {
          "serialization_type": "json",
          "args": [
            {
              "name": "max_scan",
              "type_schema": {
                "type": "integer",
                "format": "uint32",
                "minimum": 0.0
              }
            }
          ]
        },
        "result": {
          "serialization_type": "json",
          "type_schema": {
            "type": "integer",
            "format": "uint32",
            "minimum": 0.0
          }
        }
```

**File:** crates/contract/tests/sandbox/tee_cleanup_after_resharing.rs (L166-254)
```rust
#[tokio::test]
async fn reshare__should_evict_expired_attestations_via_post_reshare_sweep() -> Result<()> {
    const ATTESTATION_EXPIRY_SECONDS: u64 = 5;
    const BLOCKS_TO_FAST_FORWARD: u64 = 100;

    let SandboxTestSetup {
        worker,
        contract,
        mpc_signer_accounts,
        ..
    } = SandboxTestSetup::builder()
        .with_protocols(&[Protocol::CaitSith])
        .build()
        .await;

    let initial_participants = assert_running_return_participants(&contract).await?;
    let threshold = assert_running_return_threshold(&contract).await;
    let initial_participants_count = initial_participants.participants.len();

    // Insert an attestation from an outsider whose expiry is a few seconds away.
    let (stale_accounts, _) = gen_accounts(&worker, 1).await;
    let stale_account = &stale_accounts[0];
    let stale_tls_key: dtos::Ed25519PublicKey = p2p_tls_key().into();
    let block_info = worker.view_block().await?;
    let expiry_timestamp_seconds =
        block_info.timestamp() / 1_000_000_000 + ATTESTATION_EXPIRY_SECONDS;
    let expiring_attestation = Attestation::Mock(MockAttestation::WithConstraints {
        mpc_docker_image_hash: None,
        launcher_docker_compose_hash: None,
        expiry_timestamp_seconds: Some(expiry_timestamp_seconds),
        expected_measurements: None,
    });
    let submit_result = submit_participant_info(
        stale_account,
        &contract,
        &expiring_attestation,
        &stale_tls_key,
    )
    .await?;
    assert!(submit_result.is_success());
    assert_eq!(
        get_tee_accounts(&contract).await.unwrap().len(),
        initial_participants_count + 1
    );

    // Advance past the expiry before triggering the reshare so that the post-reshare
    // sweep sees the outsider entry as invalid.
    worker.fast_forward(BLOCKS_TO_FAST_FORWARD).await?;

    // Reshare to the threshold subset; this triggers the post-reshare cleanup promise.
    let mut new_participants = Participants::new();
    for (account_id, participant_id, participant_info) in initial_participants
        .participants
        .iter()
        .take(threshold.0 as usize)
    {
        new_participants
            .insert_with_id(
                account_id.clone(),
                mpc_contract::primitives::participants::ParticipantInfo {
                    url: participant_info.url.clone(),
                    tls_public_key: participant_info.tls_public_key.clone(),
                },
                mpc_contract::primitives::participants::ParticipantId((*participant_id).into()),
            )
            .expect("Failed to insert participant");
    }
    let new_threshold_parameters = ThresholdParameters::new(
        new_participants,
        mpc_contract::primitives::thresholds::Threshold::new(threshold.0),
    )
    .unwrap();
    do_resharing(
        &mpc_signer_accounts[..threshold.0 as usize],
        &contract,
        new_threshold_parameters,
        dtos::EpochId(6),
    )
    .await?;

    // The expired outsider attestation is evicted by the `clean_invalid_attestations`
    // promise spawned from `vote_reshared`.
    let tee_accounts_after_reshare = get_tee_accounts(&contract).await.unwrap();
    assert!(
        !tee_accounts_after_reshare
            .iter()
            .any(|uid| uid.account_id == stale_account.id().clone()),
        "expired outsider attestation should have been evicted by the post-reshare sweep",
    );
```
