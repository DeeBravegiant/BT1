### Title
Block-level reference script size check omits sub-transaction scripts in Dijkstra era, allowing `maxRefScriptSizePerBlock` bypass - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

### Summary

In the Dijkstra era, the per-transaction reference script size check (`validateAllRefScriptSize`) correctly accounts for reference scripts in both the top-level transaction and all its sub-transactions via `batchNonDistinctRefScriptsSize`. However, the block-level check (`validateBodyRefScriptsSizeTooBig`, inherited from Conway and reused unchanged in Dijkstra) uses `totalRefScriptSizeInBlock`, which only calls `txNonDistinctRefScriptsSize` for each top-level transaction, completely omitting sub-transaction reference scripts. An unprivileged transaction submitter can craft transactions whose sub-transactions carry large reference scripts, causing the actual per-block reference script processing cost to exceed `maxRefScriptSizePerBlock` while the block-level check reports a value well below the limit.

### Finding Description

**Root cause — the block-level check:**

`totalRefScriptSizeInBlock` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs` iterates over the sequence of top-level transactions and, for each one, calls `txNonDistinctRefScriptsSize`:

```haskell
-- Bbody.hs lines 357-370
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 =
      getSum $ foldMap' (Monoid.Sum . txNonDistinctRefScriptsSize (UTxO utxo)) txs
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
  where
    accum (!accUtxo, !accSum) tx =
      ...
       in (updatedUtxo, accSum + txNonDistinctRefScriptsSize (UTxO accUtxo) tx)
```

`txNonDistinctRefScriptsSize` (Conway/UTxO.hs lines 183-187) only inspects the top-level transaction's `inputsTxBodyL` and `referenceInputsTxBodyL`; it has no knowledge of sub-transactions.

The Dijkstra BBODY rule reuses this function without modification:

```haskell
-- Dijkstra/Rules/Bbody.hs line 363
Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
```

**Root cause — the per-transaction check:**

`validateAllRefScriptSize` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs` (lines 313-329) uses `batchNonDistinctRefScriptsSize`:

```haskell
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $ ...
```

`batchNonDistinctRefScriptsSize` (Dijkstra/UTxO.hs lines 264-277) correctly sums reference scripts from the top-level transaction **and** all sub-transactions:

```haskell
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
```

**The inconsistency:** The per-tx check counts sub-transaction reference scripts; the block-level check does not. This is the direct analog of the ZKSync `MessageRoot` / `Bridgehub` inconsistency: one component has the complete check, the other is missing the sub-component dimension.

**Attack path:**

1. Attacker constructs a top-level transaction with zero or minimal reference inputs in its own body.
2. The top-level transaction embeds N sub-transactions, each referencing large Plutus scripts via `referenceInputsTxBodyL`.
3. The combined size (top-level + sub-txs) is kept at or just below `maxRefScriptSizePerTx` (200 KiB in Conway-inherited defaults; governance-set in Dijkstra), so `validateAllRefScriptSize` passes.
4. The block-level check sees only the top-level transaction's reference script size (≈ 0), so `validateBodyRefScriptsSizeTooBig` reports a negligible total.
5. The attacker fills a block with M such transactions. The actual reference script deserialization cost is M × (sub-tx ref script size), which can far exceed `maxRefScriptSizePerBlock` (1 MiB in Conway-inherited defaults), while the block-level check reports a value near zero.
6. Every honest node that validates the block must deserialize all sub-transaction reference scripts, incurring unbounded computational cost relative to the intended block limit.

### Impact Explanation

The `maxRefScriptSizePerBlock` limit was introduced specifically to prevent DDoS attacks via expensive reference script deserialization (documented in `docs/adr/2024-08-14_009-refscripts-fee-change.md`). By omitting sub-transaction reference scripts from the block-level accounting, the Dijkstra era reintroduces the same attack vector the limit was designed to close. An attacker-controlled transaction causes honest nodes to exceed intended validation limits, matching the allowed impact: **Medium — attacker-controlled transactions exceed intended validation limits**.

### Likelihood Explanation

No privileged access is required. Any transaction submitter can embed sub-transactions in a Dijkstra top-level transaction. The Dijkstra era is the first era to introduce sub-transactions, so the inherited Conway block-level check has never been updated to handle them. The inconsistency is structural and will be triggered by any transaction that places reference inputs exclusively in sub-transactions rather than in the top-level body.

### Recommendation

Override `validateBodyRefScriptsSizeTooBig` in the Dijkstra BBODY rule (or update `totalRefScriptSizeInBlock`) to use `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize` when iterating over transactions, so that sub-transaction reference scripts are included in the block-level accounting. Concretely, replace the per-transaction accumulation step in `totalRefScriptSizeInBlock` with a Dijkstra-aware variant that calls `batchNonDistinctRefScriptsSize` for each top-level transaction.

### Proof of Concept

```
Given:
  maxRefScriptSizePerTx   = 200 KiB  (pp ^. ppMaxRefScriptSizePerTxG)
  maxRefScriptSizePerBlock = 1 MiB   (pp ^. ppMaxRefScriptSizePerBlockG)

Construct 10 top-level transactions, each with:
  - 0 reference inputs in the top-level body
  - 1 sub-transaction with reference inputs totalling 190 KiB of Plutus scripts

Per-tx check (validateAllRefScriptSize):
  batchNonDistinctRefScriptsSize = 0 (top-level) + 190 KiB (sub-tx) = 190 KiB < 200 KiB  ✓ PASS

Block-level check (validateBodyRefScriptsSizeTooBig via totalRefScriptSizeInBlock):
  sum over top-level txs of txNonDistinctRefScriptsSize = 10 × 0 KiB = 0 KiB < 1 MiB  ✓ PASS

Actual reference script deserialization cost for the block:
  10 × 190 KiB = 1.9 MiB  >> maxRefScriptSizePerBlock (1 MiB)

Result: block accepted by the ledger; all validating nodes must deserialize 1.9 MiB of
reference scripts, nearly double the intended block limit.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L361-363)
```haskell
  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL

  Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L183-187)
```haskell
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
```
