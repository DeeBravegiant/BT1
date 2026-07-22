Let me do a final targeted search to confirm the blockifier does not enforce a minimum gas price, and check the `perform_pre_validation_stage` for any resource bounds enforcement.

### Title
Gateway Stateless Validation Bypassed on Consensus Path: Oversized Transactions and Below-Minimum Gas Price Accepted by Validators — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The gateway enforces stateless validation checks — calldata/signature/proof size limits, minimum L2 gas price, Sierra version, and class length — on every transaction submitted via the RPC/HTTP path. Transactions arriving through the consensus/P2P path in `handle_proposal_part` bypass all of these checks. A malicious proposer can craft transactions that violate any of these invariants, include them in a proposal, and every honest validator will execute them without any stateless validation.

---

### Finding Description

**Enforced path — gateway (`crates/apollo_gateway/src/stateless_transaction_validator.rs`):**

`StatelessTransactionValidator::validate()` runs the following checks on every `RpcTransaction` before it enters the mempool:

| Check | Limit |
|---|---|
| `validate_tx_extended_calldata_size` | calldata + proof_facts ≤ 5 000 felts |
| `validate_tx_signature_size` | signature ≤ 4 000 felts |
| `validate_proof_size` | proof ≤ 480 000 bytes |
| `validate_resource_bounds` | `l2_gas.max_price_per_unit ≥ min_gas_price` (8 000 000 000) |
| `validate_resource_bounds` | `l2_gas.max_amount ≤ max_l2_gas_amount` (1 210 000 000) |
| `validate_declare_tx` | Sierra version in `[min, max]`, class ≤ 81 920 felts, entry-points sorted | [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

**Unenforced path — consensus validator (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`):**

When a validator receives a `ProposalPart::Transactions` batch from the network, `handle_proposal_part` calls `convert_consensus_tx_to_internal_consensus_tx` for each transaction. That converter only computes the transaction hash and optionally spawns a proof-verification task. It never calls `StatelessTransactionValidator::validate()`. [5](#0-4) [6](#0-5) 

The `convert_rpc_tx_to_internal` helper that backs the consensus conversion path computes only the hash and extracts proof data; no size or resource-bounds check is present: [7](#0-6) 

**Blockifier does not close the gap for size limits:**

The blockifier's `perform_pre_validation_stage` checks `max_price_per_unit ≥ actual_block_gas_price` (dynamic) and `max_amount ≥ minimal_gas_amount`, but it has no check for calldata length, signature length, proof size, Sierra version, or class length. The gateway's `min_gas_price` (8 000 000 000) is a static floor that is strictly higher than the dynamic block gas price in many operating conditions; the blockifier only enforces the latter. [8](#0-7) [9](#0-8) 

The existing TODO comment in `handle_proposal_part` itself acknowledges the gap: [10](#0-9) 

---

### Impact Explanation

**Calldata / signature / proof size bypass (direct analog to the external bug):**  
A malicious proposer can stream `TransactionBatch` messages containing invoke transactions with calldata > 5 000 felts or signatures > 4 000 felts, or declare transactions with Sierra programs > 81 920 felts. Every honest validator executes them via the batcher without any size check. The resulting block commitment covers these oversized transactions. Because the OS/prover enforces its own limits at proving time, a block that passes consensus may later fail to prove, breaking liveness for that height.

**Below-minimum gas price bypass:**  
The gateway rejects any transaction whose `l2_gas.max_price_per_unit < 8 000 000 000`. The blockifier only rejects a transaction if `max_price_per_unit < actual_block_gas_price`. When the live L2 gas price is below the gateway floor (a normal operating condition), a proposer can include transactions priced between the two thresholds. Validators execute them, the fee charged is `actual_gas_used × max_price_per_unit` — lower than the gateway would ever permit — producing an incorrect fee outcome with direct economic impact.

Both cases fall within the stated impact scope:
- **High** — cross-flow disagreement: the consensus path accepts transactions that the gateway/mempool admission path would reject.
- **Critical** — incorrect fee with economic impact (gas price bypass).

---

### Likelihood Explanation

In a decentralized sequencer any staked node can become the proposer for a given height. No special privilege is required beyond being selected as proposer. The attacker constructs a well-formed `ProposalPart::Transactions` message containing crafted transactions and injects it into the consensus stream. Honest validators process it through `handle_proposal_part` with no additional gate.

---

### Recommendation

Apply the equivalent of `StatelessTransactionValidator::validate()` to every `ConsensusTransaction::RpcTransaction` received in `handle_proposal_part` before forwarding it to the batcher. Concretely:

1. Expose a `validate_consensus_tx` method (or reuse the existing `StatelessTransactionValidator`) that accepts an `RpcTransaction` and runs at minimum `validate_tx_size`, `validate_resource_bounds`, and `validate_declare_tx`.
2. Call it inside the `Some(ProposalPart::Transactions(...))` arm of `handle_proposal_part`, after the `convert_consensus_tx_to_internal_consensus_tx` call, and return `HandledProposalPart::Failed` on any violation.
3. Ensure the size limits used are sourced from the same versioned-constants / config as the gateway to keep the two paths in lockstep.

---

### Proof of Concept

```
// Attacker is the proposer for block N.
// Step 1 – craft an oversized invoke transaction (calldata = 10 000 felts, well above the
//           gateway limit of 5 000).
let oversized_tx = RpcTransaction::Invoke(RpcInvokeTransaction::V3(RpcInvokeTransactionV3 {
    calldata: Calldata(vec![Felt::ONE; 10_000]),
    // valid nonce, signature, resource_bounds, etc.
    ..
}));

// Step 2 – build a proposal that contains this transaction directly, bypassing the gateway.
//           The proposer sends ProposalPart::Transactions over the consensus P2P channel.
let batch = ProposalPart::Transactions(TransactionBatch {
    transactions: vec![ConsensusTransaction::RpcTransaction(oversized_tx)],
});

// Step 3 – honest validators receive the batch in handle_proposal_part().
//           convert_consensus_tx_to_internal_consensus_tx() is called; it only computes
//           the tx hash — no validate_tx_size() is invoked.
//           The batcher executes the transaction; the blockifier has no calldata-length check.

// Step 4 – the block is committed with the oversized transaction included.
//           The gateway would have rejected this transaction at admission time.
```

For the gas-price variant, replace the oversized calldata with `l2_gas.max_price_per_unit = actual_block_gas_price` (e.g. 1 000 000 000), which is below the gateway's `min_gas_price` of 8 000 000 000 but passes the blockifier's `check_fee_bounds`. The transaction is executed and the fee charged is `actual_gas_used × 1 000 000 000` — a fraction of what the gateway would require.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-54)
```rust
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L142-150)
```rust
    fn validate_tx_size(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        self.validate_tx_extended_calldata_size(tx)?;
        self.validate_tx_signature_size(tx)?;
        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_proof_size(invoke_tx)?;
        }

        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L598-647)
```rust
        Some(ProposalPart::Transactions(TransactionBatch { transactions: txs })) => {
            // TODO(guyn): check that the length of txs and the number of batches we receive is not
            // so big it would fill up the memory (in case of a malicious proposal)
            debug!("Received transaction batch with {} txs", txs.len());
            let conversion_results =
                futures::future::join_all(txs.into_iter().map(|tx| {
                    transaction_converter.convert_consensus_tx_to_internal_consensus_tx(tx)
                }))
                .await
                .into_iter()
                .collect::<Result<Vec<_>, _>>();
            let conversion_results = match conversion_results {
                Ok(results) => results,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to convert transactions. Stopping the build of the current \
                         proposal. {e:?}"
                    ));
                }
            };

            // Separate internal transactions from verification and store proof tasks. Each task
            // verifies the proof and stores it in the proof manager. Tasks are collected
            // and awaited later in the fin case.
            let (txs, tasks): (
                Vec<InternalConsensusTransaction>,
                Vec<Option<VerifyAndStoreProofTask>>,
            ) = conversion_results.into_iter().unzip();
            verify_and_store_proof_tasks.extend(tasks.into_iter().flatten());

            debug!(
                "Converted transactions to internal representation. hashes={:?}",
                txs.iter().map(|tx| tx.tx_hash()).collect::<Vec<TransactionHash>>()
            );

            content.push(txs.clone());
            let input = SendTxsForProposalInput { proposal_id, txs };
            let response = match batcher.send_txs_for_proposal(input).await {
                Ok(response) => response,
                Err(e) => {
                    return HandledProposalPart::Failed(format!(
                        "Failed to send transactions to batcher: {e:?}"
                    ));
                }
            };
            match response {
                SendTxsForProposalStatus::Processing => HandledProposalPart::Continue,
                SendTxsForProposalStatus::InvalidProposal(err) => HandledProposalPart::Invalid(err),
            }
        }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L184-202)
```rust
    async fn convert_consensus_tx_to_internal_consensus_tx(
        &self,
        tx: ConsensusTransaction,
    ) -> TransactionConverterResult<(InternalConsensusTransaction, Option<VerifyAndStoreProofTask>)>
    {
        match tx {
            ConsensusTransaction::RpcTransaction(tx) => {
                let (internal_tx, proof_data) = self.convert_rpc_tx_to_internal(tx).await?;
                let task = proof_data.map(|(proof_facts, proof)| {
                    self.spawn_verify_and_store_proof(proof_facts, proof)
                });
                Ok((InternalConsensusTransaction::RpcTransaction(internal_tx), task))
            }
            ConsensusTransaction::L1Handler(tx) => {
                let internal_tx = self.convert_consensus_l1_handler_to_internal_l1_handler(tx)?;
                Ok((InternalConsensusTransaction::L1Handler(internal_tx), None))
            }
        }
    }
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L334-393)
```rust
    async fn convert_rpc_tx_to_internal(
        &self,
        tx: RpcTransaction,
    ) -> TransactionConverterResult<(InternalRpcTransaction, Option<(ProofFacts, Proof)>)> {
        let (tx_without_hash, proof_data) = match tx {
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                let proof_data = if tx.proof_facts.is_empty() {
                    None
                } else {
                    Some((tx.proof_facts.clone(), tx.proof.clone()))
                };
                (InternalRpcTransactionWithoutTxHash::Invoke(tx.into()), proof_data)
            }
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
                (
                    InternalRpcTransactionWithoutTxHash::Declare(InternalRpcDeclareTransactionV3 {
                        sender_address: tx.sender_address,
                        compiled_class_hash: tx.compiled_class_hash,
                        signature: tx.signature,
                        nonce: tx.nonce,
                        class_hash,
                        resource_bounds: tx.resource_bounds,
                        tip: tx.tip,
                        paymaster_data: tx.paymaster_data,
                        account_deployment_data: tx.account_deployment_data,
                        nonce_data_availability_mode: tx.nonce_data_availability_mode,
                        fee_data_availability_mode: tx.fee_data_availability_mode,
                    }),
                    None,
                )
            }
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                let contract_address = tx.calculate_contract_address()?;
                (
                    InternalRpcTransactionWithoutTxHash::DeployAccount(
                        InternalRpcDeployAccountTransaction {
                            tx: RpcDeployAccountTransaction::V3(tx),
                            contract_address,
                        },
                    ),
                    None,
                )
            }
        };
        let tx_hash = tx_without_hash.calculate_transaction_hash(&self.chain_id)?;
        Ok((InternalRpcTransaction { tx: tx_without_hash, tx_hash }, proof_data))
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L355-372)
```rust
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L374-458)
```rust
    fn check_fee_bounds(
        &self,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let minimal_gas_amount_vector = estimate_minimal_gas_vector(
            &tx_context.block_context,
            self,
            &tx_context.get_gas_vector_computation_mode(),
        );
        let TransactionContext { block_context, tx_info } = tx_context;
        let block_info = &block_context.block_info;
        let fee_type = &tx_info.fee_type();
        match tx_info {
            TransactionInfo::Current(context) => {
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
                    ValidResourceBounds::AllResources(AllResourceBounds {
                        l1_gas: l1_gas_resource_bounds,
                        l2_gas: l2_gas_resource_bounds,
                        l1_data_gas: l1_data_gas_resource_bounds,
                    }) => {
                        let GasPriceVector { l1_gas_price, l1_data_gas_price, l2_gas_price } =
                            block_info.gas_prices.gas_price_vector(fee_type);
                        vec![
                            (
                                L1Gas,
                                l1_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_gas,
                                *l1_gas_price,
                            ),
                            (
                                L1DataGas,
                                l1_data_gas_resource_bounds,
                                minimal_gas_amount_vector.l1_data_gas,
                                *l1_data_gas_price,
                            ),
                            (
                                L2Gas,
                                l2_gas_resource_bounds,
                                minimal_gas_amount_vector.l2_gas,
                                *l2_gas_price,
                            ),
                        ]
                    }
                };
                let insufficiencies = resources_amount_tuple
                    .iter()
                    .flat_map(
                        |(resource, resource_bounds, minimal_gas_amount, actual_gas_price)| {
                            let mut insufficiencies_resource = vec![];
                            if minimal_gas_amount > &resource_bounds.max_amount {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasAmountTooLow {
                                        resource: *resource,
                                        max_gas_amount: resource_bounds.max_amount,
                                        minimal_gas_amount: *minimal_gas_amount,
                                    },
                                );
                            }
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
                            insufficiencies_resource
                        },
                    )
                    .collect::<Vec<_>>();
                if !insufficiencies.is_empty() {
                    return Err(Box::new(TransactionFeeError::InsufficientResourceBounds {
                        errors: insufficiencies,
                    }))?;
                }
```
