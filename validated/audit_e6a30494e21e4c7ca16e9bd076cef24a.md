### Title
Single-Participant Spot-State Manipulation Permanently Blocks All Foreign-Chain Verification Requests — (`File: crates/contract/src/lib.rs`)

### Summary

`verify_foreign_transaction` gates on `get_supported_foreign_chains()`, which reads the **current, instantly-mutable** per-node chain-support registrations. A single Byzantine participant (strictly below the signing threshold) can call `register_foreign_chain_support` with an empty set at any time, atomically dropping every chain from the supported set and causing every subsequent `verify_foreign_transaction` call to panic-reject. This is the direct analog of the LP.sol oracle-manipulation class: a spot-value oracle whose inputs are freely writable by a single actor, with no time-weighting, quorum guard, or cooldown.

---

### Finding Description

`verify_foreign_transaction` reads the supported-chain set at request-submission time:

```rust
let requested_chain = request.request.chain();
let supported_chains = self.get_supported_foreign_chains();
if !supported_chains.contains(&requested_chain) {
    env::panic_str(...);
}
``` [1](#0-0) 

`get_supported_foreign_chains()` computes the **strict intersection** of every active participant's current registration:

```rust
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);
if all_active_nodes_supports_chain { Some(foreign_chain) } else { None }
``` [2](#0-1) 

The registration itself is a single, unguarded map write — no validation, no rate limit, no quorum, no cooldown:

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
``` [3](#0-2) 

Because the intersection rule requires **all** active participants to cover a chain, a single participant inserting an empty `SupportedForeignChains` set instantly removes every chain from the result of `get_supported_foreign_chains()`. The project's own design documentation explicitly identifies this root cause:

> "A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down."



The proposed fix (threshold-based `get_available_foreign_chains()`) exists only in a design document; the production contract still uses the strict-intersection path.

---

### Impact Explanation

**Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants.**

The `verify_foreign_transaction` endpoint is the on-chain gateway for bridge inbound flows (foreign chain → NEAR). When the supported-chain set is emptied:

- Every `verify_foreign_transaction` call panics with `ForeignChainNotSupported`, regardless of which chain is requested.
- Pending bridge deposits on the foreign chain cannot be attested; the corresponding NEAR-side release is permanently blocked.
- Bridge accounting invariants are broken: assets are debited on the foreign chain but the NEAR-side credit is never issued.

The attacker does not need to forge a signature or collude with other participants. The manipulation is purely a state write on the NEAR contract, executable in a single transaction.

---

### Likelihood Explanation

**High.** Any single active participant — strictly below the signing threshold — can execute this attack at any time. The call requires only that `env::signer_account_id() == env::predecessor_account_id()` (the `voter_or_panic()` guard), which is satisfied by a direct transaction from the participant's own NEAR account. There is no cooldown, no minimum registration period, and no on-chain penalty for deregistering. The attacker can toggle the attack on and off across blocks, making it a persistent, low-cost griefing vector with potential for targeted bridge disruption.

---

### Recommendation

Replace the strict-intersection oracle with the threshold-based availability check already designed by the team:

1. Implement `get_available_foreign_chains()` — a chain is available iff ≥ `signing_threshold` active participants currently cover it.
2. Gate `verify_foreign_transaction` on `get_available_foreign_chains()` instead of `get_supported_foreign_chains()`.
3. Introduce the on-chain RPC whitelist (`foreign_chain_rpc_whitelist`) so that the **whitelisted** set (which chains the network trusts) is governed by threshold vote and cannot be altered by a single participant.
4. Apply a minimum registration-stability window (analogous to TWAP) before a newly registered chain is counted toward the available set, preventing rapid toggle attacks. [4](#0-3) 

---

### Proof of Concept

```
// Precondition: all N participants have registered support for Bitcoin.
// get_supported_foreign_chains() returns {Bitcoin}.
// verify_foreign_transaction(Bitcoin, ...) succeeds.

// Attack — single Byzantine participant (participant_k, k < threshold):
participant_k.call(
    mpc_contract,
    "register_foreign_chain_support",
    { "foreign_chain_support": [] }   // empty set
)

// Post-condition:
// get_supported_foreign_chains() returns {} (empty intersection).
// Every subsequent verify_foreign_transaction call panics:
//   "ForeignChainNotSupported { requested: Bitcoin }"
// Bridge inbound flow is completely halted.
// participant_k can restore the state at will by re-registering Bitcoin,
// making the attack togglable across blocks at zero cryptographic cost.
``` [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** crates/contract/src/lib.rs (L519-557)
```rust
    pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
        log!(
            "verify_foreign_transaction: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );

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

        let callback_gas = Gas::from_tgas(
            self.config
                .return_signature_and_clean_state_on_success_call_tera_gas,
        );

        let request = args_into_verify_foreign_tx_request(request);
        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_VERIFY_FOREIGN_TX_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_verify_foreign_tx_request(request, id),
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

**File:** crates/contract/src/lib.rs (L2176-2218)
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
    }
```

**File:** docs/design/calculating-supported-foreign-chains.md (L19-37)
```markdown
## Proposal: two sets of chains

> **Terms** (whitelisted, available, RPC quorum, signing threshold, *covers*) are defined in
> [Foreign Chain Transaction Verification Design — Terminology](../foreign-chain-transactions.md#terminology).

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
