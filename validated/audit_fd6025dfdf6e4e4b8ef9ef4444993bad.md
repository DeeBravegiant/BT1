### Title
Sub-Transaction Reference Script Fee Not Included in Dijkstra Minimum Fee Calculation - (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, the minimum fee check for a batch transaction only accounts for reference scripts in the **top-level** transaction. Reference scripts attached to **sub-transactions** are silently excluded from the fee calculation, even though a correct helper function (`batchNonDistinctRefScriptsSize`) already exists to compute the full batch-wide cost. An attacker can submit a batch transaction with large reference scripts exclusively in sub-transactions and pay a fee that is far below what the protocol intends to charge.

---

### Finding Description

The Dijkstra era introduces nested ("batch") transactions: a top-level `TopTx` may embed one or more `SubTx` sub-transactions, each of which can carry its own reference inputs and therefore its own reference scripts.

The Conway era established that reference scripts impose a real deserialization cost on validators and introduced a tiered fee (`minFeeRefScriptCostPerByte`) to compensate for it. The function `getConwayMinFeeTxUtxo` computes this fee by calling `txNonDistinctRefScriptsSize`, which only inspects the **top-level** transaction's `inputsTxBodyL` and `referenceInputsTxBodyL`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

The Dijkstra `EraUTxO` instance reuses this function unchanged:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

The Dijkstra UTXO transition rule enforces the fee check by calling `Shelley.validateFeeTooSmallUTxO pp tx originalUtxo`, which internally calls `getMinFeeTxUtxo`. Because `getMinFeeTxUtxo` delegates to `getConwayMinFeeTxUtxo`, sub-transaction reference scripts are never counted.

A correct batch-aware helper already exists in the same file but is **never called** from any validation rule:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

Sub-transactions have no fee field of their own (the `DijkstraSubTxBodyRaw` constructor contains no `dtbrFee`), so the top-level fee is the only mechanism to cover the entire batch's processing cost. The `dijkstraSubUtxoTransition` rule confirms this: it performs no fee check whatsoever.

---

### Impact Explanation

**Medium — Attacker-controlled transactions modify fees outside design parameters.**

An attacker can craft a Dijkstra batch transaction whose sub-transactions reference large Plutus scripts stored in UTxO outputs. The top-level transaction's fee covers only the top-level reference script overhead; the sub-transaction reference script overhead is entirely unpriced. Validators must deserialize all reference scripts across all levels to validate the batch, but the fee collected is systematically lower than the protocol intends. This is the same class of under-pricing that triggered the June 2024 DDoS attack on Cardano (documented in ADR-009), now re-introduced at the sub-transaction level.

---

### Likelihood Explanation

Any unprivileged transaction author can submit a Dijkstra batch transaction. No special role, key, or governance action is required. The attacker only needs to:
1. Create UTxO outputs containing large reference scripts.
2. Embed sub-transactions that reference those outputs.
3. Set the top-level fee to the minimum required for the top-level transaction alone (which passes `validateFeeTooSmallUTxO`).

The batch is accepted because the fee check is blind to sub-transaction reference scripts.

---

### Recommendation

Replace the `getMinFeeTxUtxo` implementation for `DijkstraEra` with one that uses `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
instance EraUTxO DijkstraEra where
  ...
  getMinFeeTxUtxo pp tx utxo =
    getMinFeeTx pp tx (batchNonDistinctRefScriptsSize utxo tx)
```

This mirrors the pattern already used by `getConwayMinFeeTxUtxo` but extends the reference-script size computation to cover all sub-transactions in the batch.

---

### Proof of Concept

**Step 1 – Establish large reference scripts in UTxO.**
Submit a Conway/Dijkstra transaction that creates several UTxO outputs each carrying a large Plutus V3 script as a reference script (e.g., 25 KiB each).

**Step 2 – Craft a batch transaction.**
Construct a Dijkstra `TopTx` whose sub-transactions each include those UTxO outputs as reference inputs. The top-level transaction body carries no reference inputs of its own.

**Step 3 – Compute the fee.**
Call `getMinFeeTxUtxo pp tx utxo` (the current implementation). It returns `getConwayMinFeeTxUtxo pp tx utxo`, which calls `txNonDistinctRefScriptsSize utxo tx`. Because the top-level body has no reference inputs, `txNonDistinctRefScriptsSize` returns `0`, and the reference-script fee component is `Coin 0`.

**Step 4 – Submit.**
Set `feeTxBodyL` to the base minimum fee (size × `txFeePerByte` + `txFeeFixed`). The `validateFeeTooSmallUTxO` check passes. The batch is accepted despite validators having to deserialize all sub-transaction reference scripts.

**Contrast with correct behavior.**
`batchNonDistinctRefScriptsSize utxo tx` would return the sum of all sub-transaction reference script sizes, and `tierRefScriptFee` would add the appropriate tiered cost to the minimum fee, causing the transaction to be rejected unless the fee is raised accordingly.

---

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L141-141)
```haskell
  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L166-175)
```haskell
getConwayMinFeeTxUtxo ::
  ( EraTx era
  , BabbageEraTxBody era
  ) =>
  PParams era ->
  Tx l era ->
  UTxO era ->
  Coin
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L183-187)
```haskell
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L372-373)
```haskell
  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
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

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L80-82)
```markdown
## Consequences

Unlike previous eras, inclusion of any inputs in the transaction containing reference scripts is no longer free. Overhead of using reference scripts was not properly accounted for when this feature was introduced in the Babbage era, which is now fixed in the Conwya era.
```
