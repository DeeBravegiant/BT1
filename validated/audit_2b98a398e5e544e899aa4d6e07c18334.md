### Title
Block-Level Reference Script Size Limit Bypassed via Sub-Transaction Reference Scripts in Dijkstra Era — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs`)

---

### Summary

The `validateBodyRefScriptsSizeTooBig` check in the BBODY rule uses `totalRefScriptSizeInBlock`, which calls `txNonDistinctRefScriptsSize` and counts only top-level transaction reference scripts. In the Dijkstra era, the per-transaction LEDGER check (`validateAllRefScriptSize`) uses `batchNonDistinctRefScriptsSize`, which also counts sub-transaction reference scripts. This asymmetry allows a block producer to craft a block whose actual total reference script data processed by validating nodes exceeds `maxRefScriptSizePerBlock`, bypassing the intended resource cap.

---

### Finding Description

The Cardano Ledger codebase introduced reference script size limits in response to a real DDoS attack on June 25, 2024 (documented in `docs/adr/2024-08-14_009-refscripts-fee-change.md`). Two limits were introduced:

1. **Per-transaction limit** (`maxRefScriptSizePerTx`): enforced in the LEDGER rule.
2. **Per-block limit** (`maxRefScriptSizePerBlock`): enforced in the BBODY rule.

In the Dijkstra era, transactions can contain **sub-transactions** (`subTransactionsTxBodyL`). The per-transaction check correctly accounts for sub-transactions:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs
validateAllRefScriptSize pp utxo tx =
  let totalRefScriptSize = batchNonDistinctRefScriptsSize utxo tx  -- includes sub-txs
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $ ...
``` [1](#0-0) 

Where `batchNonDistinctRefScriptsSize` is:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs
batchNonDistinctRefScriptsSize utxo tx =
  txNonDistinctRefScriptsSize utxo tx
    + getSum (foldMap' (Sum . txNonDistinctRefScriptsSize utxo)
                       (tx ^. bodyTxL . subTransactionsTxBodyL))
``` [2](#0-1) 

However, the **block-level check** uses `totalRefScriptSizeInBlock`, which calls only `txNonDistinctRefScriptsSize` — it does **not** call `batchNonDistinctRefScriptsSize` and therefore **does not count sub-transaction reference scripts**:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs
totalRefScriptSizeInBlock protVer txs (UTxO utxo)
  | pvMajor protVer <= natVersion @10 = ...
  | otherwise =
      snd $ F.foldl' accum (utxo, 0) txs
  where
    accum (!accUtxo, !accSum) tx =
      ...
       in (updatedUtxo, accSum + txNonDistinctRefScriptsSize (UTxO accUtxo) tx)
       --                         ^^^ only top-level tx, no sub-txs
``` [3](#0-2) 

The block-level validation check:

```haskell
validateBodyRefScriptsSizeTooBig pp blockBody utxo =
  let totalSize = totalRefScriptSizeInBlock protVer txs utxo  -- misses sub-tx scripts
      maxSize = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerBlockG
   in totalSize <= maxSize ?! ...
``` [4](#0-3) 

---

### Impact Explanation

With mainnet Dijkstra parameters (`maxRefScriptSizePerBlock = 1,048,576` bytes, `maxRefScriptSizePerTx = 204,800` bytes):

- A block can contain approximately 5 top-level transactions before the block-level check triggers (5 × 204,800 ≈ 1,024,000 bytes).
- Each of those 5 top-level transactions can additionally carry sub-transactions with up to 204,800 bytes of reference scripts each — none of which are counted by the block-level check.
- Actual reference script data processed per block: up to ~1,048,576 + 5 × 204,800 = **~2,072,576 bytes** — approximately **2× the intended limit**.

This directly mirrors the Shardeum vulnerability class: attacker-controlled input data causes resource consumption (CPU for deserialization, RAM for buffering) exceeding the intended validation limit. The June 2024 Cardano DDoS attack exploited exactly this class of issue (large reference scripts expensive to deserialize).

**Impact**: Medium — attacker-controlled blocks exceed the intended `maxRefScriptSizePerBlock` validation limit, causing honest nodes to perform more reference script deserialization work than the protocol intends to permit per block.

---

### Likelihood Explanation

Any stake pool operator (SPO) who wins a slot leadership election can produce a block. This is a normal, permissionless role. No special privilege, key compromise, or governance majority is required. The attacker only needs to be elected as a slot leader for a single slot, which occurs routinely for any active SPO.

---

### Recommendation

`totalRefScriptSizeInBlock` should be updated to use `batchNonDistinctRefScriptsSize` (or an equivalent that traverses sub-transactions) when the protocol version is in the Dijkstra era or later. Alternatively, a Dijkstra-specific BBODY rule should override `validateBodyRefScriptsSizeTooBig` to use the batch-aware size calculation, consistent with how `validateAllRefScriptSize` in the LEDGER rule already operates.

---

### Proof of Concept

1. Activate Dijkstra era with `maxRefScriptSizePerTx = 204800` and `maxRefScriptSizePerBlock = 1048576`.
2. Produce 5 top-level transactions, each with:
   - Minimal top-level reference script data (e.g., 1 byte).
   - One sub-transaction carrying 204,799 bytes of reference scripts (just under the per-tx limit when combined with the top-level byte).
3. Package all 5 into a single block.
4. The BBODY check computes `totalRefScriptSizeInBlock` ≈ 5 bytes (top-level only) — well under `maxRefScriptSizePerBlock`. The block is accepted.
5. Each node validating the block must deserialize all sub-transaction reference scripts: 5 × 204,799 ≈ 1,024,000 bytes of additional script data beyond what the block-level limit was intended to permit.
6. Total reference script deserialization work: ~1,024,005 bytes ≈ 2× `maxRefScriptSizePerBlock`, reproducing the resource-exhaustion pattern of the June 2024 attack at the block level. [5](#0-4) [6](#0-5) [1](#0-0) [7](#0-6)

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L329-355)
```haskell
-- | Validate that total reference script size does not exceed block limit.
validateBodyRefScriptsSizeTooBig ::
  forall era.
  ( AlonzoEraTx era
  , BabbageEraTxBody era
  , InjectRuleFailure "BBODY" ConwayBbodyPredFailure era
  , EraBlockBody era
  , ConwayEraPParams era
  ) =>
  PParams era ->
  BlockBody era ->
  UTxO era ->
  Rule (EraRule "BBODY" era) 'Transition ()
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

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L15-19)
```markdown
It was identified a while ago that there is an overhead associated with deserializing scripts, so much so that it would be possible to create a very large script that was fairly expensive to deserialize, but very cheap to execute. This opened up an attack vector when such a Plutus script would be used as a reference script. This problem was exacerbated by the fact that there was no real limit on the total size of reference scripts that could be used in a transaction, thus being limited only by the size of the transaction itself. Therefore this opened up Cardano to a DDoS attack where an attacker could submit many such transactions that would cost very little, but would be expensive for a `cardano-node` to validate.

In order to prevent such an attack a `"minFeeRefScriptCostPerByte"` protocol parameter was introduced in the Conway era. The idea was fairly simple: we would calculate the total size of reference scripts used by a transaction and multiply it by the value specified by this protocol parameter. Result would be added to the transaction fee. However, in order for this approach to be a definite deterrent of such attacks this parameter would have to be set to a fairly high value. Community was not very keen on having this value set to a high enough value, since that could make reference scripts almost as expensive to use as regular scripts, thus significantly reducing their usability. Taking this fact into consideration, a decision was made to set this parameter to a fairly moderate value to at l ... (truncated)

Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```
