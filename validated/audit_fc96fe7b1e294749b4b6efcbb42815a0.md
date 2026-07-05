### Title
Hardcoded Reference Script Fee Parameters and Size Limits Cannot Be Updated via Governance in Conway Era — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

---

### Summary

In the Conway era, four critical parameters governing reference script fee calculation and block/transaction size limits are hardcoded as compile-time constants in the `ConwayEraPParams ConwayEra` instance. Unlike every other protocol parameter in Conway, these values cannot be updated via on-chain governance. If the hardcoded fee multiplier or stride proves insufficient to price out a reference-script DDoS attack, the community has no on-chain remedy — only a hard fork can change them. This is a direct analog to the "magic number" class: undocumented (in the source code itself) hardcoded numeric values embedded in security-critical validation logic with no governance escape hatch.

---

### Finding Description

In `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`, the `ConwayEraPParams ConwayEra` instance provides four getter lenses that return compile-time constants rather than reading from the stored `PParams` record:

```haskell
ppMaxRefScriptSizePerTxG    = L.to . const $ 200 * 1024
ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
ppRefScriptCostMultiplierG  = L.to . const . fromJust $ boundRational 1.2
ppRefScriptCostStrideG      = L.to . const $ knownNonZeroBounded @25_600
``` [1](#0-0) 

These four values are consumed in three distinct production validation paths:

**1. Per-transaction minimum fee** (`getConwayMinFeeTx`): the multiplier `1.2` and stride `25_600` drive the exponential tier pricing in `tierRefScriptFee`, which is added to every Conway transaction's minimum fee. [2](#0-1) 

**2. Per-transaction size enforcement** (`validateRefScriptSize`): the hardcoded `200 * 1024` (204,800 bytes) is the ceiling against which `txNonDistinctRefScriptsSize` is compared; exceeding it produces `ConwayTxRefScriptsSizeTooBig`. [3](#0-2) 

**3. Per-block size enforcement** (`validateBodyRefScriptsSizeTooBig`): the hardcoded `1024 * 1024` (1,048,576 bytes) is the block-level ceiling; exceeding it produces `BodyRefScriptsSizeTooBig`. [4](#0-3) 

The `ConwayEraPParams` type class defines these four methods specifically so that the Dijkstra era can override them with real protocol-parameter lenses — which it does, storing them in `dppRefScriptCostMultiplier`, `dppRefScriptCostStride`, `dppMaxRefScriptSizePerTx`, and `dppMaxRefScriptSizePerBlock`. [5](#0-4) 

ADR 009 explicitly acknowledges the situation: *"we had to hard code some values, which will be turned into proper protocol parameters in the next era"* and *"Hard caps that are currently hard coded, but will be turned into actual protocol parameters in the next era after Conway."* [6](#0-5) [7](#0-6) 

No governance action — `ParameterChange`, `HardForkInitiation`, or any other — can alter these four values while the chain remains in the Conway era.

---

### Impact Explanation

**Fee manipulation outside design parameters (Medium).** The tiered fee formula charges approximately 6.3 ADA for a transaction carrying the maximum 204,800 bytes of reference scripts (at `minFeeRefScriptCostPerByte = 15`, multiplier `1.2`, stride `25,600`). If the actual CPU/memory cost to deserialize and validate that payload exceeds what 6.3 ADA covers at current node hardware, an attacker can submit many such transactions at a net profit relative to the damage inflicted — exactly the DDoS vector that struck mainnet on 25 June 2024. Because the multiplier and stride are compile-time constants, the community cannot raise them via a `ParameterChange` governance action; the fee floor is permanently fixed for the entire Conway era. This constitutes fees being set "outside design parameters" in the sense that the governance mechanism that is supposed to control them is bypassed.

**Resource-limit bypass (Medium).** The per-block cap of 1 MiB and per-transaction cap of 200 KiB are similarly frozen. If a future script format or compression technique makes it cheap to embed large payloads, the community cannot tighten these limits without a hard fork.

---

### Likelihood Explanation

The reference-script DDoS attack of 25 June 2024 is direct evidence that this attack class is actively exploited on Cardano mainnet. [8](#0-7) 

The hardcoded values were chosen as a rapid response under time pressure ("a bit too late in the release cycle"). The multiplier `1.2` was a community compromise — the community rejected higher values to preserve DApp usability. If hardware costs fall, script sizes grow, or a new deserialization-heavy script format is introduced, the fixed pricing becomes inadequate with no on-chain remedy. Likelihood is **medium**: the values are not trivially wrong today, but the inability to adjust them creates a persistent, unmitigable exposure for the entire Conway era.

---

### Recommendation

1. **Short term**: Add inline comments to lines 980–983 of `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs` documenting the rationale for each magic value, the ADR reference, and the fact that they are intentionally non-governable in Conway.
2. **Medium term** (already planned): The Dijkstra era correctly promotes all four values to proper `PParams` fields (`dppRefScriptCostMultiplier`, `dppRefScriptCostStride`, `dppMaxRefScriptSizePerTx`, `dppMaxRefScriptSizePerBlock`) with governance update support. Ensure the Conway→Dijkstra translation initialises these fields from the hardcoded Conway values so the effective policy is continuous across the era boundary.
3. **Process**: Treat any future "hardcoded-because-too-late-in-release-cycle" value as a security debt item requiring a tracked issue and a concrete era-upgrade plan before merging.

---

### Proof of Concept

An unprivileged transaction author proceeds as follows in the Conway era:

1. Construct a Plutus V2 or V3 reference script whose serialised size is close to the per-transaction cap: `totalRefScriptSize ≈ 204,800 bytes` (the hardcoded `200 * 1024`).
2. Place the script in a UTxO output, then reference it via `referenceInputs` in a new transaction. `txNonDistinctRefScriptsSize` will return ≈ 204,800.
3. `validateRefScriptSize` compares against the hardcoded constant and passes.
4. `getConwayMinFeeTx` calls `tierRefScriptFee 1.2 25600 baseCostPerByte 204800`. With `baseCostPerByte = 15` (the genesis value), the tiered fee adds ≈ 6.3 ADA to the base transaction fee.
5. If the node's actual deserialization cost for 204,800 bytes of Plutus script exceeds what 6.3 ADA covers, the attacker pays less than the true cost.
6. The attacker repeats this across many transactions up to the block cap (`1024 * 1024` bytes, hardcoded at line 981), filling each block with maximally expensive-to-validate reference script payloads.
7. A governance `ParameterChange` proposal to raise the multiplier or lower the caps is **impossible** in Conway: `ppRefScriptCostMultiplierG` and `ppRefScriptCostStrideG` are `L.to . const` getters with no corresponding setter or `PParamsUpdate` field — the governance machinery has no path to alter them. [1](#0-0) [9](#0-8) [3](#0-2) [4](#0-3)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L980-983)
```haskell
  ppMaxRefScriptSizePerTxG = L.to . const $ 200 * 1024
  ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
  ppRefScriptCostMultiplierG = L.to . const . fromJust $ boundRational 1.2
  ppRefScriptCostStrideG = L.to . const $ knownNonZeroBounded @25_600
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L462-471)
```haskell
validateRefScriptSize pp utxo tx =
  let totalRefScriptSize = txNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $
        ( ConwayTxRefScriptsSizeTooBig
            Mismatch
              { mismatchSupplied = totalRefScriptSize
              , mismatchExpected = maxRefScriptSizePerTx
              }
        )
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L584-587)
```haskell
  ppMaxRefScriptSizePerTxG = ppLensHKD . hkdMaxRefScriptSizePerTxL
  ppMaxRefScriptSizePerBlockG = ppLensHKD . hkdMaxRefScriptSizePerBlockL
  ppRefScriptCostMultiplierG = ppLensHKD . hkdRefScriptCostMultiplierL
  ppRefScriptCostStrideG = ppLensHKD . hkdRefScriptCostStrideL
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L19-19)
```markdown
Unfortunately things did not go exactly as planned, because on [June 25th 2024 an attack like this actually took place](https://cardanospot.io/news/ddos-attack-on-the-cardano-blockchain-mRIKAzZTNnzq5NGd). This attack forced us to make a quick decision on implementing a definite prevention of such attacks, while trying to not have a drastic impact on the common use case that DApp developers rely on so much.
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L23-23)
```markdown
Linear pricing was either too expensive when the multiplier was set too high or was an inadequate deterrent when the multiplier was set too low. Therefore, we needed to implement a pricing mechanism that would be very expensive for usage with large quantities of large plutus scripts, while keeping the pricing reasonably low for the most common use case of a total size of reference scripts of at most 25KiB per transaction. One of the constraints we had to operate under was inability to add any new protocol parameters, since that was a bit too late in the release cycle of the Conway era. In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era.
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L75-78)
```markdown
Hard caps that are currently hard coded, but will be turned into actual protocol parameters in the next era after Conway:

* Limit per transaction: `200KiB` (or `204800` bytes)
* Limit per block: `1MiB` (or `1048576` bytes)
```
