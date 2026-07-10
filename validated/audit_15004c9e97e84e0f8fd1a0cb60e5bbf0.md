### Title
`verify_foreign_transaction` Uses Legacy Intersection Check Instead of Whitelist-Gated Availability Check, Bypassing Governance Removal - (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` gates on `get_supported_foreign_chains()` — the legacy all-participant intersection rule — instead of `get_available_foreign_chains()`, which is the governance-controlled, whitelist-gated method the design mandates. Because `get_supported_foreign_chains()` never consults the on-chain RPC whitelist, a chain that has been governance-voted out of the whitelist (or was never voted in) can still be submitted for foreign-chain verification and signed by MPC nodes, bypassing the network's chain-governance mechanism entirely.

---

### Finding Description

`verify_foreign_transaction` performs the following chain-availability check:

```rust
let requested_chain = request.request.chain();
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(...)
}
``` [1](#0-0) 

`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's locally registered chains. It contains no whitelist check:

```rust
pub fn get_supported_foreign_chains(&self) -> dtos::SupportedForeignChains {
    // ...
    .filter_map(|(foreign_chain, nodes_supporting_chain)| {
        let all_active_nodes_supports_chain =
            nodes_supporting_chain.is_superset(&active_participant_account_ids);
        if all_active_nodes_supports_chain { Some(foreign_chain) } else { None }
    })
``` [2](#0-1) 

The design documentation explicitly states that `verify_foreign_transaction` must gate on `get_available_foreign_chains()`, which enforces two invariants that `get_supported_foreign_chains()` does not:

1. The chain must be **whitelisted** — voted into the on-chain `foreign_chain_rpc_whitelist` by a threshold of participants.
2. At least `signing_threshold` active participants must currently cover it.

The test suite confirms that `get_available_foreign_chains()` enforces the whitelist:

```
get_available_foreign_chains__should_exclude_chain_that_is_covered_but_not_whitelisted
// Bitcoin is NOT whitelisted. All 4 participants cover it.
// Then: Bitcoin is still not available — `available` is a subset of `whitelisted`.
``` [3](#0-2) 

The design document explicitly identifies this as the intended replacement:

> "The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour of the two views above." [4](#0-3) 

> "`verify_foreign_transaction(C)` is **rejected unless `C` is available**" [5](#0-4) 

The analog to the external report is exact: the whitelist governance flag (chain membership in `foreign_chain_rpc_whitelist`) is set and updated by threshold vote, but is **never consulted** at the `verify_foreign_transaction` entry point, because the wrong check function is called.

---

### Impact Explanation

**High.** The on-chain RPC whitelist is the network's governance mechanism for which foreign chains are trusted. When the network votes to remove a chain from the whitelist (e.g., because its RPC providers are compromised or the chain is malicious), `verify_foreign_transaction` continues to accept requests for that chain as long as all active participants still have it in their local registration. MPC nodes will process the request, produce signature shares, and submit a `respond_verify_foreign_tx` call. The resulting signed observation can be consumed by a NEAR bridge contract to trigger inbound bridge execution (e.g., minting tokens) based on data from a chain the network has explicitly de-authorized. This constitutes a forged foreign-chain verification and invalid bridge execution.

---

### Likelihood Explanation

**High.** The whitelist check is structurally absent — `verify_foreign_transaction` calls `get_supported_foreign_chains()` unconditionally, and that function has no code path that touches the whitelist. After any governance vote to remove a chain, there is an indefinite window (until all participants manually update and re-register their local configs) during which the chain remains in `get_supported_foreign_chains()`. Any unprivileged user can submit a `verify_foreign_transaction` request for the de-whitelisted chain during this window. No special access is required.

---

### Recommendation

Replace the `get_supported_foreign_chains()` call inside `verify_foreign_transaction` with `get_available_foreign_chains()`:

```rust
// Before (bypasses whitelist):
let supported_chains = self.get_supported_foreign_chains();

// After (enforces whitelist + threshold coverage):
let supported_chains = self.get_available_foreign_chains();
``` [1](#0-0) 

This aligns the runtime gate with the design invariant stated in the documentation and enforced by `get_available_foreign_chains()`.

---

### Proof of Concept

1. Network governance votes in `ChainEntry` for `ForeignChain::Bitcoin` (whitelist it).
2. All participants register Bitcoin in their local config via `register_foreign_chain_config`.
3. Network governance votes to **remove** Bitcoin from the whitelist (e.g., RPC providers compromised).
4. After the vote, `get_available_foreign_chains()` returns an empty set (Bitcoin no longer whitelisted).
5. `get_supported_foreign_chains()` still returns Bitcoin (all participants still have it registered locally).
6. An unprivileged user calls `verify_foreign_transaction` with a Bitcoin request.
7. The contract checks `get_supported_foreign_chains()` → Bitcoin is present → request is accepted and enqueued.
8. MPC nodes process the request, query the (now-untrusted) Bitcoin RPC providers, and sign the observation.
9. The signed observation is submitted on-chain via `respond_verify_foreign_tx` and can be consumed by a bridge contract to execute an inbound transfer — despite the network having governance-removed Bitcoin.

The root cause is at: [1](#0-0) 

The correct check that is never called is: [2](#0-1)

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

**File:** crates/contract/src/lib.rs (L2176-2217)
```rust
    pub fn get_supported_foreign_chains(&self) -> dtos::SupportedForeignChains {
        let active_participant_account_ids = self
            .protocol_state
            .active_participants()
            .participants()
            .iter()
            .map(|(account_id, _, _)| account_id.clone())
            .collect::<BTreeSet<_>>();

        let mut foreign_chain_to_node_mapping: BTreeMap<
            &dtos::ForeignChain,
            BTreeSet<dtos::AccountId>,
        > = BTreeMap::new();

        for (account_id, chains) in self
            .node_foreign_chain_support
            .foreign_chain_support_by_node
            .iter()
        {
            for chain in chains.iter() {
                foreign_chain_to_node_mapping
                    .entry(chain)
                    .or_default()
                    .insert(account_id.clone());
            }
        }

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

**File:** crates/contract/src/lib.rs (L7585-7600)
```rust
    #[test]
    fn get_available_foreign_chains__should_exclude_chain_that_is_covered_but_not_whitelisted() {
        // Given: 4 participants, threshold 3; Bitcoin is NOT whitelisted.
        let (_context, mut contract, _) =
            basic_setup_with_protocol(Protocol::CaitSith, DomainPurpose::ForeignTx, &mut OsRng);
        let participants = participant_account_ids(&contract);

        // When: all 4 participants cover Bitcoin.
        for account_id in &participants {
            register_foreign_chain_config(&mut contract, account_id, [dtos::ForeignChain::Bitcoin]);
        }

        // Then: Bitcoin is still not available — `available` is a subset of `whitelisted`.
        let available = contract.get_available_foreign_chains();
        assert!(available.is_empty());
    }
```

**File:** docs/design/calculating-supported-foreign-chains.md (L32-34)
```markdown
`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.
```

**File:** docs/design/calculating-supported-foreign-chains.md (L36-37)
```markdown
The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour
of the two views above.
```
