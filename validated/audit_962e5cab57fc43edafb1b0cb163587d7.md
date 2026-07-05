### Title
Sub-Transaction Reference Script Deserialization Cost Excluded from Minimum Fee Calculation - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

### Summary
In the Dijkstra era, the minimum fee for a top-level transaction is computed using `getConwayMinFeeTxUtxo`, which calls `txNonDistinctRefScriptsSize` — a function that only measures reference script sizes for the **top-level** transaction's inputs. Sub-transactions embedded in the batch are entirely excluded from this cost accounting. A `batchNonDistinctRefScriptsSize` helper that correctly aggregates sub-transaction reference script sizes exists in the same module but is never wired into the fee validation path. An unprivileged sender can therefore submit a top-level transaction with negligible reference scripts (paying a minimal fee) while embedding many sub-transactions that each carry large Plutus reference scripts, forcing every block producer to deserialize all of those scripts at a cost that is not reflected in the fee.

### Finding Description
The Dijkstra era introduces nested ("sub") transactions. The UTXO transition rule validates the minimum fee with:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs:372-373
{- minfee pp txTop utxo₀ ≤ txfee txb -}
runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

`validateFeeTooSmallUTxO` dispatches to `getMinFeeTxUtxo`, which for Dijkstra era is bound to `getConwayMinFeeTxUtxo`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs:141
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

`getConwayMinFeeTxUtxo` computes the reference-script surcharge using `txNonDistinctRefScriptsSize`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs:174-175
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

`txNonDistinctRefScriptsSize` only unions the **top-level** transaction's spend inputs and reference inputs:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs:183-187
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

Sub-transactions are never consulted. The Dijkstra module already provides the correct batch-aware function:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs:264-277
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

This function is exported but never called from the fee-validation path. The historical precedent for this class of bug is documented in the codebase itself:

> "Overhead of using reference scripts was not properly accounted for when this feature was introduced in the Babbage era, which is now fixed in the Conway era."
> — `docs/adr/2024-08-14_009-refscripts-fee-change.md`

The Conway fix applied `txNonDistinctRefScriptsSize` to the top-level transaction. The Dijkstra era repeats the same omission for sub-transactions.

Additionally, `validateExUnitsTooBigUTxO` in the Dijkstra UTXO transition also only checks the top-level transaction's declared ExUnits against `maxTxExUnits`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs:414-415
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

`totExUnits` sums only the top-level redeemers:

```haskell
-- eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs:394
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

Sub-transactions' redeemers are excluded, so both the per-transaction ExUnits cap and the script-fee component of `minfee` are computed without accounting for sub-transaction script execution costs.

### Impact Explanation
An attacker-controlled top-level transaction can carry sub-transactions whose aggregate reference-script deserialization cost (and Plutus execution cost) far exceeds what the declared fee covers. Every honest block producer that attempts to include the transaction must pay the full deserialization and execution cost, while the submitter pays only the cost attributable to the top-level transaction. This falls squarely within the **Medium** allowed impact: *"Attacker-controlled transactions … exceed intended validation limits or modify fees … outside design parameters."* The fee paid is structurally below the minimum fee that the protocol intends to charge for the actual work performed.

### Likelihood Explanation
The attack requires no privileged access, no governance majority, and no key compromise. Any unprivileged transaction sender can craft a Dijkstra-era top-level transaction embedding sub-transactions with large Plutus reference scripts. The Dijkstra era is present in the production codebase and its UTXO rule is fully wired. The attack is cheap to mount (the attacker pays only the top-level fee) and repeatable.

### Recommendation
Override `getMinFeeTxUtxo` in the Dijkstra era's `EraUTxO` instance to use `batchNonDistinctRefScriptsSize` instead of delegating to `getConwayMinFeeTxUtxo`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
getMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ batchNonDistinctRefScriptsSize utxo tx
```

Similarly, replace `Alonzo.validateExUnitsTooBigUTxO pp tx` in `dijkstraUtxoTransition` with a batch-aware variant that sums ExUnits across the top-level transaction and all sub-transactions before comparing against `maxTxExUnits`.

### Proof of Concept
1. Construct a Dijkstra-era top-level transaction with zero or minimal reference inputs (so `txNonDistinctRefScriptsSize` returns a small value and the required fee is low).
2. Embed N sub-transactions, each referencing UTxO entries that carry large Plutus scripts (e.g., scripts approaching the per-transaction 200 KiB limit each).
3. Set `txfee` to the value returned by `getConwayMinFeeTxUtxo` (which ignores sub-transaction reference scripts).
4. Submit the transaction. The ledger accepts it because `validateFeeTooSmallUTxO` passes — it only checks the top-level fee against the top-level reference script cost.
5. Every block producer that processes this transaction must deserialize all N × (script size) bytes of reference scripts, paying a validation cost that is N times larger than what the fee covers. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-141)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue

  getScriptsProvided = getDijkstraScriptsProvided

  getScriptsNeeded = getDijkstraScriptsNeeded

  getScriptsHashesNeeded = getAlonzoScriptsHashesNeeded

  getWitsVKeyNeeded _ = getConwayWitsVKeyNeeded

  getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L264-277)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L174-175)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L372-376)
```haskell
  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo

  {- (RedeemersOf txTop ≠ ∅ ⊎ Any (λ txSub → RedeemersOf txSub ≠ ∅) subtxs) → collateralCheck -}
  validate $ validateBatchCollateral pp tx originalUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
