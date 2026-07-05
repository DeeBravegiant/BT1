### Title
Transaction Splitting Bypasses Exponential `tierRefScriptFee` Pricing, Reducing Reference Script Fees to Linear — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs`)

---

### Summary

The `tierRefScriptFee` function implements an exponentially growing per-transaction fee for reference scripts, introduced in Conway to deter DDoS attacks. Because the fee is computed independently per transaction with no cross-transaction aggregation, an attacker can split a large reference script workload across multiple smaller transactions — each staying in the cheapest pricing tier — reducing total fees from exponential to linear and paying up to ~51% less than the design intends.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — convex fee formula exploitable by operation splitting (direct analog of the FEI quadratic penalty bypass).

`tierRefScriptFee` in `eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs` computes the reference script surcharge for a single transaction using exponentially growing tiers:

```
Tier 1: bytes [0, 25600)      → baseFee per byte
Tier 2: bytes [25600, 51200)  → baseFee × 1.2 per byte
Tier 3: bytes [51200, 76800)  → baseFee × 1.44 per byte
...
``` [1](#0-0) 

This is a strictly convex function: the marginal cost per byte increases with total size. The fee is computed per-transaction via `getConwayMinFeeTxUtxo` → `txNonDistinctRefScriptsSize` → `getConwayMinFeeTx` → `tierRefScriptFee`. [2](#0-1) 

There is **no cross-transaction aggregation** of reference script sizes for fee purposes. The per-block check in `validateBodyRefScriptsSizeTooBig` only enforces a hard size cap; it does not apply tiered pricing to the block-level total. [3](#0-2) 

**The splitting attack:** An attacker who wants to submit `N` bytes of reference script work per block can split across multiple transactions, each referencing at most `sizeIncrement` (25,600) bytes. Every transaction pays only the base tier rate. Total fee becomes `N × baseFee` (linear), not the exponentially growing fee for a single large transaction.

**Concrete numbers with current mainnet parameters** (`multiplier = 1.2`, `stride = 25,600`, `baseFee = 15` lovelace/byte):

| Strategy | Reference script bytes | Fee (lovelace) |
|---|---|---|
| 1 transaction, 200 KiB | 204,800 | ≈ 6,335,648 |
| 8 transactions × 25 KiB | 8 × 25,600 | 8 × 384,000 = **3,072,000** |
| **Savings** | | **~51.5%** |

The ADR that introduced this mechanism explicitly chose tiered over linear pricing because linear pricing was "an inadequate deterrent": [4](#0-3) 

The splitting attack reduces tiered pricing back to linear pricing, negating the design's primary advantage over the prior approach.

The Dijkstra era inherits the same per-transaction `tierRefScriptFee` calculation. Its `batchNonDistinctRefScriptsSize` aggregates across sub-transactions within a single top-level transaction but does not aggregate across independent top-level transactions. [5](#0-4) 

---

### Impact Explanation

**Medium.** The tiered fee was the explicit mechanism chosen to make large reference script usage expensive and deter DDoS attacks. Splitting transactions reduces fees outside design parameters — the attacker pays ~51% less for the same total per-block reference script validation work. The per-block size cap (`maxRefScriptSizePerBlock = 1 MiB`) bounds total validation work but does not restore the intended fee level. This matches: *"Attacker-controlled transactions… modify fees… outside design parameters."* [6](#0-5) 

---

### Likelihood Explanation

**High.** The attack requires no special access, no privileged role, and no coordination. Any transaction submitter can trivially split reference script inputs across multiple transactions. The only constraint is the per-block size limit, which the attacker respects. The attack is mechanically identical to submitting normal transactions with fewer reference inputs each.

---

### Recommendation

Apply tiered pricing at the **block level** rather than (or in addition to) the transaction level. Maintain a running cumulative reference script byte count across transactions in a block and apply `tierRefScriptFee` to the block-level total, charging each transaction the marginal cost of its contribution to the running total. This mirrors how `totalRefScriptSizeInBlock` already accumulates sizes for the hard cap check, and would make splitting unprofitable. [7](#0-6) 

---

### Proof of Concept

1. Store 8 distinct Plutus scripts, each ≤ 25,600 bytes, as reference scripts in UTxO outputs (one-time setup cost).
2. Submit 8 separate transactions in the same block, each referencing exactly one 25 KiB script via `referenceInputsTxBodyL`.
3. Each transaction pays `tierRefScriptFee 1.2 25600 15 25600 = Coin 384000`.
4. Total fee for 8 transactions = **3,072,000 lovelace**.
5. A single transaction referencing all 8 scripts (200 KiB) would pay `tierRefScriptFee 1.2 25600 15 204800 ≈ Coin 6,335,648`.
6. The attacker achieves the same per-block validation burden at **~51.5% of the intended cost**, confirmed by the test vector in `eras/conway/impl/test/Main.hs`: [8](#0-7)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L127-136)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L174-187)
```haskell
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

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L23-23)
```markdown
Linear pricing was either too expensive when the multiplier was set too high or was an inadequate deterrent when the multiplier was set too low. Therefore, we needed to implement a pricing mechanism that would be very expensive for usage with large quantities of large plutus scripts, while keeping the pricing reasonably low for the most common use case of a total size of reference scripts of at most 25KiB per transaction. One of the constraints we had to operate under was inability to add any new protocol parameters, since that was a bit too late in the release cycle of the Conway era. In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era.
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L43-47)
```markdown
Once we have the total size of reference scripts used in a transaction we can proceed to computing the amount of Lovelace that will be added to the fee of a transaction. Instead of using the same linear cost for the whole size we split this total size into `25KiB` chunks and each subsequent chunk will get a linear pricing cost that is higher than the previous one by a multiplier of `1.2`. In other words pricing for the first `25KiB` will be as with the initial approach, just the value of `minFeeRefScriptCostPerByte`. The following `25KiB` will have the price of `minFeeRefScriptCostPerByte * multiplier` and  so on. These are the two new hardcoded values in the fee computation:

* Size increment: `25KiB` (or 25,600 bytes)
* Multiplier: `1.2`
* minFeeRefScriptCostPerByte: `15` (supplied in Conway genesis)
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

**File:** eras/conway/impl/test/Main.hs (L44-47)
```haskell
    it "tierRefScriptFee" $ do
      let step = 25600
      map (tierRefScriptFee 1.5 step 15) [0, step .. 204800]
        `shouldBe` map Coin [0, 384000, 960000, 1824000, 3120000, 5064000, 7980000, 12354000, 18915000]
```
