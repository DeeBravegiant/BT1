Looking at the vulnerability class from the external report — **a function is called without passing a configured custom value, causing fallback to a default** — I need to find an analog in the Cardano Ledger where a fee/deposit/validation function uses a default or wrong parameter instead of the configured one.

The most direct analog I found is in the Dijkstra era's `EraUTxO` instance.

**Key evidence:**

In `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` line 141: [1](#0-0) 

```haskell
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

`getConwayMinFeeTxUtxo` is defined as: [2](#0-1) 

```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

`txNonDistinctRefScriptsSize` only counts reference scripts from the **top-level transaction's** inputs: [3](#0-2) 

However, Dijkstra introduces **subtransactions**, and the same module defines `batchNonDistinctRefScriptsSize` specifically to count reference scripts across the top-level transaction **and all subtransactions**: [4](#0-3) 

```haskell
-- | Total size of reference scripts across a top-level transaction and all its subtransactions.
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
                       (tx ^. bodyTxL . subTransactionsTxBodyL))
```

The function exists but is **never used** in `getMinFeeTxUtxo`. This is the direct analog to the report: the correct "hook" (`batchNonDistinctRefScriptsSize`) is defined but not passed/used, causing fallback to the Conway default (`txNonDistinctRefScriptsSize`) that ignores subtransactions.

The Dijkstra era also properly promotes the previously hardcoded Conway multiplier/stride values to actual protocol parameters: [5](#0-4) 

---

### Title
Reference Script Fee Bypassed for Subtransactions in Dijkstra Era — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

### Summary
The `EraUTxO DijkstraEra` instance delegates `getMinFeeTxUtxo` to `getConwayMinFeeTxUtxo`, which internally calls `txNonDistinctRefScriptsSize`. This function only measures reference scripts from the top-level transaction's inputs. Dijkstra introduces subtransactions that can also carry reference inputs with reference scripts. The module itself defines `batchNonDistinctRefScriptsSize` to aggregate sizes across the top-level transaction and all subtransactions, but this function is never wired into `getMinFeeTxUtxo`. As a result, the minimum-fee check in the UTXO rule systematically under-counts reference script bytes whenever subtransactions are present, allowing an attacker to pay less than the protocol-mandated reference-script fee.

### Finding Description
`getMinFeeTxUtxo` is the method called by the UTXO validation rule to compute the minimum acceptable fee for a transaction. For `DijkstraEra`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs, line 141
getMinFeeTxUtxo = getConwayMinFeeTxUtxo
```

`getConwayMinFeeTxUtxo` computes:

```haskell
getConwayMinFeeTxUtxo pparams tx utxo =
  getMinFeeTx pparams tx $ txNonDistinctRefScriptsSize utxo tx
```

`txNonDistinctRefScriptsSize` only inspects `tx ^. bodyTxL . referenceInputsTxBodyL` and `tx ^. bodyTxL . inputsTxBodyL` of the **top-level** transaction body. It has no knowledge of `subTransactionsTxBodyL`.

Dijkstra's own module defines the correct aggregation function:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs, lines 263-277
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
                       (tx ^. bodyTxL . subTransactionsTxBodyL))
```

This function is exported but never used in `getMinFeeTxUtxo`. The correct Dijkstra implementation should be:

```haskell
getMinFeeTxUtxo pp tx utxo =
  getMinFeeTx pp tx $ batchNonDistinctRefScriptsSize utxo tx
```

The `getMinFeeTx` for Dijkstra is `getConwayMinFeeTx`, which correctly reads the now-parameterized `ppRefScriptCostMultiplierG` and `ppRefScriptCostStrideG` from the Dijkstra protocol parameters: [6](#0-5) 

So the multiplier and stride are correctly sourced from protocol parameters in Dijkstra, but the **size input** to that calculation is wrong — it omits subtransaction reference scripts entirely.

### Impact Explanation
The reference-script tiered fee (`tierRefScriptFee`) was introduced specifically to deter DDoS attacks via large reference scripts (see ADR-009). The fee grows super-linearly with total reference script size. By placing large reference scripts exclusively in subtransactions, an attacker can submit a batch whose true reference-script footprint is arbitrarily large while the fee check only sees the (potentially zero) reference-script size of the top-level transaction. This allows fees to be set below the protocol-mandated minimum for the actual resource consumption, modifying fees outside design parameters. Impact: **Medium** — attacker-controlled transactions modify fees outside design parameters.

### Likelihood Explanation
**Low.** The Dijkstra era is not yet deployed on mainnet. Exploitation requires constructing a valid Dijkstra top-level transaction with subtransactions that carry reference inputs pointing to large scripts. No privileged access is required; any transaction submitter can craft such a transaction once the era is live.

### Recommendation
Override `getMinFeeTxUtxo` in the `EraUTxO DijkstraEra` instance to use `batchNonDistinctRefScriptsSize`:

```haskell
getMinFeeTxUtxo pp tx utxo =
  getMinFeeTx pp tx $ batchNonDistinctRefScriptsSize utxo tx
```

This mirrors the pattern already established by `batchNonDistinctRefScriptsSize` and ensures that reference scripts in subtransactions are included in the fee calculation, consistent with the intent documented in the function's own comment.

### Proof of Concept
1. Construct a Dijkstra `TopTx` whose top-level body has no reference inputs (zero reference-script bytes at the top level).
2. Embed one or more `SubTx` subtransactions, each with `referenceInputsTxBodyL` pointing to UTxO outputs that carry large Plutus reference scripts (e.g., near the 200 KiB per-tx limit each).
3. Compute the minimum fee using `getMinFeeTxUtxo` (which calls `getConwayMinFeeTxUtxo` → `txNonDistinctRefScriptsSize`). The result will reflect zero reference-script bytes.
4. Set the transaction fee to this under-estimated value and submit.
5. The UTXO rule accepts the transaction because `getMinFeeTxUtxo` returns a fee that ignores the subtransaction reference scripts, while the actual resource cost (deserialization of large scripts) is borne by every validating node.

<cite repo

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L584-587)
```haskell
  ppMaxRefScriptSizePerTxG = ppLensHKD . hkdMaxRefScriptSizePerTxL
  ppMaxRefScriptSizePerBlockG = ppLensHKD . hkdMaxRefScriptSizePerBlockL
  ppRefScriptCostMultiplierG = ppLensHKD . hkdRefScriptCostMultiplierL
  ppRefScriptCostStrideG = ppLensHKD . hkdRefScriptCostStrideL
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L103-112)
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
```
