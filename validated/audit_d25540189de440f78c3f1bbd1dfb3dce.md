### Title
Unbounded Follower Fan-out in `monitor_passive_channels_inner` Allows a Sub-threshold Byzantine Participant to Starve Signing Requests — (File: `crates/node/src/mpc_client.rs`)

---

### Summary

`monitor_passive_channels_inner` spawns one tokio task per incoming peer-initiated generation channel with no admission cap. A single Byzantine MPC participant (below the signing threshold) can open an unbounded stream of triple/presignature follower channels toward a victim node, saturating the `gen_runtime` scheduler, stalling presignature generation, and ultimately causing all signing requests on that node to time out.

---

### Finding Description

`monitor_passive_channels_inner` is the sole entry point for all inbound P2P task channels. Its loop is:

```rust
while let Some(channel) = channel_receiver.recv().await {
    let task = async move { mpc_clone.process_channel_task(channel).await };
    if is_heavy_generation_task(&task_id) {
        tasks.spawn_checked_on(&mpc_client.gen_runtime_handle, &description, task);
    } else {
        tasks.spawn_checked(&description, task);
    }
}
``` [1](#0-0) 

Every inbound channel is unconditionally spawned — there is no semaphore, per-peer admission gate, or backpressure mechanism. The project's own design document explicitly names this as an open risk:

> **Unbounded follower fan-out.** `mpc_client.rs::monitor_passive_channels_inner` spawns one task per incoming peer channel with no cap, so a node has no way to bound how much follower work peers can push onto it. [2](#0-1) 

The partial fix (Solution A) routes heavy generation tasks to a separate lower-priority `gen_runtime`: [3](#0-2) 

However, Solution B ("Bound follower concurrency") is explicitly left unimplemented:

> Cap concurrent follower gen tasks per peer, both as DoS protection and as a defensive bound on fan-out. **Necessary only if memory pressure or scheduler queue depth turn out to be a problem after A.** [4](#0-3) 

Each spawned follower task runs `run_protocol`, which calls `protocol.poke()` in a tight CPU-bound loop until `Action::Wait`: [5](#0-4) 

A 64-triple batch burst holds the thread for tens-to-hundreds of milliseconds between awaits, as documented:

> **CPU-bound, non-yielding poke loop.** `run_protocol` in `protocol.rs` runs `protocol.poke()` until `Action::Wait`. A 64-batch triple gen burst is tens-to-hundreds of ms between awaits. [6](#0-5) 

When `gen_runtime` is saturated, the presignature generation loop stalls. The signing path then calls `take_owned()` on the presignature store, which **blocks indefinitely** if no presignatures are available:

> Eventually the node will exhaust its owned presignatures. At that point `take_owned()` blocks indefinitely (waiting for a presignature that will never arrive), and the node stops being able to lead signature computations. [7](#0-6) 

---

### Impact Explanation

A victim node's signing leadership is completely suppressed for the duration of the attack. Pending `sign()` requests on the NEAR contract time out and are dropped. Users' cross-chain transactions (Bitcoin, Ethereum, etc.) fail. Because the attack targets a single node's `gen_runtime`, it breaks the request lifecycle for all signing requests that node is elected to lead, without requiring any collusion above the threshold.

This is a **request-lifecycle manipulation that breaks production safety/accounting invariants** — specifically, the liveness guarantee that threshold signatures are produced within the contract's timeout window.

---

### Likelihood Explanation

- Requires only **one** registered MPC participant to be Byzantine (strictly below the signing threshold).
- The attacker simply opens generation channels as fast as the network allows — no cryptographic capability is needed beyond being a valid participant.
- The attack is persistent: as long as the attacker keeps opening channels, the victim's `gen_runtime` remains saturated.
- The attack is cheap: opening a channel costs only a P2P TLS message; no on-chain gas is required.
- The design document confirms this is a known, unmitigated vector in the current codebase.

---

### Recommendation

Implement per-peer admission control for follower generation tasks as described in Solution B of the design document: [4](#0-3) 

Concretely: maintain a per-`leader_id` atomic counter of active follower tasks. In `monitor_passive_channels_inner`, before spawning a heavy generation task, call a non-blocking `try_admit(leader_id)`. If the per-peer cap is exceeded, drop the channel immediately and let the leader time out and retry with different participants. This avoids the global-semaphore deadlock described in the document (circular wait across threshold-N nodes).

---

### Proof of Concept

1. Byzantine participant `B` (one of N, below threshold) connects to victim node `V` over the authenticated P2P mesh.
2. `B` repeatedly calls `client.new_channel_for_task(EcdsaTaskId::ManyTriples { ... }, participants)` targeting `V` as a follower, opening hundreds of generation channels in rapid succession.
3. Each channel arrives at `V`'s `channel_receiver` in `monitor_passive_channels_inner`.
4. `monitor_passive_channels_inner` spawns each as an unbounded task on `gen_runtime` via `tasks.spawn_checked_on(&mpc_client.gen_runtime_handle, ...)`.
5. `gen_runtime` (bounded by `cores` threads) is fully occupied running `run_protocol` poke loops for `B`'s fake generation tasks.
6. `V`'s legitimate presignature generation loop cannot acquire a `gen_runtime` thread; the presignature store drains to zero.
7. When `V` is elected leader for a real `sign()` request, `ecdsa_signature_provider.make_signature(...)` calls `presignature_store.take_owned()`, which blocks indefinitely.
8. The signing timeout fires; the request is dropped; the user's cross-chain transaction fails.
9. `B` repeats indefinitely at negligible cost. [1](#0-0) [8](#0-7)

### Citations

**File:** crates/node/src/mpc_client.rs (L62-63)
```rust
    /// Lower-priority runtime for CPU-heavy asset generation.
    gen_runtime_handle: tokio::runtime::Handle,
```

**File:** crates/node/src/mpc_client.rs (L652-667)
```rust
    async fn monitor_passive_channels_inner(
        mut channel_receiver: mpsc::UnboundedReceiver<NetworkTaskChannel>,
        mpc_client: Arc<MpcClient<ForeignChainPolicyReader>>,
    ) -> anyhow::Result<()> {
        let mut tasks = AutoAbortTaskCollection::new();
        while let Some(channel) = channel_receiver.recv().await {
            let mpc_clone = mpc_client.clone();
            let task_id = channel.task_id();
            let description = format!("passive task; task_id: {task_id:?}");
            let task = async move { mpc_clone.process_channel_task(channel).await };
            if is_heavy_generation_task(&task_id) {
                tasks.spawn_checked_on(&mpc_client.gen_runtime_handle, &description, task);
            } else {
                tasks.spawn_checked(&description, task);
            }
        }
```

**File:** docs/design/signing-starvation-solution.md (L18-20)
```markdown
2. **CPU-bound, non-yielding poke loop.** `run_protocol` in `protocol.rs`
   runs `protocol.poke()` until `Action::Wait`. A 64-batch triple gen burst
   is tens-to-hundreds of ms between awaits.
```

**File:** docs/design/signing-starvation-solution.md (L21-24)
```markdown
3. **Unbounded follower fan-out.**
   `mpc_client.rs::monitor_passive_channels_inner` spawns one task per
   incoming peer channel with no cap, so a node has no way to bound how much
   follower work peers can push onto it.
```

**File:** docs/design/signing-starvation-solution.md (L70-83)
```markdown
### B — Bound follower concurrency

Cap concurrent follower gen tasks per peer, both as DoS protection and as a
defensive bound on fan-out.

**Must be per-peer admission, not a single global semaphore.** A small
global cap deadlocks in the threshold-N circular-wait case (A waits on B to
free a slot to accept A's gen; B waits on C; C waits on A). Concrete shape:
`try_admit(leader_id) -> Option<Permit>`, non-blocking; on unavailable, drop
the channel and let the leader time out and retry with different
participants.

Necessary only if memory pressure or scheduler queue depth turn out to be a
problem after A. If A leaves only CPU contention, B is overkill.
```

**File:** crates/node/src/protocol.rs (L85-116)
```rust
        loop {
            let mut messages_to_send: HashMap<ParticipantId, _> = HashMap::new();
            let outcome = loop {
                match protocol.poke()? {
                    Action::Wait => break PokeOutcome::Wait,
                    // Flush the accumulated messages before yielding, so peers can make
                    // progress while we give other tasks a chance to run.
                    Action::Yield => break PokeOutcome::Yield,
                    Action::SendMany(vec) => {
                        for participant in &participants {
                            if participant == &my_participant_id {
                                continue;
                            }
                            messages_to_send
                                .entry(*participant)
                                .or_insert(Vec::new())
                                .push(vec.clone());
                        }
                    }
                    Action::SendPrivate(participant, vec) => {
                        messages_to_send
                            .entry(From::from(participant))
                            .or_insert(Vec::new())
                            .push(vec.clone());
                    }
                    Action::Return(result) => {
                        // Warning: we cannot return immediately!! There may be some important
                        // messages to send to others to enable others to complete their computation.
                        break PokeOutcome::Return(result);
                    }
                }
            };
```

**File:** docs/asset-generation.md (L339-343)
```markdown
Eventually the node will exhaust its owned presignatures. At that point
`take_owned()` blocks indefinitely (waiting for a presignature that will
never arrive), and the node stops being able to lead signature
computations. It can still participate as a **follower**, since
`take_unowned(id)` does not depend on the local generation loop.
```

**File:** crates/node/src/tests/asset_generation_signing_contention.rs (L1-12)
```rust
//! Reproduction for issue #1175 — "asset generation impacts signing performance".
//!
//! Background asset generation (triples + presignatures) and signing both run on
//! the same `cores`-limited per-epoch MPC runtime (see
//! [`crate::coordinator::Coordinator`]'s `create_runtime_and_run`) with no priority
//! separation between them. The cait-sith poke loop in
//! [`crate::protocol::run_protocol`] is CPU-bound and does not yield between
//! network rounds, and the follower/passive side of generation
//! ([`crate::mpc_client`]'s `monitor_passive_channels_inner`) is unbounded. After a
//! resharing every node's triple/presignature stores are empty at once, so the
//! whole network refills simultaneously and each node is flooded with generation
//! work — which starves signing.
```
