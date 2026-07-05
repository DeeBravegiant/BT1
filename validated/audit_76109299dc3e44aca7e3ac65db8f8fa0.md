### Title
Block-Level Reference Script Size Limit Bypassed via Sub-Transaction Reference Scripts in Dijkstra Era — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

---

### Summary

The Dijkstra era introduces nested sub-transactions. The block-level reference script size guard (`validateBodyRefScriptsSizeTooBig`) is reused from Conway and only measures reference scripts belonging to top-level transactions. It is blind to reference scripts referenced by sub-transactions. An attacker can craft top-level transactions that carry zero top-level reference-script bytes but whose sub-transactions collectively reference arbitrarily large reference scripts, bypassing the block-level cap and forcing every validating node to deserialize far more reference-script bytes per block than the protocol intends.

---

### Finding Description

**Vulnerability class:** Resource-limit bypass — attacker-inflatable work per block via an unbounded sub-transaction reference-script aggregate that is not covered by the block-level guard.

**Root cause — two divergent size measurements:**

The Dijkstra BBODY rule (`dijkstraBbodyTransition`) calls the Conway block-level guard unchanged:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs : 363
Conway.validateBodyRefScriptsSizeTooBig @era pp blockBody (ls ^. utxoL)
``` [1](#0-0) 

That guard calls `totalRefScriptSizeInBlock`, which iterates over the top-level transaction sequence and calls `txNonDistinctRefScriptsSize` on each:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs : 357-370
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 =
      getSum $ foldMap' (Monoid.Sum . txNonDistinctRefScriptsSize (UTxO utxo)) txs
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
``` [2](#0-1) 

`txNonDistinctRefScriptsSize` only inspects `referenceInputsTxBodyL` and `inputsTxBodyL` of the **top-level** transaction body — it has no knowledge of sub-transactions:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs : 183-187
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
``` [3](#0-2) 

By contrast, the **per-transaction** check in the Dijkstra LEDGER rule uses the Dijkstra-specific `batchNonDistinctRefScriptsSize`, which correctly sums the top-level tx **and all sub-transactions**:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs : 263-277
-- | Total size of reference scripts across a top-level transaction and all its subtransactions.
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum
      ( foldMap'
          (Sum . txNonDistinctRefScriptsSize utxo)
          (tx ^. bodyTxL . subTransactionsTxBodyL)
      )
``` [4](#0-3) 

The per-transaction check in `validateAllRefScriptSize` enforces `batchNonDistinctRefScriptsSize ≤ ppMaxRefScriptSizePerTx`: [5](#0-4) 

**The gap:** The block-level guard measures only top-level reference scripts; the per-transaction guard measures the full batch. An attacker can set top-level reference inputs to zero while loading all reference-script bytes into sub-transactions. The block-level guard sees 0 bytes; the per-transaction guard allows up to `ppMaxRefScriptSizePerTx` (200 KiB) per top-level transaction.

**Exploit path:**

1. Attacker pre-populates the UTxO with many large reference-script outputs (each up to the script size limit). This is a normal, fee-paying operation.
2. Attacker crafts a top-level Dijkstra transaction with:
   - Zero top-level reference inputs (block-level guard sees 0 bytes).
   - N sub-transactions, each referencing UTxO entries with large reference scripts, with the aggregate sub-tx reference-script size ≤ `ppMaxRefScriptSizePerTx` (per-tx guard passes).
3. Attacker fills a block with many such top-level transactions. Each passes both guards individually.
4. The block-level guard accumulates 0 bytes across all top-level transactions and passes.
5. Every validating node must deserialize the reference scripts for every sub-transaction in every top-level transaction — potentially many multiples of `ppMaxRefScriptSizePerBlock` (1 MiB) of deserialization work per block.

With `ppMaxRefScriptSizePerTx = 200 KiB` and a block body size of ~90 KiB, a block can contain on the order of tens of top-level transactions (each small because reference inputs are just `TxIn` pointers). Each carries up to 200 KiB of sub-tx reference-script deserialization work, yielding total per-block deserialization work that can be an order of magnitude above the intended 1 MiB block cap.

The sub-transaction `OMap` has no count limit beyond the transaction size limit, and the transaction size limit is measured on the serialized transaction body — which only contains `TxIn` pointers to UTxO entries, not the scripts themselves. This is the exact structural analog to the ERC-1155 `delegatesOf` array inflation: an attacker-controlled list that is iterated over during validation, with the list size not bounded by the guard that is supposed to cap the work. [6](#0-5) 

---

### Impact Explanation

**Medium — attacker-controlled transactions exceed intended validation limits.**

The block-level reference-script size cap (`ppMaxRefScriptSizePerBlock`) was introduced specifically to prevent DDoS via reference-script deserialization overhead (documented in `docs/adr/2024-08-14_009-refscripts-fee-change.md` following the June 2024 mainnet attack). The bypass allows an attacker to force every honest node to perform reference-script deserialization work that is unbounded relative to the block limit, degrading block validation throughput. This matches the allowed impact: *"Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits."* [7](#0-6) 

---

### Likelihood Explanation

**Medium.** The attacker requires only the ability to submit valid Dijkstra-era transactions — no privileged keys, no governance majority, no Sybil attack. The cost is the fee to create UTxO entries containing large reference scripts (a one-time setup cost) plus the fees for the attack transactions themselves. The Dijkstra era is the current development era; the vulnerability is present in the production code path as written.

---

### Recommendation

Replace the call to `Conway.validateBodyRefScriptsSizeTooBig` in `dijkstraBbodyTransition` with a Dijkstra-specific block-level guard that uses `batchNonDistinctRefScriptsSize` (or an equivalent that sums reference-script sizes across both top-level transactions and all their sub-transactions) when computing the block aggregate. Concretely, `totalRefScriptSizeInBlock` should be extended or replaced with a variant that, for each top-level transaction, calls `batchNonDistinctRefScriptsSize` instead of `txNonDistinctRefScriptsSize`.

---

### Proof of Concept

```
Setup:
  1. Attacker submits K transactions, each creating a UTxO output with a large
     reference script (e.g., a 10 KiB Plutus script). Total UTxO reference
     script pool: K × 10 KiB.

Attack block construction:
  2. For each top-level transaction T_i in the attack block:
       - T_i.inputs        = { some_utxo_coin_input }   (no reference scripts)
       - T_i.referenceInputs = {}                        (block guard sees 0 bytes)
       - T_i.subTransactions = [
           SubTx_1 { referenceInputs = { script_utxo_1, ..., script_utxo_20 } },
           SubTx_2 { referenceInputs = { script_utxo_21, ..., script_utxo_40 } },
           ...
         ]
       batchNonDistinctRefScriptsSize(T_i) = 200 KiB  ← per-tx check passes
       txNonDistinctRefScriptsSize(T_i)    =   0 bytes ← block check sees 0

  3. Pack M such transactions into one block.
       Block-level guard total = 0 bytes  → passes ppMaxRefScriptSizePerBlock (1 MiB)
       Actual deserialization  = M × 200 KiB

  With M = 20 (easily achievable given block body size limits and small tx bodies):
       Actual deserialization = 4 MiB per block vs. 1 MiB intended cap.
```

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L363-363)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L183-187)
```haskell
txNonDistinctRefScriptsSize :: (EraTx era, BabbageEraTxBody era) => UTxO era -> Tx l era -> Int
txNonDistinctRefScriptsSize utxo tx = getSum $ foldMap (Sum . originalBytesSize . snd) refScripts
  where
    inputs = (tx ^. bodyTxL . referenceInputsTxBodyL) `Set.union` (tx ^. bodyTxL . inputsTxBodyL)
    refScripts = getReferenceScriptsNonDistinct utxo inputs
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

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
