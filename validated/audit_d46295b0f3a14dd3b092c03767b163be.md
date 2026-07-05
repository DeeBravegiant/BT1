After thorough investigation of the codebase, I found a valid analog to the external report's fee-manipulation vulnerability class.

---

### Title
Sub-Transaction Reference Scripts Excluded from Exponential Fee Calculation in Dijkstra Era — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, the minimum fee calculation for a top-level transaction uses `txNonDistinctRefScriptsSize`, which counts only the top-level transaction's reference scripts. However, the per-transaction size-limit check uses `batchNonDistinctRefScriptsSize`, which sums reference script sizes across the top-level transaction **and all its sub-transactions**. This asymmetry means an attacker can embed sub-transactions that reference large scripts, imposing the full deserialization cost on nodes while paying only the base byte-size fee — bypassing the exponential `tierRefScriptFee` that was specifically designed to deter this class of attack.

---

### Finding Description

The Conway era introduced `tierRefScriptFee` to charge exponentially for reference script deserialization overhead, after a real DDoS attack on June 25, 2024 exploited cheap-to-submit but expensive-to-deserialize scripts. The fee grows as `1.2^(chunk)` per 25 KiB tier.

The Dijkstra era introduces nested sub-transactions (`subTransactionsTxBodyL`). The `EraUTxO` instance for `DijkstraEra` inherits the Conway fee calculation unchanged:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs, line 141
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

`getConwayMinFeeTxUtxo` calls `txNonDistinctRefScriptsSize`, which only inspects the top-level transaction's `inputsTxBodyL` and `referenceInputsTxBodyL`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs, lines 183-187
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

Sub-transactions are entirely absent from this computation.

By contrast, the Dijkstra LEDGER rule's size-limit check uses `batchNonDistinctRefScriptsSize`, which explicitly aggregates sub-transaction reference scripts:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs, lines 264-277
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

And `validateAllRefScriptSize` in the Dijkstra LEDGER rule uses this batch function:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs, lines 313-329
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        DijkstraTxRefScriptsSizeTooBig ...
```

The result: the size-limit gate uses the full batch size (top-level + sub-txs), but the fee gate uses only the top-level size. An attacker can fill sub-transactions with reference inputs pointing to large scripts up to the 200 KiB per-tx limit, pay only the linear base fee (`txFeePerByte × txSize`), and force nodes to deserialize all those scripts without paying the exponential `tierRefScriptFee` surcharge.

---

### Impact Explanation

The reference script fee was designed so that the exponential cost of deserializing large scripts is reflected in the fee paid. Sub-transaction reference scripts are deserialized during `SUBLEDGER` rule processing (script validation for each sub-transaction), yet the fee calculation does not charge for them. An attacker can therefore submit transactions whose actual validation cost to nodes exceeds the fee paid, modifying the effective fee outside the design parameters set by `ppMinFeeRefScriptCostPerByte`, `ppRefScriptCostStride`, and `ppRefScriptCostMultiplier`. This matches the allowed impact: **attacker-controlled transactions modify fees outside design parameters (Medium)**.

---

### Likelihood Explanation

Any unprivileged transaction submitter can construct a Dijkstra top-level transaction containing sub-transactions whose `referenceInputsTxBodyL` or `inputsTxBodyL` point to UTxOs holding large reference scripts. No special role, key, or governance action is required. The attacker only needs to pre-create UTxOs with large scripts (a normal transaction) and then submit a top-level transaction with sub-transactions referencing them.

---

### Recommendation

Replace `getConwayMinFeeTxUtxo` with a Dijkstra-specific implementation that uses `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize` when computing the reference script fee component:

```haskell
getDijkstraMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ batchNonDistinctRefScriptsSize utxo tx
```

and update the `EraUTxO DijkstraEra` instance accordingly:

```haskell
getMinFeeTxUtxo = getDijkstraMinFeeTxUtxo
```

This mirrors the existing symmetry between `batchNonDistinctRefScriptsSize` (used in `validateAllRefScriptSize`) and the fee calculation, ensuring the exponential fee covers all reference scripts deserialized during transaction validation.

---

### Proof of Concept

1. **Setup**: Submit a transaction creating N UTxOs each holding a large Plutus reference script (e.g., 20 KiB each, N = 10, total = 200 KiB — within `maxRefScriptSizePerTx`).

2. **Attack transaction**: Construct a Dijkstra top-level transaction with 10 sub-transactions, each sub-transaction having a `referenceInputsTxBodyL` pointing to one of the large-script UTxOs.

3. **Fee paid**: The top-level transaction's fee is computed via `getConwayMinFeeTxUtxo` → `txNonDistinctRefScriptsSize`, which sees **zero** reference scripts in the top-level inputs (the large scripts are only in sub-transaction inputs). The attacker pays only the base `txFeePerByte × txSize` fee.

4. **Fee that should be paid**: `batchNonDistinctRefScriptsSize` would count 200 KiB of reference scripts. At current parameters (`minFeeRefScriptCostPerByte = 15`, multiplier `1.2`, stride `25,600`), the exponential surcharge for 200 KiB is approximately 8 × 25,600 tiers, yielding a fee on the order of several ADA — which the attacker avoids entirely.

5. **Node cost**: During `SUBLEDGERS` processing, each sub-transaction's reference scripts are deserialized and validated, imposing the full computational cost without the corresponding fee. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L166-187)
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

-- | Calculate the total size of reference scripts used by the transactions. Duplicate
-- scripts will be counted as many times as they occur, since there is never a reason to
-- include an input with the same reference script.
--
-- Any input that appears in both regular inputs and reference inputs of a transaction is
-- only used once in this computation.
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L313-329)
```haskell
validateAllRefScriptSize ::
  ( EraTx era
  , DijkstraEraTxBody era
  ) =>
  PParams era ->
  UTxO era ->
  Tx TopTx era ->
  Test (DijkstraLedgerPredFailure era)
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        DijkstraTxRefScriptsSizeTooBig
          Mismatch
            { mismatchSupplied = totalRefScriptSize
            , mismatchExpected = maxRefScriptSizePerTx
            }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L103-136)
```haskell
getConwayMinFeeTx pp tx refScriptsSize =
  alonzoMinFeeTx pp tx <+> refScriptsFee
  where
    refScriptCostPerByte = unboundRational (pp ^. ppMinFeeRefScriptCostPerByteL)
    refScriptsFee =
      tierRefScriptFee
        (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
        (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
        refScriptCostPerByte
        refScriptsSize

-- | Calculate the fee for reference scripts using an exponential growth of the price per
-- byte with linear increments
tierRefScriptFee ::
  HasCallStack =>
  -- | Growth factor or step multiplier
  Rational ->
  -- | Increment size in which price grows linearly according to the price
  Int ->
  -- | Base fee. Currently this is customizable by `ppMinFeeRefScriptCostPerByteL`
  Rational ->
  -- | Total RefScript size in bytes
  Int ->
  Coin
tierRefScriptFee multiplier sizeIncrement
  | multiplier <= 0 || sizeIncrement <= 0 = error "Size increment and multiplier must be positive"
  | otherwise = go 0
  where
    go !acc !curTierPrice !n
      | n < sizeIncrement =
          Coin $ floor (acc + toRational n * curTierPrice)
      | otherwise =
          go (acc + sizeIncrementRational * curTierPrice) (multiplier * curTierPrice) (n - sizeIncrement)
    sizeIncrementRational = toRational sizeIncrement
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L13-19)
```markdown
## Context

It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
