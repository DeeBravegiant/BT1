### Title
Presignature Participant Set Chosen for Liveness, Not Foreign-Chain Coverage, Causes Stale-State Signing Failure — (File: `docs/design/calculating-supported-foreign-chains.md`, `crates/node/src/providers/ecdsa/presign.rs`, `crates/node/src/providers/robust_ecdsa/presign.rs`)

---

### Summary

The NEAR MPC system pre-generates presignatures whose participant sets are selected based on **P2P liveness** at generation time. When a `verify_foreign_transaction` signing request later arrives, the signing set is **inherited directly from the presignature** — participants chosen for liveness, not for foreign-chain RPC coverage. If any participant in the presignature set no longer covers the requested chain at signing time (e.g., their RPC provider is down, or their coverage registration has changed), that participant produces no signature share, causing the signing to stall and the request to time out. This is the direct analog of H-4: a strategy that was optimal at one point in time (liveness-based participant selection) is blindly reused at a later point when conditions have changed (coverage-based requirements), producing a sub-optimal or failed outcome.

---

### Finding Description

**Root cause — presignature generation selects for liveness, not coverage:**

In `crates/node/src/providers/ecdsa/presign.rs`, the background presignature generation loop calls `select_random_active_participants_including_me` to pick the participant set for each presignature. "Active" here means P2P-connected and within a few blocks of the indexer height — it has no knowledge of which foreign chains each participant covers. [1](#0-0) 

The same pattern appears in the Robust ECDSA path: [2](#0-1) 

**Root cause — signing inherits the stale participant set:**

At signing time, the leader calls `presignature_store.take_owned()` and broadcasts the presignature ID to its borrowers. The signing set is the set of borrowers recorded in the presignature at generation time — there is no re-check of whether those participants currently cover the requested foreign chain. [3](#0-2) 

**The gap is explicitly acknowledged in the design documentation:**

> "Foreign-tx signing must elect participants that **cover** the requested chain (report ≥ `rpc_quorum(C)` providers for `C`), not merely online ones — a non-covering participant produces no share and can stall the request. **Implementation requirement, not current behavior: today the signing set is inherited from a presignature, whose participants were chosen for liveness, not chain coverage.**" [4](#0-3) 

**The liveness eviction mechanism does not close the gap:**

The asset store discards presignatures whose participants go P2P-offline. However, a participant can be P2P-online (and thus keep the presignature "live") while simultaneously failing to cover a specific foreign chain (e.g., their RPC provider for that chain is down, or they registered coverage for the chain but their local config has since broken). The eviction logic has no concept of per-chain coverage: [5](#0-4) 

**The contract's availability check does not prevent the failure:**

The contract's `get_available_foreign_chains()` counts participants that have registered coverage for a chain. If a participant registered coverage but their RPC is now broken, the contract still counts them as covering the chain. The contract accepts the `verify_foreign_transaction` request, but the signing fails at the node level. [6](#0-5) 

---

### Impact Explanation

This breaks the **request-lifecycle invariant**: the contract accepts a `verify_foreign_transaction` request (because the chain appears "available"), but the signing cannot complete because the presignature's participant set — chosen for liveness at generation time — does not satisfy the coverage requirement at signing time. The consumed presignature is wasted, and the user's request times out with no response. In a sustained scenario (e.g., a chain's RPC providers are intermittently degraded across multiple nodes), a large fraction of `ForeignTx` presignatures become unusable for that chain, degrading or halting the foreign-chain bridge flow without any on-chain signal to the user.

This maps to the **Medium** allowed impact: *"request-lifecycle... manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."* The condition arises from a structural mismatch in the protocol design, not from operator error — even a correctly configured node can have transient RPC failures that make it non-covering for a chain while remaining P2P-online.

---

### Likelihood Explanation

The condition is realistic and has a non-trivial probability in production:

1. Presignatures are generated continuously in the background with participants chosen at that moment for liveness.
2. Foreign-chain RPC providers are external services subject to outages, rate limits, and configuration drift.
3. A participant can be P2P-online (keeping the presignature "live" and preventing eviction) while its RPC for a specific chain is broken.
4. The gap between presignature generation and consumption can be hours (the buffer is sized for `desired_presignatures_to_buffer` presignatures, which at mainnet scale represents significant wall-clock time).
5. Any unprivileged user submitting a `verify_foreign_transaction` request triggers the failure path when the condition is present.

---

### Recommendation

1. **At presignature generation time for `ForeignTx` domains:** select participants based on both P2P liveness **and** current foreign-chain coverage registration, so that only participants covering the relevant chain(s) are included in the presignature set.
2. **At signing time:** before consuming a presignature for a `verify_foreign_transaction` request, verify that all borrowers in the presignature set currently cover the requested chain; if not, discard the presignature and select another.
3. **Alternatively:** maintain a separate presignature pool per foreign chain, generated only with participants that cover that chain, analogous to how presignatures are already per-domain.

This mirrors the H-4 recommendation: allow the restoration/signing step to adapt to current conditions rather than blindly reusing the state captured at an earlier point in time.

---

### Proof of Concept

1. **T0 — Presignature generation:** Participants `{A, B, C}` are all P2P-online. A `ForeignTx` presignature `P` is generated with borrowers `{A, B, C}`. The participant set is recorded in `P`.

2. **T0 → T1 — Condition change:** Participant `B`'s RPC provider for chain `X` goes down. `B` remains P2P-online (so `P` is not evicted by the liveness check). `B`'s on-chain registration still lists chain `X` (it hasn't re-registered), so the contract still counts `B` as covering `X`.

3. **T1 — Request arrives:** An unprivileged user calls `verify_foreign_transaction` for chain `X`. The contract checks `get_available_foreign_chains()`, sees `X` is available (A, B, C all registered), and accepts the request.

4. **T1 — Signing attempt:** The leader takes presignature `P` (participants `{A, B, C}`), broadcasts the presignature ID and the request to `{A, B, C}`. `B` attempts to verify the foreign transaction via its RPC but fails (provider down). `B` produces no signature share.

5. **Outcome:** The signing protocol stalls waiting for `B`'s share. The presignature `P` is consumed and lost. The request times out. The user receives no response. The request-lifecycle invariant — that an accepted request can be fulfilled — is violated. [4](#0-3) [7](#0-6) [5](#0-4)

### Citations

**File:** crates/node/src/providers/ecdsa/presign.rs (L29-53)
```rust
#[derive(derive_more::Deref)]
pub struct PresignatureStorage(DistributedAssetStorage<PresignOutputWithParticipants>);

impl PresignatureStorage {
    pub fn new(
        clock: Clock,
        db: Arc<SecretDB>,
        my_participant_id: ParticipantId,
        alive_participant_ids_query: Arc<dyn Fn() -> Vec<ParticipantId> + Send + Sync>,
        domain_id: DomainId,
    ) -> anyhow::Result<Self> {
        Ok(Self(DistributedAssetStorage::<
            PresignOutputWithParticipants,
        >::new(
            clock,
            db,
            crate::db::DBCol::Presignature,
            domain_id.0.to_be_bytes().to_vec(),
            my_participant_id,
            |participants, presignature| {
                presignature.is_subset_of_active_participants(participants)
            },
            alive_participant_ids_query,
        )?))
    }
```

**File:** crates/node/src/providers/ecdsa/presign.rs (L67-76)
```rust
    pub(super) async fn run_background_presignature_generation(
        client: Arc<MeshNetworkClient>,
        threshold: TSReconstructionThreshold,
        config: Arc<PresignatureConfig>,
        triple_store: Arc<TripleStorage>,
        domain_id: DomainId,
        presignature_store: Arc<PresignatureStorage>,
        keygen_out: KeygenOutput,
    ) -> ! {
        let in_flight_generations = InFlightGenerationTracker::new();
```

**File:** crates/node/src/providers/robust_ecdsa/presign.rs (L113-126)
```rust
            let participants = match client
                .select_random_active_participants_including_me(num_signers, &running_participants)
            {
                Ok(participants) => participants,
                Err(e) => {
                    tracing::warn!(
                        "Can't choose active participants for a robust-ecdsa presignature: {}. Sleeping.",
                        e
                    );
                    // that should not happen often, so sleeping here is okay
                    tokio::time::sleep(Duration::from_millis(100)).await;
                    continue;
                }
            };
```

**File:** docs/asset-generation.md (L199-209)
```markdown
**Note on orphaned unowned assets:** When an owned asset is discarded, only the
local copy is deleted. There is no mechanism to notify borrower nodes to delete
their unowned copies of the same asset. Since `take_unowned(id)` is never called
for a discarded asset, those copies remain in borrowers' `RocksDB` indefinitely.
`clean_db()` cannot find them either — it only iterates keys namespaced by
`my_participant_id`, while unowned assets are keyed by the original owner's
participant ID. This also means the same-epoch TLS-key-change cleanup
(`KeepOnly` branch in `delete_stale_triples_and_presignatures()`) misses
unowned assets, since it delegates to `clean_db()`. The only event that
clears them is a full asset wipe on epoch change (resharing). In normal
operation this constitutes a slow disk storage leak.
```

**File:** docs/asset-generation.md (L244-252)
```markdown

1. **Leader** calls `presignature_store.take_owned()` for the relevant
   domain, consuming one presignature.
2. Leader opens a network channel with the presignature's borrowers
   and broadcasts the presignature ID along with the signature request.
3. **Followers** call `presignature_store.take_unowned(id)` to retrieve
   their share, then run the protocol.
4. The leader does **not** wait for all followers to confirm success
   (`leader_waits_for_success` returns `false`).
```

**File:** docs/design/calculating-supported-foreign-chains.md (L56-67)
```markdown
## Verification behavior

Each node fans the query out to its whitelisted providers for `C` and accepts a
result only when ≥ `rpc_quorum(C)` return the same response. If fewer agree, the node
errors out and produces no signature share.

**This sub-quorum outcome must be terminal — the leader must not re-attempt the
request.** Implementation requirement, not current behavior: the generic queue
retries every request, so the foreign-tx path must special-case a sub-quorum
result as non-retryable. (Open: whether a sub-quorum from purely *transient*
failures — timeouts, finality not reached — should still retry, vs. only genuine
disagreement being terminal. Tracked in [#3477](https://github.com/near/mpc/issues/3477).)
```

**File:** docs/design/calculating-supported-foreign-chains.md (L69-76)
```markdown
## Participant election

Foreign-tx signing must elect participants that **cover** the requested chain
(report ≥ `rpc_quorum(C)` providers for `C`), not merely online ones — a
non-covering participant produces no share and can stall the request.
Implementation requirement, not current behavior: today the signing set is inherited
from a presignature, whose
participants were chosen for liveness, not chain coverage.
```
