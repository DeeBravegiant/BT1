### Title
Hardcoded Reference Script Resource Limits and Fee Curve Parameters in Conway Era Cannot Be Adjusted via Governance — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

---

### Summary

In the Conway era, four critical reference-script parameters — `ppMaxRefScriptSizePerTxG`, `ppMaxRefScriptSizePerBlockG`, `ppRefScriptCostMultiplierG`, and `ppRefScriptCostStrideG` — are hardcoded as compile-time constants in the `ConwayEraPParams ConwayEra` instance. Unlike every other protocol parameter, these four values are not stored in `ConwayPParams` and therefore cannot be updated through on-chain governance. If the actual node cost of deserializing reference scripts exceeds what these constants imply, governance has no mechanism to raise the limits or reshape the pricing curve without a hard fork. An unprivileged transaction sender can exploit this by submitting transactions with large reference scripts at a fee that is lower than the actual processing cost, repeating the class of attack that occurred on-chain in June 2024.

---

### Finding Description

The `ConwayEraPParams` type class declares four getters as `SimpleGetter` rather than updatable lenses:

```haskell
ppMaxRefScriptSizePerTxG    :: SimpleGetter (PParams era) Word32
ppMaxRefScriptSizePerBlockG :: SimpleGetter (PParams era) Word32
ppRefScriptCostMultiplierG  :: SimpleGetter (PParams era) PositiveInterval
ppRefScriptCostStrideG      :: SimpleGetter (PParams era) (NonZero Word32)
``` [1](#0-0) 

The `ConwayEra` instance resolves all four to compile-time constants, ignoring the `PParams` argument entirely:

```haskell
ppMaxRefScriptSizePerTxG    = L.to . const $ 200 * 1024
ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
ppRefScriptCostMultiplierG  = L.to . const . fromJust $ boundRational 1.2
ppRefScriptCostStrideG      = L.to . const $ knownNonZeroBounded @25_600
``` [2](#0-1) 

The `ConwayPParams` record has no fields for these four values, so no governance `ParameterChange` action can ever set them. [3](#0-2) 

These constants are consumed in two enforcement paths:

**1. Per-transaction size limit** (`validateRefScriptSize` in `Conway/Rules/Ledger.hs`):

```haskell
maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) ...
``` [4](#0-3) 

**2. Per-block size limit** (`validateBodyRefScriptsSizeTooBig` in `Conway/Rules/Bbody.hs`):

```haskell
maxSize = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerBlockG
``` [5](#0-4) 

**3. Fee curve shape** (`getConwayMinFeeTx` in `Conway/Tx.hs`):

```haskell
tierRefScriptFee
  (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
  (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
  refScriptCostPerByte
  refScriptsSize
``` [6](#0-5) 

The ADR explicitly acknowledges this design choice and states the values will become proper protocol parameters in the next era:

> "One of the constraints we had to operate under was inability to add any new protocol parameters… we had to hard code some values, which will be turned into proper protocol parameters in the next era." [7](#0-6) 

The Dijkstra era correctly fixes this by storing all four as proper `DijkstraPParams` fields with governance-updatable lenses:

```haskell
ppMaxRefScriptSizePerTxG    = ppLensHKD . hkdMaxRefScriptSizePerTxL
ppMaxRefScriptSizePerBlockG = ppLensHKD . hkdMaxRefScriptSizePerBlockL
ppRefScriptCostMultiplierG  = ppLensHKD . hkdRefScriptCostMultiplierL
ppRefScriptCostStrideG      = ppLensHKD . hkdRefScriptCostStrideL
``` [8](#0-7) 

---

### Impact Explanation

**Impact: Medium** — attacker-controlled transactions exceed intended validation limits and pay fees outside design parameters.

The hardcoded `ppMaxRefScriptSizePerTxG = 200 KiB` and `ppMaxRefScriptSizePerBlockG = 1 MiB` set the ceiling for how much reference-script data can appear per transaction and per block. The hardcoded multiplier (`1.2`) and stride (`25 600` bytes) define the shape of the tiered pricing curve used in `tierRefScriptFee`. If the actual deserialization cost of reference scripts is higher than what this curve produces — or if a future node implementation change raises that cost — governance has no lever to raise the limits or steepen the curve. An attacker can therefore submit transactions with reference scripts priced at the current (potentially insufficient) rate, consuming more node CPU/memory than the fee covers. The only remediation path is a hard fork, exactly as occurred after the June 2024 on-chain attack that the tiered pricing was designed to prevent.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires no special privilege: any transaction sender can include reference inputs pointing to large scripts. The June 2024 attack demonstrated that this class of attack is actively exploited on mainnet when pricing is insufficient. The hardcoded values were calibrated against the deserialization cost at the time of the Conway release; any change to the Plutus evaluator, CBOR decoder, or script representation that increases per-byte cost would silently make the hardcoded pricing insufficient with no governance recourse. The window of exposure lasts for the entire Conway era until a Dijkstra hard fork is enacted.

---

### Recommendation

1. **Short-term (Conway era)**: Monitor actual node deserialization benchmarks against the hardcoded pricing curve. If the curve is found insufficient, prepare an emergency hard fork rather than waiting for the scheduled Dijkstra upgrade.
2. **Long-term (already addressed in Dijkstra)**: The Dijkstra era correctly promotes all four values to proper `DijkstraPParams` fields with governance-updatable lenses (`ppMaxRefScriptSizePerBlockL`, `ppMaxRefScriptSizePerTxL`, `ppRefScriptCostStrideL`, `ppRefScriptCostMultiplierL`), allowing governance to respond without a hard fork. [9](#0-8) 

---

### Proof of Concept

1. On a Conway-era node, query the current `minFeeRefScriptCostPerByte` (e.g., 15 lovelace/byte).
2. Craft a transaction with a reference input pointing to a UTxO containing a Plutus script of exactly 25 599 bytes (just under the first tier boundary of 25 600 bytes). The fee surcharge is `floor(25599 * 15) = 383 985` lovelace.
3. Craft a second transaction with a reference input pointing to a script of 200 000 bytes (near the per-tx cap). The fee surcharge is computed by `tierRefScriptFee 1.2 25600 15 200000`.
4. Submit both transactions. Both are accepted because `totalRefScriptSize <= 204800` and the fee satisfies `getConwayMinFeeTx`.
5. Attempt to submit a governance `ParameterChange` action that sets `maxRefScriptSizePerTx = 50000` or `refScriptCostMultiplier = 1.5`. The action is rejected at the CDDL/serialization layer because `ConwayPParams` has no fields for these values and the `ppuWellFormed` check does not include them — confirming that governance cannot adjust these limits in Conway era. [10](#0-9)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L193-196)
```haskell
  ppMaxRefScriptSizePerTxG :: SimpleGetter (PParams era) Word32
  ppMaxRefScriptSizePerBlockG :: SimpleGetter (PParams era) Word32
  ppRefScriptCostMultiplierG :: SimpleGetter (PParams era) PositiveInterval
  ppRefScriptCostStrideG :: SimpleGetter (PParams era) (NonZero Word32)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L642-730)
```haskell
data ConwayPParams f era = ConwayPParams
  { cppTxFeePerByte :: !(THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f CoinPerByte)
  -- ^ The linear factor for the minimum fee calculation
  , cppTxFeeFixed :: !(THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f (CompactForm Coin))
  -- ^ The constant factor for the minimum fee calculation
  , cppMaxBBSize :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Maximal block body size
  , cppMaxTxSize :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Maximal transaction size
  , cppMaxBHSize :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word16)
  -- ^ Maximal block header size
  , cppKeyDeposit :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f (CompactForm Coin))
  -- ^ The amount of a key registration deposit
  , cppPoolDeposit :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f (CompactForm Coin))
  -- ^ The amount of a pool registration deposit
  , cppEMax :: !(THKD ('PPGroups 'TechnicalGroup 'NoStakePoolGroup) f EpochInterval)
  -- ^ Maximum number of epochs in the future a pool retirement is allowed to
  -- be scheduled for.
  , cppNOpt :: !(THKD ('PPGroups 'TechnicalGroup 'NoStakePoolGroup) f Word16)
  -- ^ Desired number of pools
  , cppA0 :: !(THKD ('PPGroups 'TechnicalGroup 'NoStakePoolGroup) f NonNegativeInterval)
  -- ^ Pool influence
  , cppRho :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f UnitInterval)
  -- ^ Monetary expansion
  , cppTau :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f UnitInterval)
  -- ^ Treasury expansion
  , cppProtocolVersion :: !(HKDNoUpdate f ProtVer)
  -- ^ Protocol version
  , cppMinPoolCost :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f (CompactForm Coin))
  -- ^ Minimum Stake Pool Cost
  , cppCoinsPerUTxOByte :: !(THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f CoinPerByte)
  -- ^ Cost in lovelace per byte of UTxO storage
  , cppCostModels :: !(THKD ('PPGroups 'TechnicalGroup 'NoStakePoolGroup) f CostModels)
  -- ^ Cost models for non-native script languages
  , cppPrices :: !(THKD ('PPGroups 'EconomicGroup 'NoStakePoolGroup) f Prices)
  -- ^ Prices of execution units (for non-native script languages)
  , cppMaxTxExUnits :: !(THKD ('PPGroups 'NetworkGroup 'NoStakePoolGroup) f OrdExUnits)
  -- ^ Max total script execution resources units allowed per tx
  , cppMaxBlockExUnits :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f OrdExUnits)
  -- ^ Max total script execution resources units allowed per block
  , cppMaxValSize :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Max size of a Value in an output
  , cppCollateralPercentage :: !(THKD ('PPGroups 'TechnicalGroup 'NoStakePoolGroup) f Word16)
  -- ^ Percentage of the txfee which must be provided as collateral when
  -- including non-native scripts.
  , cppMaxCollateralInputs :: !(THKD ('PPGroups 'NetworkGroup 'NoStakePoolGroup) f Word16)
  -- ^ Maximum number of collateral inputs allowed in a transaction
  , -- New ones for Conway:
    cppPoolVotingThresholds :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f PoolVotingThresholds)
  -- ^ Thresholds for SPO votes
  , cppDRepVotingThresholds :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f DRepVotingThresholds)
  -- ^ Thresholds for DRep votes
  , cppCommitteeMinSize :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f Word16)
  -- ^ Minimum size of the Constitutional Committee
  , cppCommitteeMaxTermLength :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f EpochInterval)
  -- ^ The Constitutional Committee Term limit in number of Slots
  , cppGovActionLifetime :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f EpochInterval)
  -- ^ Gov action lifetime in number of Epochs
  , cppGovActionDeposit :: !(THKD ('PPGroups 'GovGroup 'SecurityGroup) f (CompactForm Coin))
  -- ^ The amount of the Gov Action deposit
  , cppDRepDeposit :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f (CompactForm Coin))
  -- ^ The amount of a DRep registration deposit
  , cppDRepActivity :: !(THKD ('PPGroups 'GovGroup 'NoStakePoolGroup) f EpochInterval)
  -- ^ The number of Epochs that a DRep can perform no activity without losing their @Active@ status.
  , cppMinFeeRefScriptCostPerByte ::
      !(THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f NonNegativeInterval)
  -- ^ Reference scripts fee for the minimum fee calculation
  }
  deriving (Generic)

cppMinFeeA ::
  forall era f.
  HKDFunctor f => ConwayPParams f era -> THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f Coin
cppMinFeeA p = THKD $ unTHKD (cppTxFeePerByte p) ^. hkdCoinPerByteL @f . hkdPartialCompactCoinL @f
{-# DEPRECATED cppMinFeeA "In favor of `cppTxFeePerByte`" #-}

cppMinFeeB ::
  forall era f.
  HKDFunctor f =>
  ConwayPParams f era -> THKD ('PPGroups 'EconomicGroup 'SecurityGroup) f Coin
cppMinFeeB p = THKD $ unTHKD (cppTxFeeFixed p) ^. hkdPartialCompactCoinL @f
{-# DEPRECATED cppMinFeeB "In favor of `cppTxFeeFixed`" #-}

deriving instance Eq (ConwayPParams Identity era)

deriving instance Ord (ConwayPParams Identity era)

deriving instance Show (ConwayPParams Identity era)

```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L933-953)
```haskell
instance ConwayEraPParams ConwayEra where
  ppuWellFormed pv ppu =
    and
      [ -- Numbers
        isValid (/= 0) ppuMaxBBSizeL
      , isValid (/= 0) ppuMaxTxSizeL
      , isValid (/= 0) ppuMaxBHSizeL
      , isValid (/= 0) ppuMaxValSizeL
      , isValid (/= 0) ppuCollateralPercentageL
      , isValid (/= EpochInterval 0) ppuCommitteeMaxTermLengthL
      , isValid (/= EpochInterval 0) ppuGovActionLifetimeL
      , -- Coins
        isValid (/= CompactCoin 0) ppuPoolDepositCompactL
      , isValid (/= CompactCoin 0) ppuGovActionDepositCompactL
      , isValid (/= CompactCoin 0) ppuDRepDepositCompactL
      , hardforkConwayBootstrapPhase pv
          || isValid ((/= CompactCoin 0) . unCoinPerByte) ppuCoinsPerUTxOByteL
      , ppu /= emptyPParamsUpdate
      , pvMajor pv < natVersion @11
          || isValid (/= 0) ppuNOptL
      ]
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L980-983)
```haskell
  ppMaxRefScriptSizePerTxG = L.to . const $ 200 * 1024
  ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
  ppRefScriptCostMultiplierG = L.to . const . fromJust $ boundRational 1.2
  ppRefScriptCostStrideG = L.to . const $ knownNonZeroBounded @25_600
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs (L346-355)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs (L107-112)
```haskell
    refScriptsFee =
      tierRefScriptFee
        (unboundRational $ pp ^. ppRefScriptCostMultiplierG)
        (fromIntegral @Word32 @Int . unNonZero $ pp ^. ppRefScriptCostStrideG)
        refScriptCostPerByte
        refScriptsSize
```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L23-23)
```markdown
Linear pricing was either too expensive when the multiplier was set too high or was an inadequate deterrent when the multiplier was set too low. Therefore, we needed to implement a pricing mechanism that would be very expensive for usage with large quantities of large plutus scripts, while keeping the pricing reasonably low for the most common use case of a total size of reference scripts of at most 25KiB per transaction. One of the constraints we had to operate under was inability to add any new protocol parameters, since that was a bit too late in the release cycle of the Conway era. In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era.
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L584-587)
```haskell
  ppMaxRefScriptSizePerTxG = ppLensHKD . hkdMaxRefScriptSizePerTxL
  ppMaxRefScriptSizePerBlockG = ppLensHKD . hkdMaxRefScriptSizePerBlockL
  ppRefScriptCostMultiplierG = ppLensHKD . hkdRefScriptCostMultiplierL
  ppRefScriptCostStrideG = ppLensHKD . hkdRefScriptCostStrideL
```
