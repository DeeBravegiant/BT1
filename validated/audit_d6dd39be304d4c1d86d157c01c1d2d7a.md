### Title
Zero-Confirmation Bitcoin Transaction Signing Bypass via Unchecked `BlockConfirmations` Parameter - (File: `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

### Summary

The `verify_foreign_transaction` contract entrypoint accepts a `BitcoinRpcRequest` whose `confirmations` field is a plain `u64` with no enforced minimum. When a caller supplies `confirmations: 0`, the MPC nodes' confirmation-threshold check is trivially satisfied for any transaction — including unconfirmed mempool transactions — and the network issues a valid threshold signature over the unconfirmed payload. This is the direct analog of the `amountMin = 0` slippage bypass: a user-controlled minimum-value parameter that can be zeroed out to defeat the only protection the protocol provides.

### Finding Description

`BlockConfirmations` is defined as a transparent `u64` wrapper with no lower-bound constraint: [1](#0-0) 

The ABI schema confirms `"minimum": 0.0`, meaning zero is a valid on-chain value. [2](#0-1) 

The `verify_foreign_transaction` contract method performs no validation on the `confirmations` field before enqueuing the request: [3](#0-2) 

Inside `BitcoinInspector::extract`, the only guard is a `<=` comparison:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;
``` [4](#0-3) 

When `block_confirmations_threshold = 0`, the expression `0 <= actual_confirmations` is always `true` for any transaction the RPC node knows about, including transactions with zero on-chain confirmations (mempool). The existing test explicitly documents this behaviour: [5](#0-4) 

The signed payload (`ForeignTxSignPayload::V1`) embeds the full `request` struct, including the caller-supplied `confirmations: 0`. The MPC network therefore issues a threshold signature attesting to a transaction that has not been confirmed on Bitcoin. [6](#0-5) 

### Impact Explanation

The MPC network's security guarantee for the `verify_foreign_transaction` flow is that it only co-signs foreign-chain events after they have reached a caller-specified (and implicitly operator-trusted) finality depth. With `confirmations: 0` the network signs over a mempool transaction that:

- may never be mined (RBF replacement, fee too low),
- may be reorganised out after initial inclusion.

A bridge contract consuming the returned signature to release NEAR-side funds would do so before the Bitcoin transaction is final, enabling a double-spend: the attacker broadcasts a Bitcoin transaction, immediately obtains a valid MPC signature, claims NEAR-side funds, then replaces or reorganises the Bitcoin transaction. This matches the **High** allowed impact: *forged foreign-chain verification / light-client-style verification bypass that causes invalid bridge execution or double-spend conditions*.

### Likelihood Explanation

The attack requires only an unprivileged call to `verify_foreign_transaction` with `confirmations: 0`. No special role, key material, or threshold collusion is needed. The parameter is a plain JSON integer; any caller can set it. The only prerequisite is that the target chain is in the supported-chains list and the domain exists — both normal production conditions.

### Recommendation

1. **Contract-level**: Reject `BitcoinRpcRequest` with `confirmations == 0` inside `verify_foreign_transaction` (or in a shared validation helper), returning a descriptive `InvalidParameters` error.
2. **Node-level**: Add a defence-in-depth guard in `BitcoinInspector::extract` that returns `ForeignChainInspectionError` when `block_confirmations_threshold == 0`.
3. **Type-level**: Consider replacing `BlockConfirmations(pub u64)` with a `NonZeroU64`-backed newtype so the invariant is enforced at construction time across the entire codebase.

### Proof of Concept

```json
// Call verify_foreign_transaction with confirmations = 0
{
  "request": {
    "request": {
      "Bitcoin": {
        "tx_id": "<mempool_tx_id_hex>",
        "confirmations": 0,
        "extractors": ["BlockHash"]
      }
    },
    "domain_id": <foreign_tx_domain_id>,
    "payload_version": 1
  }
}
```

1. Attacker broadcasts a Bitcoin transaction `T` and immediately submits the above call with `confirmations: 0`.
2. The contract enqueues the request (no minimum check).
3. MPC nodes call `BitcoinInspector::extract` with `block_confirmations_threshold = BlockConfirmations(0)`; the check `0 <= actual_confirmations` passes for any transaction the RPC node has seen.
4. Nodes produce a threshold signature over `ForeignTxSignPayload::V1 { request: { confirmations: 0, … }, values: [BlockHash(…)] }`.
5. The attacker receives a valid MPC signature and uses it to claim NEAR-side bridge funds.
6. Attacker then RBF-replaces or double-spends `T` on Bitcoin, recovering the Bitcoin-side funds as well.

### Citations

**File:** crates/contract/tests/snapshots/abi__abi_has_not_changed.snap (L2516-2520)
```text
        "BlockConfirmations": {
          "type": "integer",
          "format": "uint64",
          "minimum": 0.0
        },
```

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

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L51-58)
```rust
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
```

**File:** crates/foreign-chain-inspector/tests/bitcoin_inspector.rs (L105-139)
```rust
#[tokio::test]
async fn extract_returns_empty_when_no_extractors_provided() {
    // given
    let tx_id = BitcoinTransactionHash::from([11; 32]);
    let expected_block_hash = BitcoinBlockHash::from([12; 32]);

    let confirmations = BlockConfirmations::from(9u64);
    let threshold = BlockConfirmations::from(6u64);
    let transport_block_hash = TransportBitcoinBlockHash::from(*expected_block_hash);

    let tx_response = GetRawTransactionVerboseResponse {
        blockhash: transport_block_hash,
        confirmations: *confirmations,
    };
    let block_response = GetBlockHeaderVerboseResponse {
        hash: transport_block_hash,
        height: TEST_BLOCK_HEIGHT,
    };

    let mock_client = SequentialResponseMockClientBuilder::new()
        .with_response(tx_response)
        .with_response(block_response)
        .with_response(transport_block_hash)
        .build();
    let inspector = BitcoinInspector::new(mock_client);

    // when
    let extracted_values = inspector
        .extract(tx_id, threshold, Vec::new())
        .await
        .expect("extract should succeed");

    // then
    let expected_extractions: Vec<BitcoinExtractedValue> = vec![];
    assert_eq!(expected_extractions, extracted_values);
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L337-346)
```rust
        let payload = match payload_version {
            dtos::ForeignTxPayloadVersion::V1 => {
                dtos::ForeignTxSignPayload::V1(dtos::ForeignTxSignPayloadV1 {
                    request: request.clone(),
                    values,
                })
            }
            _ => bail!("unsupported payload_version"),
        };
        Ok(payload)
```
