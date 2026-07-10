### Title
Missing Foreign-Chain Support Re-Check in `respond_verify_foreign_tx` Allows Settlement of Requests for Chains No Longer in the Supported Set - (File: `crates/contract/src/lib.rs`)

---

### Summary

`verify_foreign_transaction` validates that the requested foreign chain is in the supported set at request-creation time, but `respond_verify_foreign_tx` never re-validates this at settlement time. Because the supported-chain set is computed dynamically and can shrink between request creation and settlement (e.g., after a resharing that introduces a new participant who has not yet registered any chains), the contract can accept a `respond_verify_foreign_tx` call for a chain that is no longer supported, or pending requests for the removed chain will silently time out, causing a temporary request-lifecycle DoS.

---

### Finding Description

**At request-creation time**, `verify_foreign_transaction` enforces two layers of checks:

1. `check_request_preconditions` — domain existence, gas, deposit, and the `accept_requests` flag.
2. An explicit foreign-chain membership check:

```rust
// crates/contract/src/lib.rs lines 533–542
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
``` [1](#0-0) 

**At settlement time**, `respond_verify_foreign_tx` re-checks the `accept_requests` flag and protocol state, but **never re-checks whether the foreign chain is still in the supported set**:

```rust
// crates/contract/src/lib.rs lines 692–754
pub fn respond_verify_foreign_tx(
    &mut self,
    request: VerifyForeignTransactionRequest,
    response: VerifyForeignTransactionResponse,
) -> Result<(), Error> {
    // checks: attested participant, protocol state, accept_requests, signature validity
    // NO check: supported_chains.contains(&request.request.chain())
    ...
    pending_requests::resolve_yields_for(...)
}
``` [2](#0-1) 

The supported-chain set is **not static**. `get_supported_foreign_chains` computes the strict intersection of every active participant's registered chains:

```rust
// crates/contract/src/lib.rs lines 2176–2217
let all_active_nodes_supports_chain =
    nodes_supporting_chain.is_superset(&active_participant_account_ids);
``` [3](#0-2) 

This means the supported set can shrink between request creation and settlement in two realistic ways:

- **Resharing**: A `vote_new_parameters` resharing that adds a new participant who has not yet called `register_foreign_chain_support` causes the intersection to drop to the empty set, instantly invalidating all pending foreign-tx requests.
- **Node de-registration**: Any active participant calling `register_foreign_chain_support` with a reduced chain list removes that chain from the intersection.

**Node-side check does not fully close the gap.** The MPC node does call `chain_is_supported` before querying the foreign-chain RPC:

```rust
// crates/node/src/providers/verify_foreign_tx/sign.rs line 122
chain_is_supported(&self.foreign_chain_policy_reader, request).await?;
``` [4](#0-3) 

However, this check is a live view-call to the contract. If the chain is removed from the supported set **after** the node has passed this check but **before** it submits `respond_verify_foreign_tx`, the contract will accept the response without re-validating chain support. This is a real, if narrow, race window.

---

### Impact Explanation

**Primary (Medium) — Request-lifecycle DoS**: When a resharing or node de-registration removes a chain from the supported set, all pending `verify_foreign_transaction` requests for that chain become unprocessable. Nodes will fail the `chain_is_supported` check and produce no signature shares. The requests sit in the pending map until the 200-block yield-resume timeout fires, at which point they are cleaned up via `return_verify_foreign_tx_and_clean_state_on_success`. During this window, the contract's pending-request queue is polluted with unserviceable entries, and callers receive no response until timeout. This breaks the request-lifecycle safety invariant that a queued request will either be fulfilled or rejected promptly. [5](#0-4) 

**Secondary (race condition) — Signature produced for a de-listed chain**: In the narrow window between a node passing `chain_is_supported` and submitting `respond_verify_foreign_tx`, if the chain is removed (e.g., because its RPC providers were voted out as compromised), the contract will accept the response and deliver a threshold signature to the caller — a signature whose underlying foreign-chain data was gathered from providers that the network has since revoked. This breaks the invariant that every accepted `respond_verify_foreign_tx` corresponds to a chain the network currently trusts.

---

### Likelihood Explanation

**Moderate for DoS**: Resharings are a normal operational event (TEE attestation expiry, participant rotation). Every resharing that adds a new participant who has not yet registered chain support will transiently drop the supported-chain set to empty, stranding all in-flight foreign-tx requests until the new participant registers. This is not a rare edge case.

**Low for the race-condition path**: Requires a chain to be removed from the supported set in the narrow window between a node's `chain_is_supported` check and its `respond_verify_foreign_tx` submission. Exploiting this intentionally is difficult, but it can occur naturally during rapid governance changes.

---

### Recommendation

**Short-term**: Add a foreign-chain support re-check inside `respond_verify_foreign_tx` before calling `resolve_yields_for`. If the chain is no longer supported, return an error (do not panic, to avoid reverting the cleanup). Document the chosen policy (reject the response and let the request time out, or accept and log a warning).

**Long-term**: Consider snapshotting the supported-chain set at request-creation time (stored in the pending-request map entry) so that settlement can validate against the state that was in effect when the request was accepted, rather than the current live state. Add integration tests that cover the resharing-then-settle sequence for foreign-tx requests.

---

### Proof of Concept

1. All `N` participants register support for chain `Bitcoin`. `get_supported_foreign_chains()` returns `{Bitcoin}`.
2. User calls `verify_foreign_transaction({chain: Bitcoin, ...})`. The check at line 535 passes. The request is enqueued in `pending_verify_foreign_tx_requests`.
3. Governance calls `vote_new_parameters` to add a new participant `P_new`. The resharing completes. `P_new` has not yet called `register_foreign_chain_support`.
4. `get_supported_foreign_chains()` now returns `{}` (strict intersection with `P_new`'s empty set).
5. MPC nodes attempt to process the pending request. Each node calls `chain_is_supported`, which reads the contract's current state and returns `Err(ChainNotSupported)`. No signature shares are produced.
6. The request sits in `pending_verify_foreign_tx_requests` for up to 200 blocks (`REQUEST_EXPIRATION_BLOCKS`), then times out. The caller receives a timeout error.
7. `respond_verify_foreign_tx` is never called, so the missing re-check is not the direct trigger here — but if `P_new` registers chain support immediately after step 3 and nodes race to process the request, a node that passed `chain_is_supported` before `P_new` de-registered could still submit a valid `respond_verify_foreign_tx`. The contract accepts it without re-checking, because the settlement path has no foreign-chain support guard. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L691-754)
```rust
    #[handle_result]
    pub fn respond_verify_foreign_tx(
        &mut self,
        request: VerifyForeignTransactionRequest,
        response: VerifyForeignTransactionResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!(
            "respond_verify_foreign_tx: signer={}, request={:?}",
            &signer,
            &request
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain.0.into())?;

        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L117-123)
```rust
    async fn execute_foreign_chain_request(
        &self,
        request: &dtos::ForeignChainRpcRequest,
        payload_version: dtos::ForeignTxPayloadVersion,
    ) -> anyhow::Result<dtos::ForeignTxSignPayload> {
        chain_is_supported(&self.foreign_chain_policy_reader, request).await?;

```

**File:** crates/node/src/requests/queue.rs (L29-33)
```rust
/// the highest height of all participants.
const STALE_PARTICIPANT_THRESHOLD: NumBlocks = 10;
/// The number of blocks after which a request is assumed to have timed out.
/// This is equal to the yield-resume timeout on the blockchain.
pub const REQUEST_EXPIRATION_BLOCKS: NumBlocks = 200;
```
