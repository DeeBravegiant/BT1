### Title
Presignature Participants Selected Without Foreign-Chain Coverage Check — (`crates/node/src/providers/verify_foreign_tx/sign.rs`)

---

### Summary

`make_verify_foreign_tx_leader()` takes a presignature from the domain store without verifying that the presignature's participants cover the requested foreign chain. Presignatures are generated with participants chosen for liveness, not chain coverage. Follower nodes that lack the requested chain's inspector fail inside `execute_foreign_chain_request()` and cannot produce a signature share. If fewer than the reconstruction threshold of covering participants are in the presignature, the signing fails, the presignature is consumed, and the user's request times out.

---

### Finding Description

In `make_verify_foreign_tx_leader()`, the presignature is taken unconditionally from the domain's presignature store:

```rust
let domain_data = self.ecdsa_signature_provider.domain_data(foreign_tx_request.domain_id)?;
let (presignature_id, presignature) = domain_data.presignature_store.take_owned().await;
let participants = presignature.participants.clone();
``` [1](#0-0) 

No check is made that the presignature's participants cover the requested foreign chain. Presignatures are generated during the general signing protocol with participants chosen for liveness, not for chain coverage.

When a follower processes the request in `make_verify_foreign_tx_follower()`, it calls `execute_foreign_chain_request()`:

```rust
let response_payload = self
    .execute_foreign_chain_request(
        &foreign_tx_request.request,
        foreign_tx_request.payload_version,
    )
    .await?;
``` [2](#0-1) 

Inside `execute_foreign_chain_request()`, each chain arm requires a configured inspector:

```rust
dtos::ForeignChainRpcRequest::Bitcoin(request) => {
    let inspector = self
        .inspectors
        .bitcoin
        .as_ref()
        .context("no inspector configured for bitcoin")?;
``` [3](#0-2) 

If a follower node has no inspector configured for the requested chain, the function returns an error and that node produces no signature share. The design document explicitly acknowledges this structural gap:

> *"Foreign-tx signing must elect participants that cover the requested chain… Implementation requirement, not current behavior: today the signing set is inherited from a presignature, whose participants were chosen for liveness, not chain coverage."*



The contract-side `verify_foreign_transaction()` accepts the request if the chain is in `get_supported_foreign_chains()` (threshold participants cover it overall), but the specific presignature participants may not all cover the chain. [4](#0-3) 

---

### Impact Explanation

This is a **Medium** request-lifecycle issue. If the presignature contains more than `n − reconstruction_threshold` participants that lack the requested chain's inspector, fewer than `reconstruction_threshold` shares are produced and the signing fails. The presignature is consumed (removed from the store) but the signing produces no result, causing the user's `verify_foreign_transaction` yield to time out. This breaks the foreign-tx verification flow for any request whose presignature happens to include non-covering participants.

---

### Likelihood Explanation

Any deployment where not all participants cover all whitelisted chains is affected. The `get_available_foreign_chains()` gate only ensures that *at least* `signing_threshold` participants cover the chain globally — it does not guarantee that the specific presignature drawn from the store has enough covering participants. Since presignatures are generated continuously in the background without chain-coverage filtering, this mismatch occurs in normal operation whenever a presignature is drawn that includes non-covering participants.

---

### Recommendation

Before consuming a presignature for a foreign-tx signing request, filter the presignature store to only select presignatures whose participant set contains at least `reconstruction_threshold` participants that cover the requested chain (as reported via `register_foreign_chain_config`). Alternatively, generate a separate presignature pool per foreign chain, or re-elect participants at signing time based on chain coverage rather than inheriting the presignature's liveness-based participant set.

---

### Proof of Concept

1. Deploy a 4-node network with `reconstruction_threshold = 3`.
2. Configure nodes 1–3 to cover Bitcoin; node 4 has no Bitcoin inspector.
3. The background presignature protocol generates a presignature with participants `{1, 2, 3, 4}` (all four, chosen for liveness).
4. A user submits `verify_foreign_transaction` for Bitcoin. The contract accepts it (`get_supported_foreign_chains()` returns Bitcoin because 3 of 4 nodes cover it).
5. The leader calls `make_verify_foreign_tx_leader()`, takes the presignature with participants `{1, 2, 3, 4}`, and broadcasts the task.
6. Node 4 enters `make_verify_foreign_tx_follower()`, calls `execute_foreign_chain_request()`, hits `self.inspectors.bitcoin.as_ref().context("no inspector configured for bitcoin")?`, and returns an error — producing no share.
7. Only 3 shares are available from nodes 1–3. If the presignature was generated with exactly 3 participants (the minimum), and one is node 4, only 2 shares are produced — below threshold.
8. The signing fails, the presignature is wasted, and the user's request times out. [5](#0-4)

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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L103-108)
```rust
        let response_payload = self
            .execute_foreign_chain_request(
                &foreign_tx_request.request,
                foreign_tx_request.payload_version,
            )
            .await?;
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L131-136)
```rust
            dtos::ForeignChainRpcRequest::Bitcoin(request) => {
                let inspector = self
                    .inspectors
                    .bitcoin
                    .as_ref()
                    .context("no inspector configured for bitcoin")?;
```

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
