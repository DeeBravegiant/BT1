### Title
Single-participant foreign-chain de-registration permanently bricks all `verify_foreign_transaction` requests via strict intersection rule — (`crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` gates on `get_supported_foreign_chains()`, which enforces a strict all-participant intersection rule. A single participant (strictly below the signing threshold) can permanently brick ALL foreign-chain verification by re-registering with an empty chain list. There is no protocol-level mechanism to override this without that participant's cooperation, directly analogous to the Convex pool shutdown scenario in the reference report.

---

### Finding Description

`verify_foreign_transaction` at line 534 calls `get_supported_foreign_chains()`:

```rust
let requested_chain = request.request.chain();
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(
        &InvalidParameters::ForeignChainNotSupported { requested: requested_chain }.to_string(),
    );
}
```

`get_supported_foreign_chains()` at line 2176 computes the **strict intersection** of every active participant's registered chains:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);
if all_active_nodes_supports_chain {
    Some(foreign_chain)
} else {
    None
}
```

Any participant can call `register_foreign_chain_support` (line 972) — gated only by `voter_or_panic()`, i.e., being a current participant — and submit an empty `SupportedForeignChains` set:

```rust
pub fn register_foreign_chain_support(
    &mut self,
    foreign_chain_support: dtos::SupportedForeignChains,
) -> Result<(), Error> {
    let account_id = self.voter_or_panic();
    self.node_foreign_chain_support
        .foreign_chain_support_by_node
        .insert(account_id, foreign_chain_support);
    Ok(())
}
```

Once one participant registers an empty set, `get_supported_foreign_chains()` returns an empty set, and every subsequent `verify_foreign_transaction` call panics with `ForeignChainNotSupported` — regardless of which chain is requested.

The design documentation explicitly acknowledges this root cause:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains, and `verify_foreign_transaction` rejects any request whose target chain is not in it. A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down."

The proposed fix — switching `verify_foreign_transaction` to gate on `get_available_foreign_chains()` (which requires only `signing_threshold` participants to cover a chain, not all) — exists in the contract at line 2226 but has **not** been wired into `verify_foreign_transaction`. The production path still calls `get_supported_foreign_chains()`.

The analog to the reference report is direct: just as the Amphora protocol had no mechanism to update a collateral type when the Convex pool (external dependency) shut down, the NEAR MPC contract has no mechanism to continue serving `verify_foreign_transaction` requests when a participant's RPC provider for a foreign chain becomes unavailable and that participant re-registers without it. In both cases, a single external-dependency failure permanently bricks a user-facing flow with no in-protocol recovery path.

---

### Impact Explanation

Every `verify_foreign_transaction` call for every chain fails with `ForeignChainNotSupported` the moment any one participant's registered chain set becomes empty. All in-flight yield-resume promises for pending foreign-tx requests will time out and fail. Bridge services or NEAR contracts that depend on `verify_foreign_transaction` for inbound cross-chain flows (e.g., Omnibridge) are permanently blocked until the offending participant re-registers — an action the protocol cannot compel. This matches the **Medium** allowed impact: "request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."

---

### Likelihood Explanation

The trigger requires only one participant (strictly below threshold) to call `register_foreign_chain_support` with an empty set. This can occur:

1. **Unintentionally**: a participant's RPC provider for a foreign chain goes down; the node re-registers its current (now-reduced) coverage, dropping that chain and collapsing the intersection to empty.
2. **Intentionally**: a single malicious or compromised participant calls `register_foreign_chain_support({})` directly.

No threshold collusion, no key material, and no privileged access is required. The call is open to any active participant.

---

### Recommendation

Replace the `get_supported_foreign_chains()` gate in `verify_foreign_transaction` with `get_available_foreign_chains()`, which already exists and uses the threshold-based availability check:

```rust
// Before (strict intersection — one participant can zero the set):
let supported_chains = self.get_supported_foreign_chains();

// After (threshold-based — tolerates up to n − signing_threshold non-covering nodes):
let supported_chains = self.get_available_foreign_chains();
```

`get_available_foreign_chains()` returns chains covered by at least `signing_threshold` active participants, so no single participant below threshold can collapse the available set to empty. This is the fix already designed and partially implemented (tracked in issue #3434); it only needs to be wired into `verify_foreign_transaction`.

---

### Proof of Concept

```
// Setup: 3-of-4 threshold, all 4 participants have registered Bitcoin.
// verify_foreign_transaction(Bitcoin) succeeds.

// Step 1: participant_1's Bitcoin RPC provider goes down.
// participant_1 re-registers with an empty set:
participant_1.call("register_foreign_chain_support", { foreign_chain_support: [] });

// Step 2: get_supported_foreign_chains() now returns {} (empty intersection).

// Step 3: any user calls verify_foreign_transaction(Bitcoin):
user.call("verify_foreign_transaction", { request: { chain: Bitcoin, ... } });
// → panics: ForeignChainNotSupported { requested: Bitcoin }

// Step 4: same result for Ethereum, Solana, or any other chain.
// ALL foreign-tx verification is bricked until participant_1 re-registers.
// The protocol has no mechanism to force or substitute this.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L972-983)
```rust
    pub fn register_foreign_chain_support(
        &mut self,
        foreign_chain_support: dtos::SupportedForeignChains,
    ) -> Result<(), Error> {
        let account_id = self.voter_or_panic();

        self.node_foreign_chain_support
            .foreign_chain_support_by_node
            .insert(account_id, foreign_chain_support);

        Ok(())
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

**File:** crates/contract/src/lib.rs (L2224-2228)
```rust
    /// The **available** foreign chains: whitelisted chains that are supported
    /// by at least the signing threshold of active participants.
    pub fn get_available_foreign_chains(&self) -> dtos::AvailableForeignChains {
        self.foreign_chains.get().available_foreign_chains.clone()
    }
```

**File:** docs/design/calculating-supported-foreign-chains.md (L9-13)
```markdown
Today, `get_supported_foreign_chains()` returns the **strict intersection** of every
active participant's registered chains, and `verify_foreign_transaction` rejects any
request whose target chain is not in it. A single node that registers an empty list
(or hasn't registered yet) drops **every** chain — one operator can take the whole
feature down. That is what this proposal fixes.
```
