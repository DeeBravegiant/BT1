The code confirms this is a real vulnerability. Here is the full analysis:

---

### Title
Block-Level Reference Script Size Limit Bypassed via Sub-Transaction Reference Scripts — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`, `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs`)

---

### Summary

The Dijkstra BBODY block-level reference script size check (`ppMaxRefScriptSizePerBlockG`) delegates to Conway's `totalRefScriptSizeInBlock`, which calls `txNonDistinctRefScriptsSize` per top-level transaction only. Sub-transaction reference scripts are invisible to this check. The Dijkstra-specific `batchNonDistinctRefScriptsSize` — which correctly sums top-level plus all sub-transaction reference scripts — is used only for the per-transaction limit (`ppMaxRefScriptSizePerTxG`) inside the LEDGER rule. An attacker can therefore exceed the block-level byte limit by distributing reference script usage across sub-transactions.

---

### Finding Description

**BBODY block-level check (Dijkstra):**

`dijkstraBbodyTransition` calls:

```haskell
Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
``` [1](#0-0) 

`validateBodyRefScriptsSizeTooBig` computes:

```haskell
totalSize = totalRefScriptSizeInBlock protVer txs utxo
``` [2](#0-1) 

`totalRefScriptSizeInBlock` iterates over the top-level `txs` sequence and for each calls `txNonDistinctRefScriptsSize (UTxO accUtxo) tx`: [3](#0-2) 

`txNonDistinctRefScriptsSize` only inspects the top-level transaction body's own `inputsTxBodyL` and `referenceInputsTxBodyL`:

```haskell
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
``` [4](#0-3) 

Sub-transaction inputs are never consulted here.

**Per-transaction check (Dijkstra LEDGER):**

`validateAllRefScriptSize` uses `batchNonDistinctRefScriptsSize`, which correctly sums the top-level tx plus all sub-transactions:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
                       (tx ^. bodyTxL . subTransactionsTxBodyL))
``` [5](#0-4) 

This is only used to enforce `ppMaxRefScriptSizePerTxG`: [6](#0-5) 

**The gap:** `totalRefScriptSizeInBlock` (block-level) uses `txNonDistinctRefScriptsSize` (top-level only). `validateAllRefScriptSize` (per-tx) uses `batchNonDistinctRefScriptsSize` (top + subs). Sub-tx reference scripts are counted for the per-tx limit but are completely invisible to the block-level limit.

---

### Impact Explanation

An attacker can exceed `ppMaxRefScriptSizePerBlockG` by placing reference script usage inside sub-transactions. Reference scripts must be loaded and validated during block processing; the block-level limit exists precisely to bound this cost. Bypassing it allows a block to carry far more reference script bytes than the protocol intends, increasing block validation cost beyond the designed ceiling.

This matches the **Medium** impact: attacker-controlled transactions exceed intended validation limits.

---

### Likelihood Explanation

The Dijkstra era is new and sub-transactions are a novel construct. The oversight is a straightforward design gap: the block-level accounting function was not updated to be sub-transaction-aware when `batchNonDistinctRefScriptsSize` was introduced. Any unprivileged user who can submit a Dijkstra-era transaction with sub-transactions can trigger this. No special keys, governance majority, or privileged access is required.

---

### Recommendation

Replace the call to `txNonDistinctRefScriptsSize` inside `totalRefScriptSizeInBlock` (or in `validateBodyRefScriptsSizeTooBig` when called from the Dijkstra BBODY rule) with `batchNonDistinctRefScriptsSize`, so that sub-transaction reference scripts are included in the block-level accounting. Alternatively, override `validateBodyRefScriptsSizeTooBig` in the Dijkstra BBODY rule to use a Dijkstra-aware total that sums `batchNonDistinctRefScriptsSize` across all top-level transactions in the block.

---

### Proof of Concept

Let:
- `ppMaxRefScriptSizePerBlockG` = 512 KB
- `ppMaxRefScriptSizePerTxG` = 200 KB

Construct a block with 3 top-level transactions, each structured as:
- Top-level tx body: 0 reference inputs (0 bytes of ref scripts)
- Sub-transactions: reference inputs pointing to UTxO entries whose scripts total 190 KB

Per-tx check (`validateAllRefScriptSize`): `batchNonDistinctRefScriptsSize` = 0 + 190 KB = 190 KB ≤ 200 KB — **passes**.

Block-level check (`validateBodyRefScriptsSizeTooBig`): `totalRefScriptSizeInBlock` = 0 + 0 + 0 = 0 bytes ≤ 512 KB — **passes**.

Actual reference script bytes loaded during block validation: 3 × 190 KB = 570 KB > 512 KB — **limit exceeded**.

The block is accepted despite the true reference script load exceeding `ppMaxRefScriptSizePerBlockG`.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L363-363)
```haskell
  Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L264-276)
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
