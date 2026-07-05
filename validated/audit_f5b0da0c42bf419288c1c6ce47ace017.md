### Title
Dijkstra-Era Sub-Transaction Plutus Execution Units Excluded from Batch Fee Calculation and `maxTxExUnits` Enforcement — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

In the Dijkstra era, a top-level transaction can embed multiple sub-transactions via the `sub_transactions` field. The minimum fee calculation and the `maxTxExUnits` enforcement both operate exclusively on the top-level transaction's execution units, ignoring Plutus script execution units declared in sub-transactions. An unprivileged transaction submitter can craft a batch whose sub-transactions collectively execute far more Plutus computation than `maxTxExUnits` permits, while paying a fee that reflects only the top-level transaction's (potentially zero) execution units.

---

### Finding Description

**Root cause — `validateExUnitsTooBigUTxO` only checks the top-level transaction:**

In the Dijkstra UTxO transition rule, the execution-unit guard is:

```
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
``` [1](#0-0) 

`validateExUnitsTooBigUTxO` delegates to `totExUnits tx`:

```haskell
totalExUnits = totExUnits tx
``` [2](#0-1) 

`totExUnits` is defined as:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
``` [3](#0-2) 

`tx ^. witsTxL` resolves to the top-level transaction's `dtWits` field only. Sub-transactions carry their own independent witness sets (`dstWits`) that are never traversed by this path.

**Root cause — fee calculation also ignores sub-transaction execution units:**

`alonzoMinFeeTx` (used by Dijkstra via `getMinFeeTx = getConwayMinFeeTx`) computes:

```haskell
alonzoMinFeeTx pp tx =
  (tx ^. sizeTxF <×> ...) <+> (pp ^. ppTxFeeFixedL)
    <+> txscriptfee (pp ^. ppPricesL) allExunits
  where
    allExunits = totExUnits tx   -- top-level only
``` [4](#0-3) 

The `txscriptfee` component — the per-execution-unit charge — is computed from `totExUnits tx`, which, as shown above, excludes all sub-transaction redeemers.

**Sub-transactions can contain Plutus scripts:**

`DijkstraStAnnSubTx` carries `dsastPlutusScriptsWithContext`, confirming sub-transactions are fully capable of running Plutus scripts: [5](#0-4) 

The sub-transaction body type `DijkstraSubTxBodyRaw` includes `dstbrScriptIntegrityHash`, `dstbrGuards`, and redeemer-bearing witness sets, confirming Plutus execution is possible inside sub-transactions: [6](#0-5) 

**Contrast with the correctly implemented reference-script batch check:**

The Dijkstra ledger rule correctly aggregates reference-script sizes across the entire batch via `batchNonDistinctRefScriptsSize`:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
        (tx ^. bodyTxL . subTransactionsTxBodyL))
``` [7](#0-6) 

No analogous `batchTotExUnits` function exists. The execution-unit dimension of the batch is entirely unaccounted for in both the limit check and the fee.

**Collateral check is batch-aware but execution-unit check is not:**

`validateBatchCollateral` correctly detects redeemers in any sub-transaction and enforces collateral for the whole batch: [8](#0-7) 

This demonstrates the design intent is batch-aware validation — but the execution-unit limit and fee were not extended to match.

---

### Impact Explanation

**Medium.** An attacker-controlled transaction causes the batch to exceed the intended `maxTxExUnits` validation limit and pays a fee that is outside the design parameters set by `ppPricesL` and `ppMaxTxExUnitsL`. Specifically:

- The `maxTxExUnits` protocol parameter is the primary mechanism for bounding per-transaction Plutus computation cost. A batch with N sub-transactions each carrying near-`maxTxExUnits` execution units imposes N × `maxTxExUnits` of Plutus evaluation work on every validating node, while the top-level fee covers at most one unit of `maxTxExUnits` worth of script fees.
- The fee paid is structurally lower than what the protocol parameters prescribe for the actual computation performed, constituting a fee modification outside design parameters.

This maps to the allowed Medium impact: *"Attacker-controlled transactions… exceed intended validation limits or modify fees… outside design parameters."*

---

### Likelihood Explanation

**Medium.** The Dijkstra era is the current development frontier and sub-transactions are a first-class feature. Any transaction submitter (no privilege required) can construct a top-level transaction with an `OMap` of sub-transactions. The only natural bound is `maxTxSize`, which limits total serialized bytes but does not bound execution units — a compact Plutus script can declare arbitrarily large execution units up to `maxTxExUnits` per sub-transaction. The attack requires no key compromise, no governance majority, and no Sybil capability.

---

### Recommendation

1. **Short term:** Introduce a `batchTotExUnits` function analogous to `batchNonDistinctRefScriptsSize` that sums execution units across the top-level transaction and all sub-transactions. Add a validation check `batchTotExUnits tx ≤ maxTxExUnits pp` in the Dijkstra UTxO rule, replacing or supplementing the current top-level-only check.

2. **Short term:** Update `alonzoMinFeeTx` (or introduce a Dijkstra-specific override of `getMinFeeTx`) to include sub-transaction execution units in the `txscriptfee` component.

3. **Long term:** Add property-based tests asserting that the minimum fee for a batch transaction scales with the total execution units across all sub-transactions, and that no batch can pass `validateExUnitsTooBigUTxO` with a total execution unit count exceeding `maxTxExUnits`.

---

### Proof of Concept

1. Construct a Dijkstra top-level transaction with zero Plutus scripts in its own body (so `totExUnits topTx = 0`).
2. Embed N sub-transactions in `subTransactionsTxBodyL`, each containing a Plutus script with execution units = `maxTxExUnits - 1`.
3. Set the top-level fee to the minimum: `txSize * feePerByte + fixedFee + txscriptfee prices 0` (no script fee component).
4. Submit the transaction.
5. **Expected (correct) behavior:** Rejected because total batch execution units = N × (`maxTxExUnits` − 1) >> `maxTxExUnits`.
6. **Actual behavior:** `validateExUnitsTooBigUTxO pp tx` passes (top-level units = 0 ≤ `maxTxExUnits`); `validateFeeTooSmallUTxO` passes (fee covers zero execution units); the batch is accepted. Each validating node must evaluate N × (`maxTxExUnits` − 1) execution units while the submitter paid for zero.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L282-300)
```haskell
-- | Validate collateral if any transaction in the batch has redeemers.
validateBatchCollateral ::
  forall era rule.
  ( AlonzoEraTx era
  , DijkstraEraTxBody era
  , InjectRuleFailure rule Alonzo.AlonzoUtxoPredFailure era
  , InjectRuleFailure rule Babbage.BabbageUtxoPredFailure era
  ) =>
  PParams era ->
  Tx TopTx era ->
  UTxO era ->
  Test (EraRuleFailure rule era)
validateBatchCollateral pp tx (UTxO utxo) =
  -- TODO OPTIMIZATION: Rewrite in a way that doesn't require this check when rules are executed without validation
  when (hasAnyRedeemers tx) $
    Babbage.validateTotalCollateral pp (tx ^. bodyTxL) utxoCollateral
  where
    utxoCollateral = Map.restrictKeys utxo (tx ^. bodyTxL . collateralInputsTxBodyL)
    hasAnyRedeemers t =
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Utxo.hs (L459-465)
```haskell
validateExUnitsTooBigUTxO pp tx =
  failureUnless (pointWiseExUnits (<=) totalExUnits maxTxExUnits) $
    ExUnitsTooBigUTxO Mismatch {mismatchSupplied = totalExUnits, mismatchExpected = maxTxExUnits}
  where
    maxTxExUnits = pp ^. ppMaxTxExUnitsL
    -- This sums up the ExUnits for all embedded Plutus Scripts anywhere in the transaction:
    totalExUnits = totExUnits tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L375-388)
```haskell
alonzoMinFeeTx ::
  ( EraTx era
  , AlonzoEraTxWits era
  , AlonzoEraPParams era
  ) =>
  PParams era ->
  Tx l era ->
  Coin
alonzoMinFeeTx pp tx =
  (tx ^. sizeTxF <×> (fromCompact . unCoinPerByte) (pp ^. ppTxFeePerByteL))
    <+> (pp ^. ppTxFeeFixedL)
    <+> txscriptfee (pp ^. ppPricesL) allExunits
  where
    allExunits = totExUnits tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Tx.hs (L383-390)
```haskell
  DijkstraStAnnSubTx ::
    { dsastTx :: !(Tx SubTx era)
    , dsastScriptsNeeded :: ScriptsNeeded era
    , dsastScriptsProvided :: ScriptsProvided era
    , dsastTxInfoResult :: TxInfoResult era
    , dsastPlutusLanguagesUsed :: Set Language
    , dsastPlutusScriptsWithContext :: Either (NonEmpty (CollectError era)) [PlutusWithContext]
    } ->
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L189-208)
```haskell
  DijkstraSubTxBodyRaw ::
    { dstbrSpendInputs :: !(Set TxIn)
    , dstbrReferenceInputs :: !(Set TxIn)
    , dstbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dstbrCerts :: !(OSet.OSet (TxCert era))
    , dstbrWithdrawals :: !Withdrawals
    , dstbrVldt :: !ValidityInterval
    , dstbrGuards :: !(OSet (Credential Guard))
    , dstbrMint :: !MultiAsset
    , dstbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dstbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dstbrNetworkId :: !(StrictMaybe Network)
    , dstbrVotingProcedures :: !(VotingProcedures era)
    , dstbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dstbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dstbrTreasuryDonation :: !Coin
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L263-277)
```haskell
-- | Total size of reference scripts across a top-level transaction and all its subtransactions.
batchNonDistinctRefScriptsSize ::
  ( EraTx era
  , DijkstraEraTxBody era
  ) =>
  UTxO era ->
  Tx TopTx era ->
  Int
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```
