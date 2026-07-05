### Title
Unbounded Sub-Transaction Plutus ExUnits Bypass Per-Transaction Execution Limit — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`)

---

### Summary

In the Dijkstra era, sub-transactions embedded in a top-level transaction body are processed through the `SUBLEDGERS → SUBLEDGER → SUBUTXOW → SUBUTXO` rule chain with **no check on their Plutus script ExUnits**. The top-level `validateExUnitsTooBigUTxO` check covers only the top-level transaction's redeemers. An attacker can craft a transaction with many sub-transactions, each carrying Plutus scripts with large ExUnits budgets, bypassing the `maxTxExUnits` protocol parameter and paying a fee that does not cover the actual computational cost.

---

### Finding Description

The Dijkstra era introduces nested transactions. The top-level transaction body stores sub-transactions in `dtbrSubTransactions :: OMap TxId (Tx SubTx era)`. [1](#0-0) 

Each `Tx SubTx era` is a full `DijkstraSubTx` carrying its own body, witnesses, and Plutus redeemers. The annotated sub-transaction type confirms Plutus scripts are collected and prepared for execution per sub-transaction: [2](#0-1) 

**Top-level ExUnits check (only covers top-level redeemers):**

The Dijkstra UTXO transition calls `Alonzo.validateExUnitsTooBigUTxO pp tx`, which computes `totExUnits tx` by summing only the top-level transaction's redeemers (`tx ^. witsTxL . rdmrsTxWitsL`). Sub-transactions' redeemers are never included. [3](#0-2) 

**Sub-transaction processing — no ExUnits check:**

The `SUBLEDGERS` rule iterates over all sub-transactions with `foldM`, dispatching each to `SUBLEDGER`: [4](#0-3) 

`SUBLEDGER` calls `SUBUTXOW → SUBUTXO`. The `SUBUTXO` transition (`dijkstraSubUtxoTransition`) performs validity-interval, input, output, and network-ID checks, but **no ExUnits check**: [5](#0-4) 

This omission is structurally confirmed: `DijkstraSubUtxoPredFailure` has no `ExUnitsTooBigUTxO` constructor, and the mapping function explicitly marks it as impossible for sub-transactions: [6](#0-5) 

**Fee calculation does not cover sub-transaction ExUnits:**

The top-level fee is computed via `getConwayMinFeeTx`, which includes an ExUnits-based component only for the top-level redeemers. Sub-transactions' redeemers are not summed. The `sizeTxF` for Dijkstra serializes only the top-level body (which includes sub-transaction bytes, so size is bounded by `maxTxSize`), but the ExUnits fee component is zero for sub-transactions: [7](#0-6) 

The `validateFeeTooSmallUTxO` check is also explicitly marked impossible for sub-transactions: [8](#0-7) 

---

### Impact Explanation

An attacker submits a top-level transaction with `N` sub-transactions (bounded only by `maxTxSize`), each containing a Plutus script whose redeemer specifies ExUnits = `maxTxExUnits`. The top-level transaction carries no Plutus scripts (ExUnits = 0), so `validateExUnitsTooBigUTxO` passes. During block validation, nodes execute `N × maxTxExUnits` worth of Plutus computation. The fee paid covers only the byte-size of the transaction, not the script execution cost. Both the per-transaction and per-block ExUnits limits are bypassed because sub-transaction ExUnits are never counted.

This matches the **Medium** allowed impact: *"Attacker-controlled transactions… exceed intended validation limits or modify fees, deposits, refunds, rewards, treasury donations, or withdrawals outside design parameters."*

---

### Likelihood Explanation

The Dijkstra era is experimental and not yet activated on mainnet. However, the code is production-quality and present in the repository for future activation. Exploitation requires only the ability to submit a valid transaction — no privileged keys, governance majority, or external dependency. Any unprivileged transaction author can trigger this path once the era is active.

---

### Recommendation

1. **Add a per-sub-transaction ExUnits check** in `dijkstraSubUtxoTransition` (or `SUBUTXOW`) analogous to `Alonzo.validateExUnitsTooBigUTxO`, enforcing that each sub-transaction's total ExUnits does not exceed a protocol-parameter bound (e.g., a new `maxSubTxExUnits` parameter or reuse `maxTxExUnits`).
2. **Aggregate sub-transaction ExUnits into the top-level check**: modify `validateExUnitsTooBigUTxO` for Dijkstra to sum ExUnits across the top-level transaction and all its sub-transactions before comparing against `maxTxExUnits`.
3. **Include sub-transaction ExUnits in the fee calculation**: extend `getConwayMinFeeTx` (or introduce a Dijkstra-specific override) to sum redeemer ExUnits from all sub-transactions and apply the `prices` multiplier, ensuring the fee covers the actual computational cost.

---

### Proof of Concept

1. Construct a top-level `DijkstraTx` with zero top-level redeemers (ExUnits = 0).
2. Embed `N` sub-transactions in `dtbrSubTransactions`, each containing a Plutus script whose redeemer specifies `ExUnits { exUnitsMem = maxMem, exUnitsSteps = maxSteps }` (equal to `maxTxExUnits`). Keep total serialized size ≤ `maxTxSize`.
3. Submit the transaction. The ledger runs `validateExUnitsTooBigUTxO` on the top-level tx: `0 ≤ maxTxExUnits` — passes.
4. `dijkstraSubLedgersTransition` iterates over all `N` sub-transactions via `foldM`, calling `SUBLEDGER` for each. Each `SUBLEDGER` calls `SUBUTXOW → SUBUTXO`. No ExUnits check is performed.
5. Each sub-transaction's Plutus scripts are executed with their full `maxTxExUnits` budget.
6. Total Plutus computation performed: `N × maxTxExUnits`. Fee paid: covers only byte-size, not ExUnits. The `maxTxExUnits` and `maxBlockExUnits` protocol limits are effectively bypassed. [4](#0-3) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-184)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Tx.hs (L261-284)
```haskell
toCBORForSizeComputation ::
  ( EncCBOR (TxBody l era)
  , EncCBOR (TxWits era)
  , EncCBOR (TxAuxData era)
  ) =>
  DijkstraTx l era ->
  Encoding
toCBORForSizeComputation tx =
  encodeListLen 3
    <> encCBOR (tx ^. bodyDijkstraTxL)
    <> encCBOR (tx ^. witsDijkstraTxL)
    <> encodeNullStrictMaybe encCBOR (tx ^. auxDataDijkstraTxL)

sizeDijkstraTxF ::
  forall era l.
  EraTx era =>
  SimpleGetter (DijkstraTx l era) Word32
sizeDijkstraTxF =
  to $
    errorFail
      . integralToBounded @Int64 @Word32
      . LBS.length
      . serialize (eraProtVerLow @era)
      . toCBORForSizeComputation
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs (L128-135)
```haskell
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L231-278)
```haskell
dijkstraSubUtxoTransition = do
  TRC (SubUtxoEnv slot pp certState originalUtxo (IsValid isValid), utxoState, stAnnTx) <-
    judgmentContext
  let tx = stAnnTx ^. txStAnnTxG

  let txBody = tx ^. bodyTxL

  runTest $ Allegra.validateOutsideValidityIntervalUTxO slot txBody

  sysSt <- liftSTS $ asks systemStart
  ei <- liftSTS $ asks epochInfo
  runTest $ Alonzo.validateOutsideForecast ei slot sysSt tx

  let allSizedOutputs = txBody ^. allSizedOutputsTxBodyF
  let allOutputs = fmap sizedValue allSizedOutputs
  runTest $ Alonzo.validateOutputTooBigUTxO pp allOutputs

  runTest $ Shelley.validateInputSetEmptyUTxO txBody

  let inputs = txBody ^. inputsTxBodyL
  let refInputs = txBody ^. referenceInputsTxBodyL
  runTest $ Shelley.validateBadInputsUTxO originalUtxo (inputs `Set.union` refInputs)
  runTest $ Shelley.validateBadInputsUTxO (utxosUtxo utxoState) inputs

  runTestOnSignal $ Shelley.validateOutputBootAddrAttrsTooBig allOutputs

  runTestOnSignal $ Babbage.validateOutputTooSmallUTxO pp allSizedOutputs

  netId <- liftSTS $ asks networkId
  runTestOnSignal $ Shelley.validateWrongNetwork netId allOutputs
  runTestOnSignal $ Shelley.validateWrongNetworkWithdrawal netId txBody
  runTestOnSignal $ validateWrongNetworkInDirectDeposit netId txBody
  runTestOnSignal $ Alonzo.validateWrongNetworkInTxBody netId txBody

  if isValid
    then do
      newState <-
        Shelley.updateUTxOStateNoFees
          pp
          utxoState
          txBody
          certState
          (utxosGovState utxoState)
          (tellEvent . TotalDeposits (hashAnnotated txBody))
          (\a b -> tellEvent $ TxUTxODiff a b)
      pure $ newState & utxosDonationL <>~ txBody ^. treasuryDonationTxBodyL
    else
      pure utxoState
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L332-332)
```haskell
  FeeTooSmallUTxO _ -> error "Impossible: `FeeTooSmallUTxO` for SUBUTXO"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L340-340)
```haskell
  ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
```
