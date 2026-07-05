I need to carefully trace the actual code paths to determine if the block-level check truly misses sub-transaction ref scripts.

### Title
Block-Level Ref Script Size Limit Bypass via Sub-Transaction Reference Scripts — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

### Summary

The block-level reference script size check in `dijkstraBbodyTransition` reuses `Conway.validateBodyRefScriptsSizeTooBig`, which internally calls `totalRefScriptSizeInBlock` → `txNonDistinctRefScriptsSize`. This function only inspects the top-level `Tx TopTx era` body's inputs and reference inputs. It does **not** traverse `subTransactionsTxBodyL`. The per-transaction check (`validateAllRefScriptSize`) uses `batchNonDistinctRefScriptsSize`, which explicitly sums the top-level tx plus all sub-txs. The two counters are structurally inconsistent, and the block-level limit `ppMaxRefScriptSizePerBlockG` can be bypassed by placing all reference scripts inside sub-transactions.

---

### Finding Description

**Block-level check path:**

`dijkstraBbodyTransition` (line 363) calls:
```haskell
Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
``` [1](#0-0) 

This delegates to `totalRefScriptSizeInBlock`, which iterates over the block's `StrictSeq (Tx TopTx era)` and calls `txNonDistinctRefScriptsSize` on each top-level tx: [2](#0-1) 

`txNonDistinctRefScriptsSize` only reads `referenceInputsTxBodyL` and `inputsTxBodyL` of the top-level tx body — it has no awareness of `subTransactionsTxBodyL`: [3](#0-2) 

**Per-transaction check path:**

`validateAllRefScriptSize` in the LEDGER rule calls `batchNonDistinctRefScriptsSize`: [4](#0-3) 

`batchNonDistinctRefScriptsSize` explicitly sums the top-level tx **plus all sub-txs**: [5](#0-4) 

The two counters are structurally different. The block-level counter is blind to sub-transaction reference scripts.

---

### Impact Explanation

An unprivileged block producer can craft a block where:

- Each top-level tx carries **zero** top-level reference inputs but embeds sub-transactions that each reference large scripts.
- The per-tx check passes because `batchNonDistinctRefScriptsSize(top-tx) ≤ ppMaxRefScriptSizePerTxG`.
- The block-level check passes because `totalRefScriptSizeInBlock` sees 0 for every top-tx → total = 0 ≤ `ppMaxRefScriptSizePerBlockG`.
- The actual total ref script data in the block (top + all sub-txs across all top-txs) can be a multiple of `ppMaxRefScriptSizePerBlockG`.

This is a bypass of the `ppMaxRefScriptSizePerBlockG` protocol parameter — the intended block-level resource cap on reference script data is not enforced when scripts are placed in sub-transactions.

**Corrected impact scope:** This is **Medium** — attacker-controlled blocks exceed the intended validation limit (`ppMaxRefScriptSizePerBlockG`). The question's framing as "Critical divergence" is **not accurate**: all production nodes run the same Haskell code and would reach the same (incorrect) acceptance decision. There is no inter-node divergence; the limit is simply not enforced.

---

### Likelihood Explanation

Exploitable by any block producer in the Dijkstra era. No privileged access, governance majority, or key compromise is required. The attacker only needs to construct a valid `TopTx` with sub-transactions referencing large scripts, keeping each top-tx's top-level ref script count at zero.

---

### Recommendation

Replace the call to `Conway.validateBodyRefScriptsSizeTooBig` in `dijkstraBbodyTransition` with a Dijkstra-specific block-level check that uses `batchNonDistinctRefScriptsSize` (or an equivalent accumulator) instead of `txNonDistinctRefScriptsSize`, so that sub-transaction reference scripts are counted toward `ppMaxRefScriptSizePerBlockG`.

---

### Proof of Concept

```
ppMaxRefScriptSizePerTxG    = 200_000  (bytes)
ppMaxRefScriptSizePerBlockG = 500_000  (bytes)

Craft a block with 10 TopTxs.
Each TopTx:
  - 0 top-level reference inputs (top-level ref script size = 0)
  - 1 SubTx referencing a 49_000-byte script
    → batchNonDistinctRefScriptsSize = 49_000 ≤ 200_000  ✓ per-tx check passes

Block-level check:
  totalRefScriptSizeInBlock = sum of txNonDistinctRefScriptsSize over 10 TopTxs
                            = 0 + 0 + ... = 0 ≤ 500_000  ✓ block check passes

Actual ref script data in block:
  10 × 49_000 = 490_000 bytes  (just under block limit — increase to 11 txs)
  11 × 49_000 = 539_000 bytes  > 500_000  ✗ limit violated, block accepted anyway
```

The differential between `batchNonDistinctRefScriptsSize` and `txNonDistinctRefScriptsSize` is exactly the sub-transaction ref script contribution, which the block-level check ignores entirely. [6](#0-5) [7](#0-6)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L335-371)
```haskell
dijkstraBbodyTransition = do
  TRC
    ( Shelley.BbodyEnv pp account
      , Shelley.BbodyState ls blocksMade
      , DijkstraBbodySignal block@Block {blockBody}
      ) <-
    judgmentContext

  Shelley.validateBlockBodySize block (pp ^. ppProtocolVersionL)

  Shelley.validateBlockBodyHash block

  let bhSlot = block ^. slotNoBlockHeaderL

  (firstSlot, curEpoch) <- liftSTS $ slotToEpochBoundary bhSlot

  let txs = blockBody ^. txSeqBlockBodyL

  ls' <-
    trans @(EraRule "LEDGERS" era) $
      TRC
        ( Shelley.LedgersEnv bhSlot curEpoch pp account
        , ls
        , fromStrict txs
        )

  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

  Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)

  case blockBody ^. perasCertBlockBodyL of
    SNothing -> pure ()
    SJust cert ->
      let nonce = block ^. prevNonceBlockHeaderL
       in validatePerasCert nonce PerasKey cert ?! injectFailure (PerasCertValidationFailed cert nonce)

  pure $ Shelley.BbodyState ls' $ incrBlocks block firstSlot (pp ^. ppDG) blocksMade
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L342-355)
```haskell
validateBodyRefScriptsSizeTooBig pp blockBody utxo =
  let protVer = pp ^. ppProtocolVersionL
      txs = blockBody ^. txSeqBlockBodyL
      totalSize = totalRefScriptSizeInBlock protVer txs utxo
      maxSize = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerBlockG
   in totalSize
        <= maxSize
          ?! injectFailure
            ( BodyRefScriptsSizeTooBig $
                Mismatch
                  { mismatchSupplied = totalSize
                  , mismatchExpected = maxSize
                  }
            )
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L357-370)
```haskell
totalRefScriptSizeInBlock ::
  (AlonzoEraTx era, BabbageEraTxBody era) => ProtVer -> StrictSeq (Tx TopTx era) -> UTxO era -> Int
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 =
      getSum $ foldMap' (Monoid.Sum . txNonDistinctRefScriptsSize (UTxO utxo)) txs
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
  where
    accum (!accUtxo, !accSum) tx =
      let updatedUtxo = accUtxo `Map.union` unUTxO toAdd
          toAdd
            | IsValid True <- tx ^. isValidTxL = txouts $ tx ^. bodyTxL
            | otherwise = collOuts $ tx ^. bodyTxL
       in (updatedUtxo, accSum + txNonDistinctRefScriptsSize (UTxO accUtxo) tx)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L183-187)
```haskell
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
