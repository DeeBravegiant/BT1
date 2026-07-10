### Title
Presignature Irrevocably Consumed Before Foreign-Chain RPC Call in `make_verify_foreign_tx_leader`, Enabling Presignature Pool Drain - (File: crates/node/src/providers/verify_foreign_tx/sign.rs)

### Summary

In `make_verify_foreign_tx_leader`, the leader node permanently removes an owned presignature from storage and opens a P2P channel to followers **before** making the external foreign-chain RPC call. If the RPC call fails (timeout, network error, chain unavailability), the presignature is irrecoverably lost. Because the generic request queue retries failed requests, each retry consumes another presignature. An unprivileged caller can exploit this ordering to drain the presignature pool for the `ForeignTx` domain, stalling all `verify_foreign_transaction` requests.

### Finding Description

In `make_verify_foreign_tx_leader` the operations are ordered as follows:

1. `presignature_store.take_owned().await` — removes the presignature from RocksDB and the in-memory queue (line 63)
2. `new_channel_for_task(...)` — broadcasts the presignature ID to follower nodes (lines 65–71)
3. `execute_foreign_chain_request(...)` — makes the external foreign-chain RPC call (lines 73–78)

`take_owned()` in `DistributedAssetStorage` immediately commits a `delete` to RocksDB before returning:

```rust
pub async fn take_owned(&self) -> (UniqueId, T) {
    let (id, asset) = self.owned_queue.take_owned().await;
    let mut update = self.db.update();
    update.delete(self.col, &self.make_key(id));
    update.commit().expect("Unrecoverable error writing to database");
    (id, asset)
}
```

There is no mechanism to return a presignature to the store. If `execute_foreign_chain_request` returns `Err` (e.g., the 5-second `FOREIGN_CHAIN_INSPECTION_TIMEOUT` fires, or the RPC node returns an error), `make_verify_foreign_tx_leader` propagates the error and the presignature is permanently gone.

The design document for the foreign-chain feature explicitly acknowledges that the generic coordinator queue **retries** every failed request:

> *"the generic queue retries every request, so the foreign-tx path must special-case a sub-quorum result as non-retryable"* — `docs/design/calculating-supported-foreign-chains.md`

This means each retry of the same `verify_foreign_transaction` request consumes another presignature. An attacker who submits requests for a chain whose RPC is temporarily slow or unavailable will cause the leader to drain its presignature pool one presignature per retry attempt.

The same flaw exists in `make_verify_foreign_tx_follower`: the follower calls `execute_foreign_chain_request` before `make_signature_follower_given_request` (which calls `take_unowned`), so follower-side unowned shares are not consumed on RPC failure. The owned presignature on the leader side is the sole irrecoverable loss.

### Impact Explanation

This breaks the production accounting invariant that a presignature is consumed only when it successfully contributes to a threshold signature. Presignatures are expensive to generate (each requires two OT-based triples, which are themselves multi-round MPC computations). Draining the pool stalls all subsequent `verify_foreign_transaction` requests until the background generation loop replenishes the store. For bridge use-cases (Omnibridge inbound flow), this means inbound cross-chain transfers cannot be attested, effectively freezing the bridge flow. This matches the **Medium** allowed impact: *"request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

Any unprivileged NEAR account can call `verify_foreign_transaction` with a valid deposit. The attacker does not need to control any MPC node or hold any key material. The attacker only needs to submit requests for a chain whose configured RPC providers are temporarily unreachable (e.g., during a brief outage, rate-limiting, or by targeting a chain with a single slow provider). The 5-second `FOREIGN_CHAIN_INSPECTION_TIMEOUT` means each attempt ties up a presignature for at most 5 seconds before the loss is confirmed. With the retry loop, a sustained stream of requests against an unavailable chain will continuously drain presignatures faster than the background generation loop can replenish them.

### Recommendation

Move `presignature_store.take_owned()` to **after** `execute_foreign_chain_request` succeeds. The corrected ordering should be:

1. Fetch the foreign-tx request from the store.
2. Execute the foreign-chain RPC call and obtain `response_payload`.
3. Only then call `take_owned()` to consume the presignature.
4. Open the P2P channel and run the signing protocol.

This ensures a presignature is consumed only when the signing is guaranteed to proceed. Additionally, the design document's open item (marking sub-quorum RPC failures as non-retryable, tracked in issue #3477) should be resolved to prevent the retry loop from amplifying the drain.

### Proof of Concept

The vulnerable ordering is directly visible in the production code:

```
crates/node/src/providers/verify_foreign_tx/sign.rs, lines 54–87
```

```rust
pub(super) async fn make_verify_foreign_tx_leader(
    &self,
    id: SignatureId,
) -> anyhow::Result<((dtos::ForeignTxSignPayload, Signature), VerifyingKey)> {
    let foreign_tx_request = self.verify_foreign_tx_request_store.get(id).await?;

    let domain_data = self
        .ecdsa_signature_provider
        .domain_data(foreign_tx_request.domain_id)?;

    // STEP 1: Presignature irrevocably deleted from RocksDB here
    let (presignature_id, presignature) = domain_data.presignature_store.take_owned().await;
    let participants = presignature.participants.clone();

    // STEP 2: Channel opened, presignature_id broadcast to followers
    let channel = self.ecdsa_signature_provider.new_channel_for_task(
        VerifyForeignTxTaskId::VerifyForeignTx { id, presignature_id },
        participants,
    )?;

    // STEP 3: External RPC call — if this returns Err, presignature is gone forever
    let response_payload = self
        .execute_foreign_chain_request(
            &foreign_tx_request.request,
            foreign_tx_request.payload_version,
        )
        .await?;  // <-- early return drops presignature permanently

    // ...signing proceeds only if step 3 succeeded
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L54-87)
```rust
    pub(super) async fn make_verify_foreign_tx_leader(
        &self,
        id: SignatureId,
    ) -> anyhow::Result<((dtos::ForeignTxSignPayload, Signature), VerifyingKey)> {
        let foreign_tx_request = self.verify_foreign_tx_request_store.get(id).await?;

        let domain_data = self
            .ecdsa_signature_provider
            .domain_data(foreign_tx_request.domain_id)?;
        let (presignature_id, presignature) = domain_data.presignature_store.take_owned().await;
        let participants = presignature.participants.clone();
        let channel = self.ecdsa_signature_provider.new_channel_for_task(
            VerifyForeignTxTaskId::VerifyForeignTx {
                id,
                presignature_id,
            },
            participants,
        )?;

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
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L117-150)
```rust
    async fn execute_foreign_chain_request(
        &self,
        request: &dtos::ForeignChainRpcRequest,
        payload_version: dtos::ForeignTxPayloadVersion,
    ) -> anyhow::Result<dtos::ForeignTxSignPayload> {
        chain_is_supported(&self.foreign_chain_policy_reader, request).await?;

        let values: Vec<dtos::ExtractedValue> = match request {
            dtos::ForeignChainRpcRequest::Ethereum(_request) => {
                bail!("ForeignChainRpcRequest::Ethereum is unsupported")
            }
            dtos::ForeignChainRpcRequest::Solana(_request) => {
                bail!("ForeignChainRpcRequest::Solana is unsupported")
            }
            dtos::ForeignChainRpcRequest::Bitcoin(request) => {
                let inspector = self
                    .inspectors
                    .bitcoin
                    .as_ref()
                    .context("no inspector configured for bitcoin")?;
                let transaction_id = request.tx_id.0.into();
                let block_confirmations = request.confirmations.0.into();
                let extractors: Vec<BitcoinExtractor> = request
                    .extractors
                    .iter()
                    .cloned()
                    .map(TryInto::try_into)
                    .collect::<Result<_, _>>()?;
                let extracted_values = inspector
                    .extract(transaction_id, block_confirmations, extractors)
                    .timeout(FOREIGN_CHAIN_INSPECTION_TIMEOUT)
                    .await
                    .context("timed out during execution of foreign chain request")??;
                extracted_values.into_iter().map(Into::into).collect()
```

**File:** crates/node/src/assets.rs (L497-505)
```rust
    pub async fn take_owned(&self) -> (UniqueId, T) {
        let (id, asset) = self.owned_queue.take_owned().await;
        let mut update = self.db.update();
        update.delete(self.col, &self.make_key(id));
        update
            .commit()
            .expect("Unrecoverable error writing to database");
        (id, asset)
    }
```

**File:** docs/design/calculating-supported-foreign-chains.md (L62-67)
```markdown
**This sub-quorum outcome must be terminal — the leader must not re-attempt the
request.** Implementation requirement, not current behavior: the generic queue
retries every request, so the foreign-tx path must special-case a sub-quorum
result as non-retryable. (Open: whether a sub-quorum from purely *transient*
failures — timeouts, finality not reached — should still retry, vs. only genuine
disagreement being terminal. Tracked in [#3477](https://github.com/near/mpc/issues/3477).)
```
