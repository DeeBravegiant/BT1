### Title
Unconstrained `BlockConfirmations` in `BitcoinRpcRequest` Allows Attacker to Bypass Bitcoin Finality Check and Obtain MPC Signature on Unconfirmed Transactions - (File: `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

### Summary

The `BlockConfirmations` field inside `BitcoinRpcRequest` is a plain `u64` wrapper supplied entirely by the caller. Neither the NEAR smart contract (`verify_foreign_transaction`) nor the MPC node validates that this value meets any minimum. An unprivileged caller can set `confirmations: 0`, causing the MPC network to sign a Bitcoin transaction that has zero on-chain confirmations, enabling a double-spend or bridge-drain attack.

### Finding Description

`BlockConfirmations` is defined as a transparent `u64` newtype with no lower-bound constraint: [1](#0-0) 

The `BitcoinRpcRequest` struct embeds it as a plain public field:

```rust
pub struct BitcoinRpcRequest {
    pub tx_id: BitcoinTxId,
    pub confirmations: BlockConfirmations,  // user-controlled, no minimum
    pub extractors: Vec<BitcoinExtractor>,
}
```

The contract's `verify_foreign_transaction` entry point validates the domain, chain support, gas, and deposit — but performs **no validation** on the `confirmations` value before storing the request: [2](#0-1) 

The request is passed through `args_into_verify_foreign_tx_request` without any field-level sanitization: [3](#0-2) 

On the node side, `BitcoinInspector::extract` uses the caller-supplied value directly as the finality threshold: [4](#0-3) 

The guard is:

```rust
let enough_block_confirmations =
    block_confirmations_threshold <= transaction_block_confirmation;
```

When `block_confirmations_threshold` is `0`, the inequality `0 <= any_u64` is unconditionally `true`. The check is completely bypassed and the MPC proceeds to sign.

### Impact Explanation

An attacker who sets `confirmations: BlockConfirmations(0)` in a `BitcoinRpcRequest` causes every MPC node to treat any Bitcoin transaction — including a freshly broadcast, unconfirmed mempool transaction — as finalized. The MPC network then collectively produces a valid threshold signature over the `ForeignTxSignPayload` that attests to the transaction's finality. A bridge contract that trusts this signature will release funds on the NEAR side while the underlying Bitcoin transaction is still reversible. The attacker can then double-spend the Bitcoin input, keeping both the NEAR-side payout and the Bitcoin.

This matches the allowed impact: **High — forged foreign-chain verification / light-client-style verification bypass that causes invalid bridge execution or double-spend conditions.**

### Likelihood Explanation

The attack requires only a standard NEAR account and a 1 yoctoNEAR deposit. The `verify_foreign_transaction` method is a public, permissionless entry point. No privileged role, key leak, or collusion is needed. The attacker simply crafts a `BitcoinRpcRequest` with `confirmations: 0` and a real (but unconfirmed) Bitcoin transaction ID. All honest MPC nodes will independently reach the same conclusion (the check passes for all of them) and co-sign the payload.

### Recommendation

Enforce a protocol-level minimum on `BlockConfirmations` at the contract boundary before the request is stored. For example:

```rust
const MINIMUM_BITCOIN_CONFIRMATIONS: u64 = 6;

pub fn verify_foreign_transaction(&mut self, request: VerifyForeignTransactionRequestArgs) {
    // ... existing checks ...
    if let ForeignChainRpcRequest::Bitcoin(ref btc) = request.request {
        if btc.confirmations.0 < MINIMUM_BITCOIN_CONFIRMATIONS {
            env::panic_str("Bitcoin confirmations below minimum required");
        }
    }
    // ...
}
```

Alternatively, make `BlockConfirmations` a validated newtype whose constructor rejects values below the minimum, mirroring how `ThresholdParameters` is validated at the DTO boundary. [5](#0-4) 

### Proof of Concept

1. Attacker broadcasts a Bitcoin transaction `TX` spending input `I` (double-spend candidate).
2. Attacker calls `verify_foreign_transaction` on the NEAR MPC contract with:
   ```json
   {
     "request": {
       "request": {
         "Bitcoin": {
           "tx_id": "<TX hash>",
           "confirmations": 0,
           "extractors": [{"BlockHash": null}]
         }
       },
       "domain_id": <foreign_tx_domain_id>,
       "payload_version": 1
     }
   }
   ```
3. The contract stores the request without validating `confirmations`.
4. Each MPC node calls `BitcoinInspector::extract(TX, threshold=0, [BlockHash])`. The check `0 <= actual_confirmations` is always true regardless of `TX`'s confirmation count.
5. All nodes extract the block hash and co-sign `ForeignTxSignPayload::V1 { request, values }`.
6. The contract resolves the yield and returns a valid MPC signature to the attacker.
7. The attacker presents the signature to the bridge contract to claim NEAR-side funds.
8. Attacker simultaneously broadcasts a conflicting Bitcoin transaction spending input `I` to a different address, double-spending the original. [4](#0-3) [1](#0-0)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1282-1282)
```rust
pub struct BlockConfirmations(pub u64);
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

**File:** crates/contract/src/dto_mapping.rs (L217-224)
```rust
impl TryIntoContractType<ThresholdParameters> for dtos::ThresholdParameters {
    type Error = Error;

    fn try_into_contract_type(self) -> Result<ThresholdParameters, Self::Error> {
        // Validate eagerly at the DTO boundary so invalid proposal parameters are rejected here.
        ThresholdParameters::new(self.participants.into_contract_type(), self.threshold)
    }
}
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L50-58)
```rust
        let transaction_block_confirmation = rpc_response.confirmations.into();
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
```
