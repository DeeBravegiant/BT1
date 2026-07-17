Now I have all the information needed to make a determination. Let me trace the full attack path precisely.

The full attack path is now confirmed. Here is my determination:

---

### Title
Unauthenticated Peer-Reported Height Triggers Irreversible Data-Directory Wipe via `StateSync::run` Stale-Hash Check — (`chain/client/src/sync/state/mod.rs`)

### Summary

An unauthenticated peer can force any node that is mid-state-sync to immediately shut down and wipe its entire data directory by advertising an inflated `highest_block_height`. The stale-sync-hash guard in `StateSync::run` compares the peer-supplied height against the sync hash's block height with no chain-side validation, and the resulting `EpochSyncDataReset` shutdown unconditionally calls `std::fs::remove_dir_all` on the hot store path at next startup.

### Finding Description

**Entrypoint — peer-advertised height reaches the stale-hash check unvalidated:**

`syncing_info()` in `client_actor.rs` builds `eligible_peers` by filtering `network_info.highest_height_peers` only for hashes already in the local `invalid_blocks` set:

```rust
// chain/client/src/client_actor.rs:1684-1700
let eligible_peers: Vec<_> = self
    .network_info
    .highest_height_peers
    .iter()
    .filter(|p| !self.client.chain.is_block_invalid(&p.highest_block_hash))
    .collect();
// ...
let highest_height = peer_info.highest_block_height.min(shutdown_height);
``` [1](#0-0) 

A fabricated `highest_block_hash` that has never been seen by the node is not in `invalid_blocks`, so it passes the filter. `highest_height_peers()` in `peer_manager_actor.rs` returns peers within `highest_peer_horizon` of the **maximum** reported height:

```rust
// chain/network/src/peer_manager/peer_manager_actor.rs:488-499
let max_height = infos.iter().map(|i| i.highest_block_height).max()...;
infos.into_iter().filter(|i| {
    i.highest_block_height.saturating_add(self.state.config.highest_peer_horizon) >= max_height
}).collect()
``` [2](#0-1) 

A single malicious peer reporting a very high height becomes the sole entry in `highest_height_peers`, so it is always selected by `syncing_info()`.

**The unguarded stale-hash check:**

`StateSync::run` fires `StaleSyncHash` purely on the peer-supplied value:

```rust
// chain/client/src/sync/state/mod.rs:283-291
if highest_height > block_header.height() + chain.epoch_length + STALE_SYNC_HASH_THRESHOLD {
    return Ok(StateSyncResult::StaleSyncHash);
}
``` [3](#0-2) 

`STALE_SYNC_HASH_THRESHOLD` is 100 in production. The attacker only needs to advertise `highest_block_height > sync_hash_height + epoch_length + 100`.

**Propagation to irreversible shutdown:**

`StaleSyncHash` propagates without any additional check:

```rust
// chain/client/src/sync/handler.rs:150-152
StateSyncResult::StaleSyncHash => {
    return Ok(Some(SyncHandlerRequest::EpochSyncDataReset));
}
``` [4](#0-3) 

```rust
// chain/client/src/client_actor.rs:1877-1881
SyncHandlerRequest::EpochSyncDataReset => {
    if let Some(tx) = self.shutdown_signal.take() {
        let _ = tx.send(ShutdownReason::EpochSyncDataReset);
    }
}
``` [5](#0-4) 

**Data directory wipe on next startup:**

`neard/src/cli.rs` writes a marker file and re-execs the process:

```rust
// neard/src/cli.rs:632-651
let needs_restart =
    matches!(&sig, ShutdownSignal::ClientShutdown(ShutdownReason::EpochSyncDataReset));
if needs_restart {
    write_epoch_sync_data_reset_marker(&hot_store_path);
}
// ...
if needs_restart { exec_restart(); }
``` [6](#0-5) 

On the re-exec, `check_epoch_sync_data_reset_marker` calls `std::fs::remove_dir_all` on the entire hot store path:

```rust
// neard/src/cli.rs:668-671
std::fs::remove_dir_all(hot_store_path)
    .expect("failed to delete data directory for epoch sync reset");
``` [7](#0-6) 

### Impact Explanation

Any node in `SyncStatus::StateSync` — including validator nodes mid-catchup — can be forced to:
1. Immediately shut down
2. Wipe its entire hot data directory (all downloaded state parts, headers, blocks, DB)
3. Restart from genesis, requiring a full re-sync

This is permanent, irreversible data loss triggered by a single unauthenticated peer connection. For a validator node, this causes missed block production and potential stake slashing. The attack can be repeated on every subsequent sync attempt, permanently preventing the node from catching up.

### Likelihood Explanation

The attack requires only:
1. Connecting to the target node (standard P2P, no authentication)
2. Advertising `highest_block_height > sync_hash_height + epoch_length + 100` with any unknown hash

The target node must be in `SyncStatus::StateSync`. This state is reached by any node doing far-horizon sync (a common operational scenario). The attack is deterministic and repeatable with a single peer connection.

### Recommendation

The stale-hash check must be grounded in locally-verified chain data, not peer-reported height. Specifically:

- Replace the peer-supplied `highest_height` in the stale-hash check with the node's own locally-verified `header_head.height` (which is validated via cryptographic header chain verification during header sync).
- Alternatively, require that the peer's reported height be corroborated by a locally-known block header before it can trigger `StaleSyncHash`.
- At minimum, require multiple independent peers to agree on a height exceeding the threshold before triggering the irreversible data-reset path.

### Proof of Concept

In a test-loop test with `test_features` (to lower `STALE_SYNC_HASH_THRESHOLD` to 5):

1. Run validators to far-horizon height.
2. Add a fresh node; block its state part downloads so it stays in `SyncStatus::StateSync`.
3. Record `sync_hash_height` from the node's `StateSyncStatus`.
4. Inject a single peer advertising `highest_block_height = sync_hash_height + epoch_length + 6` with a random unknown hash.
5. Run one `run_sync_step` tick.
6. Assert the node is denylisted (i.e., `EpochSyncDataReset` fired) — **without the chain having actually advanced past the threshold**.

This is structurally identical to the existing `test_far_horizon_stale_sync_hash_detection` test [8](#0-7) 

except that test advances the *real* chain past the threshold. The exploit substitutes a single lying peer for real chain advancement, and the check fires identically because no chain-side validation exists at the decision point.

### Citations

**File:** chain/client/src/client_actor.rs (L1684-1700)
```rust
        let eligible_peers: Vec<_> = self
            .network_info
            .highest_height_peers
            .iter()
            .filter(|p| !self.client.chain.is_block_invalid(&p.highest_block_hash))
            .collect();
        metrics::PEERS_WITH_INVALID_HASH
            .set(self.network_info.highest_height_peers.len() as i64 - eligible_peers.len() as i64);
        let peer_info = if let Some(peer_info) = eligible_peers.choose(&mut thread_rng()) {
            peer_info
        } else {
            return Ok(SyncRequirement::NoPeers);
        };

        let peer_id = peer_info.peer_info.id.clone();
        let shutdown_height = self.client.config.expected_shutdown.get().unwrap_or(u64::MAX);
        let highest_height = peer_info.highest_block_height.min(shutdown_height);
```

**File:** chain/client/src/client_actor.rs (L1877-1881)
```rust
            SyncHandlerRequest::EpochSyncDataReset => {
                if let Some(tx) = self.shutdown_signal.take() {
                    let _ = tx.send(ShutdownReason::EpochSyncDataReset);
                }
            }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L488-499)
```rust
        let max_height = match infos.iter().map(|i| i.highest_block_height).max() {
            Some(height) => height,
            None => return vec![],
        };
        // Find all peers whose height is within `highest_peer_horizon` from max height peer(s).
        infos
            .into_iter()
            .filter(|i| {
                i.highest_block_height.saturating_add(self.state.config.highest_peer_horizon)
                    >= max_height
            })
            .collect()
```

**File:** chain/client/src/sync/state/mod.rs (L283-291)
```rust
        if highest_height > block_header.height() + chain.epoch_length + STALE_SYNC_HASH_THRESHOLD {
            tracing::warn!(
                target: "sync",
                ?block_header,
                highest_height,
                "stale sync hash detected, triggering data reset"
            );
            return Ok(StateSyncResult::StaleSyncHash);
        }
```

**File:** chain/client/src/sync/handler.rs (L150-152)
```rust
                    StateSyncResult::StaleSyncHash => {
                        return Ok(Some(SyncHandlerRequest::EpochSyncDataReset));
                    }
```

**File:** neard/src/cli.rs (L632-651)
```rust
            let needs_restart =
                matches!(&sig, ShutdownSignal::ClientShutdown(ShutdownReason::EpochSyncDataReset));
            if needs_restart {
                write_epoch_sync_data_reset_marker(&hot_store_path);
            }

            tracing::warn!(target: "neard", ?sig, "stopping, this may take a few minutes");
            if let Some(handle) = cold_store_loop_handle {
                handle.store(false, std::sync::atomic::Ordering::Relaxed);
            }
            resharding_handle.0.stop();
            near_async::shutdown_all_actors();
            // Disable the subscriber to properly shutdown the tracer.
            near_o11y::reload(Some("error"), None, Some("off"), None).unwrap();

            // Re-exec after shutting down actors. We skip RocksDB shutdown since
            // the data directory will be wiped on the next startup anyway.
            if needs_restart {
                exec_restart();
            }
```

**File:** neard/src/cli.rs (L668-671)
```rust
        tracing::info!(target: "neard", ?hot_store_path, "epoch sync data reset marker found, deleting data directory");
        std::fs::remove_dir_all(hot_store_path)
            .expect("failed to delete data directory for epoch sync reset");
    }
```

**File:** test-loop-tests/src/tests/sync/far_horizon.rs (L777-861)
```rust
fn test_far_horizon_stale_sync_hash_detection() {
    use crate::setup::peer_manager_actor::HandlerResult;
    use near_client::sync::state::STALE_SYNC_HASH_THRESHOLD;
    use near_network::types::{NetworkRequests, NetworkResponses};

    init_test_logger();

    let epoch_length = 10;
    let accounts = make_accounts(100);
    let mut env = TestLoopBuilder::new()
        .validators(4, 0)
        .num_shards(4)
        .epoch_length(epoch_length)
        .add_user_accounts(&accounts, Balance::from_near(1_000_000))
        .build();

    execute_money_transfers(&mut env.test_loop, &env.node_datas, &accounts).unwrap();
    env.node_runner(0).run_until_head_height(far_horizon_height(epoch_length));

    let new_account = create_account_id("new_node");
    let node_state = env
        .node_state_builder()
        .account_id(&new_account)
        .config_modifier(|config| {
            config.tracked_shards_config = TrackedShardsConfig::AllShards;
            config.epoch_sync.epoch_sync_horizon_num_epochs = TEST_EPOCH_SYNC_HORIZON;
        })
        .build();
    env.add_node("new_node", node_state);
    let new_node_idx = env.node_datas.len() - 1;

    // Drop state part requests so state sync never completes.
    env.node_datas[new_node_idx].register_override_handler(
        &mut env.test_loop.data,
        Box::new(|request| match &request {
            NetworkRequests::StateRequestPart { .. } => {
                HandlerResult::Handled(NetworkResponses::NoResponse)
            }
            _ => HandlerResult::Unhandled(request),
        }),
    );

    // Run until new node enters StateSync and record its sync hash.
    let mut node_sync_hash = None;
    let new_node_handle = env.node_datas[new_node_idx].client_sender.actor_handle();
    env.test_loop.run_until(
        |data| {
            let status = &data.get(&new_node_handle).client.sync_handler.sync_status;
            if let SyncStatus::StateSync(s) = status {
                node_sync_hash = Some(s.sync_hash);
                true
            } else {
                false
            }
        },
        Duration::seconds(20),
    );
    let node_sync_hash = node_sync_hash.unwrap();

    // Advance the chain by one epoch so the validators get a new sync hash.
    env.node_runner(0).run_for_number_of_blocks(epoch_length as usize);

    // validator sync hash should have changed to a new epoch
    let validator_sync_hash = env.node(0).client().chain.find_sync_hash().unwrap().unwrap();
    assert_ne!(node_sync_hash, validator_sync_hash);

    // Advance the chain past the detection threshold. The stale sync hash
    // check fires on each run_sync_step when in StateSync, and once
    // highest_height > epoch_start + epoch_length + STALE_SYNC_HASH_THRESHOLD
    // the node triggers EpochSyncDataReset.
    env.node_runner(0).run_for_number_of_blocks(epoch_length as usize);

    assert!(env.test_loop.is_denylisted("new_node"));

    // Verify the chain advanced past the detection threshold. The syncing
    // node sees validator heights via highest_height_peers.
    let sync_hash_height =
        env.node(0).client().chain.get_block_header(&node_sync_hash).unwrap().height();
    let validator_height = env.node(0).head().height;
    let expected_threshold = sync_hash_height + epoch_length + STALE_SYNC_HASH_THRESHOLD;
    assert!(
        validator_height > expected_threshold,
        "validator height {validator_height} should exceed threshold {expected_threshold}",
    );
}
```
