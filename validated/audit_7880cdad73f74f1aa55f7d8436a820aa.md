### Title
Single-Participant Chain De-Registration Invalidates All Pending `verify_foreign_transaction` Requests — (File: `crates/contract/src/lib.rs`)

### Summary

`verify_foreign_transaction` gates on `get_supported_foreign_chains()`, which is computed as the **strict intersection** of every active participant's registered chains. Any single participant can call `register_foreign_chain_support({})` at any time to remove all chains from their registration, instantly collapsing the supported-chain set to empty. All pending `verify_foreign_transaction` requests that were accepted while the chain was supported will then time out because MPC nodes re-check chain support before processing and refuse to produce signature shares.

### Finding Description

`verify_foreign_transaction` in `crates/contract/src/lib.rs` checks `get_supported_foreign_chains()` at request-submission time: [1](#0-0) 

`get_supported_foreign_chains()` computes the strict intersection — a chain is included only if **every** active participant has registered it: [2](#0-1) 

Any participant can overwrite their registration at any time via `register_foreign_chain_support`, which performs no rate-limiting or minimum-set enforcement: [3](#0-2) 

Calling this with an empty `SupportedForeignChains` immediately removes that participant's entry, collapsing the intersection to empty for every chain they previously covered.

On the node side, `execute_foreign_chain_request` re-checks chain support before querying the foreign chain RPC: [4](#0-3) 

This check reads from the node's indexer view of the contract state. Once the de-registration is indexed, every node refuses to produce a signature share for any pending request targeting the now-unsupported chain. The requests time out silently — the design doc explicitly notes that failed verifications produce no on-chain failure signal: [5](#0-4) 

The design doc itself acknowledges the root cause: "A single node that registers an empty list (or hasn't registered yet) drops **every** chain — one operator can take the whole feature down." [6](#0-5) 

This is tracked as issue #3434 but is **not yet fixed in production code**. The current `get_supported_foreign_chains()` still uses the strict intersection rule.

### Impact Explanation

A single Byzantine participant (strictly below signing threshold) can:

1. Wait for multiple `verify_foreign_transaction` requests to accumulate in the pending queue.
2. Call `register_foreign_chain_support({})` — one transaction, no special privilege beyond being a participant.
3. All pending requests for every previously-supported chain time out. Users receive no signature and no explicit error.

In a bridge context (the primary stated use case — Omnibridge inbound flow), this freezes in-flight bridge operations: a user who deposited assets on a foreign chain and submitted a `verify_foreign_transaction` to claim on NEAR cannot complete the claim. The deposit is not stolen but the claim is permanently blocked until the participant re-registers and the user resubmits.

This matches the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

### Likelihood Explanation

- Requires only a single participant account — no collusion, no threshold.
- The call is a standard contract method (`register_foreign_chain_support`) with no rate limit, no cooldown, and no minimum-set constraint.
- The attacker can time the de-registration to maximise the number of in-flight requests invalidated.
- Re-registration by the same participant immediately restores the chain, making the attack repeatable and deniable ("accidental misconfiguration").

### Recommendation

Replace the strict-intersection rule with the threshold-based availability model already designed in `docs/design/calculating-supported-foreign-chains.md` (issue #3434):

- A chain is **available** iff ≥ `signing_threshold` active participants cover it.
- `verify_foreign_transaction` gates on `get_available_foreign_chains()` instead of `get_supported_foreign_chains()`.
- No single participant below threshold can collapse the available set.

Until that migration lands, add a minimum-coverage floor: reject a `register_foreign_chain_support` call that would drop any previously-supported chain below `signing_threshold` coverage.

### Proof of Concept

```
Setup: 5-participant network, threshold = 3. All 5 participants have registered
       {Bitcoin, Ethereum}. get_supported_foreign_chains() = {Bitcoin, Ethereum}.

Step 1: Bridge users submit 50 verify_foreign_transaction(Bitcoin, ...) requests.
        All accepted — Bitcoin is in get_supported_foreign_chains().

Step 2: Attacker (participant P1) calls:
        register_foreign_chain_support(foreign_chain_support: {})

Step 3: get_supported_foreign_chains() now returns {} (strict intersection broken).

Step 4: MPC nodes' indexers pick up P1's de-registration.
        execute_foreign_chain_request → chain_is_supported() → false.
        All 50 pending requests are abandoned by every node.

Step 5: 50 requests time out. Users receive no signature, no error message.
        Bridge deposits on Bitcoin are frozen.

Step 6: P1 calls register_foreign_chain_support({Bitcoin, Ethereum}) again.
        Attack is repeatable at will.
``` [7](#0-6) [8](#0-7)

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L73-115)
```rust
        let response_payload = self
            .execute_foreign_chain_request(
                &foreign_tx_request.request,
                foreign_tx_request.payload_version,
            )
            .await?;

        let sign_request = build_signature_request(&foreign_tx_request, &response_payload)?;

        let response = self
            .ecdsa_signature_provider
            .make_signature_leader_given_parameters(sign_request, presignature, channel)
            .await?;
        Ok(((response_payload, response.0), response.1))
    }

    pub(super) async fn make_verify_foreign_tx_follower(
        &self,
        channel: NetworkTaskChannel,
        id: SignatureId,
        presignature_id: UniqueId,
    ) -> anyhow::Result<()> {
        metrics::MPC_NUM_PASSIVE_SIGN_REQUESTS_RECEIVED.inc();
        let foreign_tx_request = timeout(
            Duration::from_secs(self.config.signature.timeout_sec),
            self.verify_foreign_tx_request_store.get(id),
        )
        .await??;
        metrics::MPC_NUM_PASSIVE_SIGN_REQUESTS_LOOKUP_SUCCEEDED.inc();

        let response_payload = self
            .execute_foreign_chain_request(
                &foreign_tx_request.request,
                foreign_tx_request.payload_version,
            )
            .await?;

        let sign_request = build_signature_request(&foreign_tx_request, &response_payload)?;

        self.ecdsa_signature_provider
            .make_signature_follower_given_request(channel, presignature_id, sign_request)
            .await
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L117-123)
```rust
    async fn execute_foreign_chain_request(
        &self,
        request: &dtos::ForeignChainRpcRequest,
        payload_version: dtos::ForeignTxPayloadVersion,
    ) -> anyhow::Result<dtos::ForeignTxSignPayload> {
        chain_is_supported(&self.foreign_chain_policy_reader, request).await?;

```

**File:** docs/foreign-chain-transactions.md (L557-559)
```markdown
* Nodes **do not participate** if RPC queries fail or extraction fails.
* A failed verification does **not** produce an on-chain failure response. The request eventually times out and fails with the standard timeout error.
* *Known limitation:* a failed verification is not signalled explicitly — even when the failure reason is known (RPC sub-quorum, extraction error), the request just times out. Emitting an explicit failure so callers can react sooner is a desirable improvement, tracked in [#3477](https://github.com/near/mpc/issues/3477).
```

**File:** docs/design/calculating-supported-foreign-chains.md (L9-13)
```markdown
Today, `get_supported_foreign_chains()` returns the **strict intersection** of every
active participant's registered chains, and `verify_foreign_transaction` rejects any
request whose target chain is not in it. A single node that registers an empty list
(or hasn't registered yet) drops **every** chain — one operator can take the whole
feature down. That is what this proposal fixes.
```
