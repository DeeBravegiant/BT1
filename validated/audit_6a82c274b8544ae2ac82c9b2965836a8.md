### Title
Single Byzantine Participant Can Block All `verify_foreign_transaction` Requests via Empty Chain Registration - (File: `crates/contract/src/lib.rs`, `docs/design/calculating-supported-foreign-chains.md`)

### Summary
The `get_supported_foreign_chains()` function computes the **strict intersection** of every active participant's registered foreign chains. A single Byzantine participant below the signing threshold can block all `verify_foreign_transaction` requests by calling `register_foreign_chain_support` with an empty list, collapsing the intersection to the empty set and causing every foreign-chain verification request to be rejected at the contract level.

### Finding Description
When a user calls `verify_foreign_transaction`, the contract enforces:

```rust
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(
        &InvalidParameters::ForeignChainNotSupported {
            requested: requested_chain,
        }
        .to_string(),
    );
}
``` [1](#0-0) 

`get_supported_foreign_chains()` returns the strict intersection of every active participant's registered chain list. This is explicitly documented as the current production behavior in the design document:

> "Today, `get_supported_foreign_chains()` returns the **strict intersection** of every active participant's registered chains, and `verify_foreign_transaction` rejects any request whose target chain is not in it. A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down." [2](#0-1) 

The proposed fix — replacing the intersection rule with a threshold-based availability check — is tracked in issue #3434 and is explicitly **not yet implemented**; the document's status is "Proposed." [3](#0-2) 

The analog to the external report is direct: just as `removeLiq` iterates over all vaults and one paused vault blocks the entire withdrawal, `verify_foreign_transaction` checks the intersection over all participants and one participant with an empty registration blocks all chains.

### Impact Explanation
A single Byzantine participant (strictly below the signing threshold) can permanently block the entire `verify_foreign_transaction` feature for all users by calling `register_foreign_chain_support` with an empty list. The intersection of any set with the empty set is empty, so every subsequent `verify_foreign_transaction` call for any chain panics with `ForeignChainNotSupported`. This breaks the liveness of the bridge/foreign-chain verification flow — a contract execution-flow manipulation that breaks production safety/accounting invariants. The user's deposit (1 yoctoNEAR) is consumed and the request is never queued.

**Impact: Medium** — contract execution-flow manipulation that breaks production safety/accounting invariants without requiring network-level DoS or threshold collusion.

### Likelihood Explanation
`register_foreign_chain_support` is callable by any active participant. A single Byzantine participant (one of n nodes, where n > signing threshold) is sufficient. The design document explicitly acknowledges this as a reachable attack vector ("one operator can take the whole feature down"), confirming it is not merely theoretical. The attacker does not need to collude with any other participant.

**Likelihood: Medium** — requires one Byzantine participant, which is explicitly within the allowed attacker model ("Byzantine participant strictly below the signing threshold").

### Recommendation
Implement the threshold-based availability check proposed in `docs/design/calculating-supported-foreign-chains.md`:

- Replace `get_supported_foreign_chains()` (strict intersection) with `get_available_foreign_chains()` (threshold-based): a chain `C` is available if ≥ `signing_threshold` active participants report coverage for `C`.
- `verify_foreign_transaction` should reject only if `C` is not in the **available** set, not the intersection set.
- This ensures no single participant can collapse the available set to empty. [4](#0-3) 

### Proof of Concept
1. Attacker is participant `P_evil`, one of `n` active participants where `n > signing_threshold` (i.e., strictly below threshold).
2. Attacker calls `register_foreign_chain_support({})` — an empty chain list — from `P_evil`'s account.
3. `get_supported_foreign_chains()` computes the intersection of all participants' registered chains. Since `P_evil` registered `{}`, the intersection is `{}` (empty set).
4. Any user calling `verify_foreign_transaction` for Bitcoin, Ethereum, Solana, or any other chain receives `ForeignChainNotSupported` and the transaction panics.
5. All bridge/foreign-chain verification is blocked for all users until `P_evil` re-registers a non-empty chain list.
6. The attacker can sustain the block indefinitely by re-registering an empty list whenever challenged.

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

**File:** docs/design/calculating-supported-foreign-chains.md (L1-6)
```markdown
# Calculating the whitelisted and available foreign-chain sets

Status: Proposed — supersedes the all-participant intersection rule in
[`docs/foreign-chain-transactions.md`](../foreign-chain-transactions.md). Tracked by
[#3434](https://github.com/near/mpc/issues/3434).

```

**File:** docs/design/calculating-supported-foreign-chains.md (L9-13)
```markdown
Today, `get_supported_foreign_chains()` returns the **strict intersection** of every
active participant's registered chains, and `verify_foreign_transaction` rejects any
request whose target chain is not in it. A single node that registers an empty list
(or hasn't registered yet) drops **every** chain — one operator can take the whole
feature down. That is what this proposal fixes.
```

**File:** docs/design/calculating-supported-foreign-chains.md (L24-37)
```markdown
The network distinguishes the **whitelisted** set (vote-driven policy, `get_whitelisted_foreign_chains()`)
from the **available** set (servable right now, `get_available_foreign_chains()`):

- **Whitelisted** is derived purely from the on-chain RPC whitelist — **no per-node input can add or
  remove a chain**, so no single operator can change it.
- **Available** is computed dynamically from the per-node config reports: `C` is available iff
  ≥ `signing_threshold` active participants cover `C`. `available ⊆ whitelisted` always.

`verify_foreign_transaction(C)` is **rejected unless `C` is available**: the contract fails fast
instead of accepting a request that can't reach the signing threshold and letting it time out. The
rejection is temporary — `C` becomes serviceable again as soon as enough nodes report coverage.

The legacy `get_supported_foreign_chains()` (the intersection rule) is **to be deprecated** in favour
of the two views above.
```
