### Title
`verify_foreign_transaction` gates on all-participant intersection instead of threshold-based availability, allowing a single Byzantine participant to block all foreign chain verification — (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` checks whether the requested foreign chain is in `get_supported_foreign_chains()`, which requires **every** active participant to have registered support for the chain. The system's own design documentation mandates that this gate should use `get_available_foreign_chains()`, which only requires ≥ `signing_threshold` participants to cover the chain. A single Byzantine participant strictly below the signing threshold can block all foreign chain verification — and therefore all bridge operations — by simply never calling `register_foreign_chain_support` or `register_foreign_chains_config`.

---

### Finding Description

In `verify_foreign_transaction`, the chain-availability gate reads:

```rust
let requested_chain = request.request.chain();
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(...)
}
``` [1](#0-0) 

`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's registered chains:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);
``` [2](#0-1) 

This is the legacy "all-participant intersection rule." The system's own design documentation explicitly states:

> `verify_foreign_transaction(C)` is **rejected unless `C` is available** … The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour of the two views above. [3](#0-2) 

The correct gate is `get_available_foreign_chains()`, which is computed by `update_available_chains_config_cache` and counts only active-participant TLS keys, applying the threshold:

```rust
.filter_map(|(chain, count)| (count >= threshold).then_some(chain))
``` [4](#0-3) 

The `recompute_available_foreign_chains` helper already derives the correct `active_tls_keys` and `threshold` from the current protocol state: [5](#0-4) 

The two functions answer fundamentally different questions:

| Function | Condition | Reference set |
|---|---|---|
| `get_supported_foreign_chains()` | ALL participants registered | Every active participant |
| `get_available_foreign_chains()` | ≥ threshold participants registered | Threshold-many active participants |

`verify_foreign_transaction` uses the first (wrong) function, mirroring the original bug's pattern of checking against the wrong reference set.

---

### Impact Explanation

A single Byzantine participant strictly below the signing threshold can block **all** foreign chain transaction verification by simply never calling `register_foreign_chain_support` or `register_foreign_chains_config`. Because `get_supported_foreign_chains()` requires unanimous registration, one absent participant collapses the supported-chain set to empty. Every subsequent `verify_foreign_transaction` call panics with `ForeignChainNotSupported`, regardless of how many other participants have registered.

The `verify_foreign_transaction` endpoint is the on-chain entry point for bridge operations (e.g., the Omnibridge inbound flow described in the design docs). Users who have already committed funds on a foreign chain and submitted a verification request cannot get those transactions processed. If the Byzantine participant refuses to register indefinitely, bridge funds remain frozen until governance completes a resharing to remove that participant — a multi-step, time-consuming process.

This maps to the **Medium** allowed impact: request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants (the invariant being: a chain that ≥ threshold participants cover must be serviceable).

---

### Likelihood Explanation

The attack requires no special privileges, no key material, no collusion, and no on-chain transaction beyond simply *not* calling a registration method. Any single participant who wishes to disrupt bridge operations — a competitor, a compromised node operator, or a participant who has been kicked out of the proposed new set and wants to cause damage before resharing completes — can execute this by doing nothing. The attack is persistent until governance intervenes.

---

### Recommendation

Replace `self.get_supported_foreign_chains()` with `self.get_available_foreign_chains()` in `verify_foreign_transaction`:

```rust
// Before (wrong reference set — all-participant intersection):
let supported_chains = self.get_supported_foreign_chains();

// After (correct reference set — threshold-based availability):
let available_chains = self.get_available_foreign_chains();
if !available_chains.contains(&requested_chain) { ... }
```

This aligns the gate with the system's own design specification and ensures that up to `n − signing_threshold` non-registering participants cannot block foreign chain verification.

---

### Proof of Concept

1. Deploy the MPC contract with N=4 participants and signing threshold T=3.
2. Have participants 0, 1, 2 call `register_foreign_chains_config` registering Bitcoin.
3. Participant 3 (Byzantine, below threshold) never registers any chain.
4. Call `verify_foreign_transaction` with a Bitcoin request.
5. **Observed**: the call panics with `ForeignChainNotSupported` because `get_supported_foreign_chains()` returns empty — participant 3 has not registered, so the strict intersection is empty.
6. **Expected** (with the correct gate): `get_available_foreign_chains()` would return Bitcoin, because 3 ≥ threshold(3) participants cover it, and the call would proceed.

The existing unit test `get_available_foreign_chains__should_include_chain_when_at_least_threshold_participants_cover_it` already proves that `get_available_foreign_chains()` correctly returns Bitcoin when exactly threshold participants register it — confirming the fix is straightforward. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L533-542)
```rust
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
```

**File:** crates/contract/src/lib.rs (L1028-1055)
```rust
    fn recompute_available_foreign_chains(&mut self) {
        let Ok(params) = self.protocol_state.threshold_parameters() else {
            return;
        };
        // TODO(#3556): replace this with a per-scheme
        // `required_active_signers(protocol, reconstruction_threshold)`.
        let Some(threshold) = self.protocol_state.domain_registry().ok().and_then(|r| {
            r.domains()
                .iter()
                .filter(|d| d.purpose == DomainPurpose::ForeignTx)
                .map(|d| d.reconstruction_threshold.inner())
                .max()
        }) else {
            // No op if contract isn't in Running or Resharing state, or
            // there is no foreign tx domain registered.
            // Not panicking is intentional.
            log!("Skipping available foreign chains recomputation");
            return;
        };
        let active_tls_keys: BTreeSet<_> = params
            .participants()
            .participants()
            .iter()
            .map(|(_, _, info)| info.tls_public_key.clone())
            .collect();
        self.foreign_chains
            .get_mut()
            .update_available_chains_config_cache(&active_tls_keys, threshold);
```

**File:** crates/contract/src/lib.rs (L2203-2217)
```rust
        foreign_chain_to_node_mapping
            .into_iter()
            .filter_map(|(foreign_chain, nodes_supporting_chain)| {
                let all_active_nodes_supports_chain =
                    nodes_supporting_chain.is_superset(&active_participant_account_ids);

                if all_active_nodes_supports_chain {
                    Some(foreign_chain)
                } else {
                    None
                }
            })
            .cloned()
            .collect::<BTreeSet<dtos::ForeignChain>>()
            .into()
```

**File:** crates/contract/src/lib.rs (L7545-7563)
```rust
    #[test]
    fn get_available_foreign_chains__should_include_chain_when_at_least_threshold_participants_cover_it()
     {
        // Given: 4 participants, signing threshold 3; Bitcoin whitelisted.
        let (_context, mut contract, _) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut OsRng);
        let participants = participant_account_ids(&contract);
        whitelist_chain(&mut contract, dtos::ForeignChain::Bitcoin);

        // When: exactly the threshold (3) of 4 participants cover Bitcoin — one node does not.
        for account_id in participants.iter().take(3) {
            register_foreign_chain_config(&mut contract, account_id, [dtos::ForeignChain::Bitcoin]);
        }

        // Then: Bitcoin is available. A single non-covering node cannot take it down — the
        // regression the legacy intersection rule had.
        let available = contract.get_available_foreign_chains();
        assert!(available.contains(&dtos::ForeignChain::Bitcoin));
        assert_eq!(available.len(), 1);
```

**File:** docs/design/calculating-supported-foreign-chains.md (L29-37)
```markdown
- **Available** is computed dynamically from the per-node config reports: `C` is available iff
  ≥ `signing_threshold` active participants cover `C`. `available ⊆ whitelisted` always.

`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.

The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour
of the two views above.
```

**File:** crates/contract/src/foreign_chains_metadata.rs (L61-65)
```rust
        self.available_foreign_chains = chain_to_supporter_count
            .into_iter()
            .filter_map(|(chain, count)| (count >= threshold).then_some(chain))
            .collect::<BTreeSet<_>>()
            .into();
```
