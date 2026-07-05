### Title
Tiered Reference-Script Fee Is Per-Transaction, Allowing Splitting to Reduce Total Fees Below Design Intent - (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs`)

### Summary

The Conway/Dijkstra era introduced `tierRefScriptFee`, a convex (exponentially increasing) pricing function for reference scripts, specifically to deter DDoS attacks by making large reference-script usage expensive. Because the function is applied **per transaction** rather than cumulatively across a block, any transaction submitter can split reference-script usage across multiple smaller transactions and pay a materially lower total fee than a single transaction carrying the same aggregate script bytes would require. This directly undermines the stated security goal of the tiered pricing mechanism.

### Finding Description

`tierRefScriptFee` in `eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs` computes the reference-script surcharge for a single transaction by dividing the total script bytes into `sizeIncrement`-byte (25 KiB) chunks, with each successive chunk priced at `multiplier` (1.2×) times the previous chunk's rate: [1](#0-0) 

`getConwayMinFeeTxUtxo` calls this function with the reference-script size of the **current transaction only**: [2](#0-1) 

Because `tierRefScriptFee` is a strictly convex function (multiplier > 1), the fee for a single transaction carrying `N` bytes of reference scripts is **greater** than the sum of fees for `k` transactions each carrying `N/k` bytes. The block-level size check (`validateBodyRefScriptsSizeTooBig`) enforces a hard cap on total bytes per block but does **not** aggregate fees across transactions: [3](#0-2) 

The ADR that introduced this mechanism explicitly states the goal was to make large reference-script usage "very expensive": [4](#0-3) 

### Impact Explanation

**Medium — Attacker-controlled transactions modify fees outside design parameters.**

With Conway mainnet parameters (stride = 25,600 bytes, multiplier = 1.2, base = 15 lovelace/byte):

| Strategy | Script bytes | Fee (lovelace) |
|---|---|---|
| 1 tx × 204,800 bytes | 204,800 | ≈ 6,335,600 |
| 8 txs × 25,600 bytes | 204,800 | 3,072,000 |
| **Savings** | | **≈ 3,263,600 (≈51.5%)** |

An attacker wishing to flood the network with expensive-to-deserialize reference scripts can split their load into 25 KiB chunks, each priced at the base tier, and pay roughly half the fee that the tiered mechanism was designed to impose. The total node-side deserialization cost is unchanged; only the attacker's cost is reduced. This partially restores the DDoS attack surface that the tiered pricing was introduced to close after the June 2024 mainnet attack.

### Likelihood Explanation

**High.** No special privilege is required. Any transaction submitter can observe that splitting reference-script inputs across multiple transactions reduces fees. The splitting is trivially achievable: instead of one transaction referencing eight 25 KiB script UTxOs, submit eight transactions each referencing one. The only constraint is the per-block size limit (1 MiB), which still applies to the total bytes but not to the fee calculation.

### Recommendation

Apply `tierRefScriptFee` to the **cumulative** reference-script bytes seen so far in the block rather than resetting the tier counter for each transaction. `totalRefScriptSizeInBlock` already accumulates the running total across transactions in a block: [5](#0-4) 

The block-body rule could compute a per-block cumulative fee using the same tiered function applied to the running total, and verify that the sum of individual transaction fees is at least that amount. Alternatively, the per-transaction fee could be computed as `tierRefScriptFee(cumulative + txSize) - tierRefScriptFee(cumulative)` (the marginal cost), which is exactly the approach the external report's mitigation suggests (using the midpoint/marginal value rather than the endpoint value).

### Proof of Concept

Using Conway defaults (stride = 25,600, multiplier = 1.2, base = 15 lovelace/byte):

```
-- Single transaction with 2 × 25 KiB = 51,200 bytes of reference scripts:
tierRefScriptFee 1.2 25600 15 51200
  = 25600 × 15  +  25600 × 18
  = 384,000 + 460,800
  = 844,800 lovelace

-- Two transactions each with 25,600 bytes:
2 × tierRefScriptFee 1.2 25600 15 25600
  = 2 × (25600 × 15)
  = 2 × 384,000
  = 768,000 lovelace

-- Savings: 844,800 − 768,000 = 76,800 lovelace (≈9.1%)

-- Eight transactions each with 25,600 bytes (equivalent to one max-size tx):
8 × tierRefScriptFee 1.2 25600 15 25600
  = 8 × 384,000
  = 3,072,000 lovelace

-- vs. one transaction with 204,800 bytes:
tierRefScriptFee 1.2 25600 15 204800
  ≈ 6,335,600 lovelace

-- Savings: ≈ 3,263,600 lovelace (≈51.5%)
```

The test in `eras/conway/impl/test/Main.hs` confirms that when `multiplier = 1` (linear pricing) the function is additive and splitting has no effect, but with `multiplier = 1.5` (or 1.2 on mainnet) the convexity is explicit: [6](#0-5)

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

**File:** eras/conway/impl/test/Main.hs (L41-47)
```haskell
    prop "tierRefScriptFee is a linear function when growth is 1" $ \(Positive sizeIncrement) baseFee (NonNegative size) ->
      tierRefScriptFee 1 sizeIncrement baseFee size
        === Coin (floor (fromIntegral size * baseFee))
    it "tierRefScriptFee" $ do
      let step = 25600
      map (tierRefScriptFee 1.5 step 15) [0, step .. 204800]
        `shouldBe` map Coin [0, 384000, 960000, 1824000, 3120000, 5064000, 7980000, 12354000, 18915000]
```
