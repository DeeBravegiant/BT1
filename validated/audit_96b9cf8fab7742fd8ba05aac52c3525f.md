### Title
Hardcoded Reference Script Fee Curve Parameters in Conway Era Cannot Be Adjusted by Governance - (`eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

### Summary
In the Conway era, four parameters governing reference script fee calculation and size enforcement are hardcoded as compile-time constants rather than governance-adjustable protocol parameters. The `ConwayEraPParams` instance returns constant values for `ppMaxRefScriptSizePerTxG` (200 KiB), `ppMaxRefScriptSizePerBlockG` (1 MiB), `ppRefScriptCostMultiplierG` (1.2), and `ppRefScriptCostStrideG` (25,600 bytes). Unlike `minFeeRefScriptCostPerByte`, which is a proper protocol parameter, these four values cannot be changed by any governance action — only by a hard fork. If the hardcoded fee curve is insufficient to cover the actual computational cost of deserializing and processing large reference scripts, an attacker can submit transactions that impose more validator work than the fee paid, and the community has no on-chain mechanism to respond.

### Finding Description
The `ConwayEraPParams` instance in `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs` implements the four reference-script parameters as constant getters:

```haskell
ppMaxRefScriptSizePerTxG    = L.to . const $ 200 * 1024
ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
ppRefScriptCostMultiplierG  = L.to . const . fromJust $ boundRational 1.2
ppRefScriptCostStrideG      = L.to . const $ knownNonZeroBounded @25_600
```

These are consumed directly in `getConwayMinFeeTx` (`eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs`) to compute the tiered reference-script fee:

```haskell
refScriptsFee =
  tierRefScriptFee
    (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
    (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
    refScriptCostPerByte
    refScriptsSize
```

The `tierRefScriptFee` function applies the multiplier and stride to produce an exponentially growing fee curve. Because the multiplier and stride are hardcoded constants in Conway, the shape of the fee curve is immutable for the entire era. The ADR (`docs/adr/2024-08-14_009-refscripts-fee-change.md`) explicitly acknowledges this:

> "One of the constraints we had to operate under was inability to add any new protocol parameters, since that was a bit too late in the release cycle of the Conway era. In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era."

The Dijkstra era corrects this by promoting all four values to proper protocol parameters (`dppRefScriptCostStride`, `dppRefScriptCostMultiplier`, `dppMaxRefScriptSizePerBlock`, `dppMaxRefScriptSizePerTx` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs`), confirming the Conway design is a known deficiency.

The root cause is structurally identical to the external report: a single hardcoded constant is applied uniformly where the correct value depends on context (actual deserialization cost, Plutus language version, hardware generation), and governance has no mechanism to correct it.

### Impact Explanation
An attacker can submit transactions carrying reference scripts up to the hardcoded 200 KiB per-transaction limit. The fee charged is determined solely by the hardcoded multiplier (1.2) and stride (25,600 bytes). If the actual CPU/memory cost of deserializing those scripts exceeds what the fee curve recovers — for example, after a new Plutus language version is introduced with higher deserialization overhead, or if the initial calibration was insufficient — every such transaction imposes a net computational deficit on block-producing and validating nodes. Because the multiplier and stride cannot be raised by governance, the community cannot close this gap without a hard fork. This constitutes fee modification outside design parameters: the fee paid by the attacker is structurally decoupled from the actual validation cost, and the protocol has no on-chain remedy.

This maps to the **Medium** allowed impact: *Attacker-controlled transactions exceed intended validation limits or modify fees outside design parameters.*

### Likelihood Explanation
The June 2024 DDoS attack on Cardano (referenced in the ADR) demonstrated that reference-script deserialization cost is a real and actively exploited attack surface. The hardcoded values were calibrated against the cost model at the time of the Conway release. Any subsequent change to Plutus interpreter cost models, introduction of a new script language, or shift in hardware economics can silently invalidate the calibration. Because the parameters are immutable in Conway, no governance response is possible until the Dijkstra hard fork activates. The attacker entry path requires only the ability to submit a standard transaction — no privileged access, no key compromise, no consensus majority.

### Recommendation
The Dijkstra era already provides the correct fix: promote `ppRefScriptCostMultiplierG`, `ppRefScriptCostStrideG`, `ppMaxRefScriptSizePerTxG`, and `ppMaxRefScriptSizePerBlockG` to proper protocol parameters governed by the on-chain update mechanism, exactly as done in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs`. For the Conway era specifically, the community should ensure the Dijkstra hard fork is activated before any event (new Plutus version, cost-model update) that could shift the actual deserialization cost above what the hardcoded curve recovers.

### Proof of Concept
1. Observe that `ConwayEraPParams` returns compile-time constants for all four parameters: [1](#0-0) 

2. Observe that `getConwayMinFeeTx` consumes these constants to compute the fee: [2](#0-1) 

3. Observe that `tierRefScriptFee` applies the multiplier and stride to produce the fee curve: [3](#0-2) 

4. Confirm the ADR explicitly acknowledges the hardcoding and the intent to fix it in the next era: [4](#0-3) [5](#0-4) 

5. Confirm Dijkstra promotes these to proper protocol parameters, proving the Conway design is deficient: [6](#0-5)

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L151-157)
```haskell
  , dppMaxRefScriptSizePerBlock :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of all reference scripts combined from
  -- all transactions within a block.
  , dppMaxRefScriptSizePerTx :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of reference scripts that a transaction can use.
  , dppRefScriptCostStride :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f (NonZero Word32))
  , dppRefScriptCostMultiplier :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f PositiveInterval)
```
