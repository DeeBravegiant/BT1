### Title
Hardcoded Reference Script Fee and Size Parameters in Conway Era Cannot Be Adjusted via Governance — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

### Summary
The Conway era `ConwayEraPParams ConwayEra` instance hardcodes four critical reference-script resource parameters as compile-time constants that are invisible to on-chain governance. Because these values cannot be updated via a `PParamUpdate` governance action, any miscalibration of the fee curve or size caps can only be corrected by a hard fork, leaving the network unable to respond to fee-manipulation or resource-exhaustion attacks within the era.

### Finding Description
In `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs` lines 980–983, the `ConwayEraPParams ConwayEra` instance implements four getters as `L.to . const <literal>`:

```haskell
ppMaxRefScriptSizePerTxG    = L.to . const $ 200 * 1024      -- 204 800 bytes
ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024     -- 1 048 576 bytes
ppRefScriptCostMultiplierG  = L.to . const . fromJust $ boundRational 1.2
ppRefScriptCostStrideG      = L.to . const $ knownNonZeroBounded @25_600
``` [1](#0-0) 

These four values are consumed in three separate enforcement points:

1. **Minimum fee calculation** — `getConwayMinFeeTx` in `eras/conway/impl/src/Cardano/Ledger/Conway/Tx.hs` calls `tierRefScriptFee` with the hardcoded multiplier and stride to compute the lovelace surcharge for reference scripts. [2](#0-1) 

2. **Per-block size cap** — `validateBodyRefScriptsSizeTooBig` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Bbody.hs` rejects a block whose total reference-script bytes exceed the hardcoded 1 MiB limit. [3](#0-2) 

3. **Per-transaction size cap** — `validateRefScriptSize` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs` enforces the hardcoded 200 KiB per-transaction limit. [4](#0-3) 

By contrast, `ppMinFeeRefScriptCostPerByte` (`cppMinFeeRefScriptCostPerByte`) **is** a proper protocol parameter with a governance update path (tag 33). [5](#0-4) 

The ADR that introduced these values explicitly acknowledges the problem:
> *"One of the constraints we had to operate under was inability to add any new protocol parameters … In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era."* [6](#0-5) 

The Dijkstra era corrects this by promoting all four values to proper `DijkstraPParams` fields (`dppMaxRefScriptSizePerBlock`, `dppMaxRefScriptSizePerTx`, `dppRefScriptCostStride`, `dppRefScriptCostMultiplier`) with full governance update paths. [7](#0-6) 

### Impact Explanation
An unprivileged transaction author can craft a transaction that references up to 204 800 bytes of reference scripts (the hardcoded per-tx cap). The minimum fee charged for those scripts is computed from the hardcoded exponential curve (`multiplier = 1.2`, `stride = 25 600`). If the actual deserialization and execution cost imposed on every validating node exceeds what this curve charges — as the June 2024 DDoS attack demonstrated was possible with the earlier linear pricing — the attacker pays less than the cost they impose. Because governance cannot raise the multiplier or lower the size caps within Conway era, the network has no on-chain remedy: fees for reference-script-heavy transactions are permanently outside the design parameters that governance is supposed to control, until a hard fork is executed.

This matches the **Medium** allowed impact: *"Attacker-controlled transactions … exceed intended validation limits or modify fees … outside design parameters."*

### Likelihood Explanation
**Medium.** The June 25 2024 DDoS attack on Cardano mainnet directly exploited the reference-script deserialization cost gap, demonstrating the attack is practical and was already executed. The hardcoded multiplier of 1.2 was chosen as a community compromise rather than a value proven sufficient to cover worst-case deserialization cost. Because `ppMinFeeRefScriptCostPerByte` is the only adjustable knob, governance can raise the base price but cannot reshape the exponential curve or tighten the size caps if the current calibration proves inadequate again.

### Recommendation
The four hardcoded constants should be replaced with proper protocol parameters in Conway era, following the pattern already implemented in Dijkstra. Concretely, `ppMaxRefScriptSizePerTxG`, `ppMaxRefScriptSizePerBlockG`, `ppRefScriptCostMultiplierG`, and `ppRefScriptCostStrideG` should be backed by `ConwayPParams` fields with `PParamUpdate` tags, so that governance actions can adjust them without requiring a hard fork. Until that is possible, the Conway genesis and any subsequent governance actions should set `minFeeRefScriptCostPerByte` conservatively high enough to compensate for the inability to adjust the curve shape.

### Proof of Concept
1. Deploy a UTxO whose output carries a Plutus reference script of exactly 204 800 bytes (the hardcoded `ppMaxRefScriptSizePerTxG`).
2. Submit a Conway-era transaction that includes this UTxO in its reference inputs. The ledger computes the minimum fee via `getConwayMinFeeTx` → `tierRefScriptFee 1.2 25600 baseCost 204800`, yielding a fixed lovelace amount determined entirely by the hardcoded curve.
3. Every validating node must deserialize and hash 204 800 bytes of script data. If the actual CPU/memory cost of this operation exceeds the lovelace charged (as was the case before the June 2024 patch), the attacker imposes net-negative-fee validation work on the network.
4. A governance action to raise `minFeeRefScriptCostPerByte` can increase the base price but cannot change the multiplier or stride, so the curve shape remains fixed. A governance action to lower the size cap is impossible because `ppMaxRefScriptSizePerTxG` has no `PParamUpdate` path in Conway era.
5. The only remedy is a hard fork to Dijkstra, where `dppMaxRefScriptSizePerTx` and `dppRefScriptCostMultiplier` become proper protocol parameters. [8](#0-7) [1](#0-0)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L980-983)
```haskell
  ppMaxRefScriptSizePerTxG = L.to . const $ 200 * 1024
  ppMaxRefScriptSizePerBlockG = L.to . const $ 1024 * 1024
  ppRefScriptCostMultiplierG = L.to . const . fromJust $ boundRational 1.2
  ppRefScriptCostStrideG = L.to . const $ knownNonZeroBounded @25_600
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L1337-1344)
```haskell
ppMinFeeRefScriptCostPerByte :: ConwayEraPParams era => PParam era
ppMinFeeRefScriptCostPerByte =
  PParam
    { ppName = "minFeeRefScriptCostPerByte"
    , ppLens = ppMinFeeRefScriptCostPerByteL
    , ppEraDecoder = Nothing
    , ppUpdate = Just $ PParamUpdate 33 ppuMinFeeRefScriptCostPerByteL
    }
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L1-30)
```haskell
{-# LANGUAGE DataKinds #-}
{-# LANGUAGE DeriveGeneric #-}
{-# LANGUAGE FlexibleContexts #-}
{-# LANGUAGE FlexibleInstances #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE MultiParamTypeClasses #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE RankNTypes #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE StandaloneDeriving #-}
{-# LANGUAGE TypeApplications #-}
{-# LANGUAGE TypeFamilies #-}
{-# LANGUAGE TypeOperators #-}
{-# LANGUAGE UndecidableInstances #-}
{-# OPTIONS_GHC -Wno-orphans #-}

module Cardano.Ledger.Conway.Rules.Ledger (
  LEDGER,
  ConwayLedgerPredFailure (..),
  ConwayLedgerEvent (..),
  shelleyToConwayLedgerPredFailure,
  conwayLedgerTransition,
  conwayLedgerTransitionTRC,
  validateTreasuryValue,
  validateRefScriptSize,
  validateWithdrawalsDelegated,
  updateDormantDRepExpiries,
  updateVotingDRepExpiries,
) where

```

**File:** docs/adr/2024-08-14_009-refscripts-fee-change.md (L23-23)
```markdown
Linear pricing was either too expensive when the multiplier was set too high or was an inadequate deterrent when the multiplier was set too low. Therefore, we needed to implement a pricing mechanism that would be very expensive for usage with large quantities of large plutus scripts, while keeping the pricing reasonably low for the most common use case of a total size of reference scripts of at most 25KiB per transaction. One of the constraints we had to operate under was inability to add any new protocol parameters, since that was a bit too late in the release cycle of the Conway era. In other words we had to hard code some values, which will be turned into proper protocol parameters in the next era.
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L151-158)
```haskell
  , dppMaxRefScriptSizePerBlock :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of all reference scripts combined from
  -- all transactions within a block.
  , dppMaxRefScriptSizePerTx :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f Word32)
  -- ^ Limit on the total number of bytes of reference scripts that a transaction can use.
  , dppRefScriptCostStride :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f (NonZero Word32))
  , dppRefScriptCostMultiplier :: !(THKD ('PPGroups 'NetworkGroup 'SecurityGroup) f PositiveInterval)
  }
```
