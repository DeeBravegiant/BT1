### Title
Caller-Controlled `confirmations` Parameter in `verify_foreign_transaction` Bypasses Bitcoin Finality Enforcement — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint accepts a caller-supplied `confirmations` field inside `BitcoinRpcRequest` with no on-chain minimum enforcement. Any unprivileged caller can set `confirmations: 0`, causing MPC nodes to sign a verification payload for an unconfirmed (mempool) Bitcoin transaction. A bridge contract that trusts this attestation without re-checking the confirmation count is exposed to a double-spend attack.

---

### Finding Description

In `crates/contract/src/lib.rs`, the `verify_foreign_transaction` function accepts a `VerifyForeignTransactionRequestArgs` that embeds a `ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest { tx_id, confirmations, extractors })`. The `confirmations` field is the caller's stated minimum number of Bitcoin block confirmations required before the MPC network will sign the verification payload. [1](#0-0) 

The function performs five checks: domain purpose, gas, deposit (1 yoctonear), `accept_requests` flag, and supported-chain membership. **There is no on-chain validation of the `confirmations` value.** The ABI schema confirms the only constraint is `"minimum": 0.0`: [2](#0-1) 

The actual confirmation check is performed entirely off-chain inside `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`: [3](#0-2) 

The guard is `block_confirmations_threshold <= transaction_block_confirmation`. When the caller supplies `confirmations: 0`, this evaluates to `0 <= actual_confirmations`, which is trivially true for **any** transaction including unconfirmed mempool entries (where `confirmations` from `getrawtransaction` is 0). The MPC nodes then sign the canonical payload:

```
msg_hash = SHA-256(borsh(ForeignTxSignPayload::V1 { request: Bitcoin{tx_id, confirmations: 0, ...}, values: [...] }))
```

The `BitcoinRpcRequest` struct carries `confirmations` as a plain `u64` with no newtype minimum: [4](#0-3) 

The node's `execute_foreign_chain_request` passes the caller-supplied value directly as the threshold: [5](#0-4) 

The design document explicitly identifies the primary use case as bridge inbound flows where a NEAR contract must react to a finalized foreign-chain event: [6](#0-5) 

---

### Impact Explanation

A malicious caller can obtain a valid MPC threshold signature attesting to a Bitcoin transaction that has zero confirmations. If a bridge contract (e.g., Omnibridge inbound) releases NEAR-side assets upon receiving a valid `VerifyForeignTransactionResponse` without independently re-checking the `confirmations` value embedded in the signed payload, the attacker can:

1. Broadcast a Bitcoin transaction to the mempool (0 confirmations).
2. Call `verify_foreign_transaction` with `confirmations: 0` and the mempool `tx_id`.
3. MPC nodes pass the check (`0 <= 0`) and produce a valid threshold signature.
4. Attacker submits the signature to the bridge contract and receives NEAR-side funds.
5. Attacker double-spends the Bitcoin transaction (via RBF or miner cooperation).
6. Attacker retains both the NEAR funds and the Bitcoin.

This is a forged foreign-chain verification / verification bypass that directly enables double-spend conditions — matching the **High** allowed impact: *"Cross-chain replay, forged foreign-chain verification, light-client-style verification bypass … that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

**Medium.** The attack requires:
- A bridge contract that trusts the MPC attestation without re-validating the `confirmations` field in the signed payload. This is a realistic assumption: the MPC contract is the designated trust anchor, and downstream contracts are expected to rely on its attestations.
- The ability to broadcast a Bitcoin transaction and then double-spend it (possible via opt-in RBF, which is common on mainnet Bitcoin).

The attack is directly reachable by any unprivileged NEAR account that can call `verify_foreign_transaction` with a 1-yoctonear deposit. No participant collusion, TEE compromise, or privileged access is required.

---

### Recommendation

Enforce a protocol-level minimum confirmation count on-chain. Two options:

1. **Per-chain minimum in `ForeignChainPolicy`**: Extend the on-chain foreign-chain configuration to include a `min_confirmations` field per chain. In `verify_foreign_transaction`, reject any request whose `confirmations` is below the on-chain minimum before enqueuing the yield.

2. **Hard-coded floor**: Add a constant `MIN_BITCOIN_CONFIRMATIONS` (e.g., 1 or 6) and panic in `verify_foreign_transaction` if `request.confirmations < MIN_BITCOIN_CONFIRMATIONS`.

Either approach mirrors the recommendation in the external report: fix the critical parameter (confirmation threshold) to be set by the protocol owner rather than left entirely to the caller.

---

### Proof of Concept

```rust
// Attacker calls verify_foreign_transaction with confirmations: 0
let request_args = VerifyForeignTransactionRequestArgs {
    domain_id: foreign_tx_domain_id,
    payload_version: ForeignTxPayloadVersion::V1,
    request: ForeignChainRpcRequest::Bitcoin(BitcoinRpcRequest {
        tx_id: mempool_tx_id,   // unconfirmed mempool transaction
        confirmations: 0.into(), // <-- no on-chain check rejects this
        extractors: vec![BitcoinExtractor::BlockHash],
    }),
};
// Contract accepts it; MPC nodes evaluate: 0 <= 0 → true → sign
// Attacker receives valid threshold signature over unconfirmed tx
// Attacker claims bridge funds, then double-spends the Bitcoin tx
```

The off-chain guard in `BitcoinInspector::extract` (`block_confirmations_threshold <= transaction_block_confirmation`) evaluates to `0 <= 0 = true` for any mempool transaction, so every node independently produces the same result and the threshold signature is assembled normally. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L514-557)
```rust
    /// Submit a verification + signing request for a foreign chain transaction.
    /// MPC nodes will verify the transaction on the foreign chain before signing.
    /// The signed payload is derived from the transaction ID (hash of tx_id).
    #[handle_result]
    #[payable]
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

**File:** crates/contract/tests/snapshots/abi__abi_has_not_changed.snap (L2516-2520)
```text
        "BlockConfirmations": {
          "type": "integer",
          "format": "uint64",
          "minimum": 0.0
        },
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L50-59)
```rust
        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
        }
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L267-271)
```rust
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,
    pub extractors: Vec<BitcoinExtractor>,
}
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L137-149)
```rust
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
```

**File:** docs/foreign-chain-transactions.md (L7-10)
```markdown
This feature lets the MPC network sign payloads only after verifying a specific foreign-chain transaction, so NEAR contracts can react to external chain events without a trusted relayer. Primary use cases:

* Omnibridge inbound flow (foreign chain -> NEAR) where Chain Signatures are required to attest that a foreign transaction finalized successfully.
* Broader chain abstraction: a single MPC network verifies foreign chain state and returns small, typed observations that contracts can interpret.
```
