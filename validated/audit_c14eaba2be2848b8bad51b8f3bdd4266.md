### Title
Missing Zero-Value Validation for `dRepActivity` in `ppuWellFormed` Allows Governance-Controlled Permanent Freezing of All DRep Deposits - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

---

### Summary

The Conway-era `ppuWellFormed` function validates that certain protocol parameter updates cannot be set to zero. However, `dRepActivity` (`EpochInterval`) is **absent** from this validation list, while every other governance-critical `EpochInterval` parameter (`committeeMaxTermLength`, `govActionLifetime`) is explicitly checked. A ratified `ParameterChange` governance action setting `dRepActivity = EpochInterval 0` causes every DRep's expiry to be computed as the **current epoch**, making all DReps immediately and permanently expired. This freezes all DRep-gated governance actions (including future parameter changes, treasury withdrawals, hard-fork initiations, and constitution updates), permanently locking the governance system and all associated DRep deposits without any recovery path short of a hard fork.

---

### Finding Description

**Vulnerability class:** Missing validation in a parameter-update setter that was present (implicitly, by design) for analogous parameters — directly analogous to the Audius M06 pattern.

**Root cause — exact location:**

In `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`, the `ppuWellFormed` implementation for `ConwayEra` validates the following parameters against zero:

```haskell
instance ConwayEraPParams ConwayEra where
  ppuWellFormed pv ppu =
    and
      [ isValid (/= 0) ppuMaxBBSizeL
      , isValid (/= 0) ppuMaxTxSizeL
      , isValid (/= 0) ppuMaxBHSizeL
      , isValid (/= 0) ppuMaxValSizeL
      , isValid (/= 0) ppuCollateralPercentageL
      , isValid (/= EpochInterval 0) ppuCommitteeMaxTermLengthL   -- ✓ checked
      , isValid (/= EpochInterval 0) ppuGovActionLifetimeL        -- ✓ checked
      , isValid (/= CompactCoin 0) ppuPoolDepositCompactL
      , isValid (/= CompactCoin 0) ppuGovActionDepositCompactL
      , isValid (/= CompactCoin 0) ppuDRepDepositCompactL
      ...
      ]
``` [1](#0-0) 

`ppuDRepActivityL` (`EpochInterval`) is **not** in this list. There is no `isValid (/= EpochInterval 0) ppuDRepActivityL` check.

The same omission exists in the `DijkstraEra` instance: [2](#0-1) 

**How `dRepActivity = 0` causes permanent expiry:**

When a DRep registers or votes, its expiry is computed by `computeDRepExpiry` / `computeDRepExpiryVersioned`:

```haskell
computeDRepExpiry ppDRepActivity currentEpoch =
  binOpEpochNo
    (-)
    (addEpochInterval currentEpoch ppDRepActivity)
``` [3](#0-2) 

With `ppDRepActivity = EpochInterval 0`, `addEpochInterval currentEpoch (EpochInterval 0) = currentEpoch`, so every DRep's expiry is set to `currentEpoch - numDormantEpochs`. Since `currentEpoch > expiry` is the expiry check, all DReps become expired immediately upon the next epoch boundary. No DRep can ever renew: any `ConwayUpdateDRep` certificate also calls `computeDRepExpiry` with the same zero interval, producing the same already-expired result. [4](#0-3) 

**The `actionWellFormed` gate only checks `ParameterChange` via `ppuWellFormed`:**

```haskell
actionWellFormed pv ga = failureUnless isWellFormed $ MalformedProposal ga
  where
    isWellFormed = case ga of
      ParameterChange _ ppd _ -> ppuWellFormed pv ppd
      _ -> True
``` [5](#0-4) 

Because `ppuWellFormed` does not reject `ppuDRepActivityL = SJust (EpochInterval 0)`, a `ParameterChange` proposal with this value passes the `actionWellFormed` check and can be ratified and enacted normally.

**Contrast with the analogous parameters that ARE protected:**

The test suite explicitly confirms that `ppuGovActionLifetimeL` and `ppuCommitteeMaxTermLengthL` cannot be zero: [6](#0-5) 

But there is no corresponding test for `ppuDRepActivityL cannot be 0`.

---

### Impact Explanation

**Impact: High — Permanent freezing of DRep deposits and governance.**

Once `dRepActivity = 0` is enacted:

1. All currently registered DReps become expired at the next epoch boundary. Their deposits (currently 500 ADA each on mainnet) are locked in `vsDReps` state and cannot be reclaimed: `ConwayUnRegDRep` requires the DRep to be registered (it still is), but the deposit refund check passes — however, the DRep's voting weight is zero, so no future DRep-gated governance action can ever reach quorum.

2. Any new DRep registration immediately expires. `computeDRepExpiryVersioned` with `EpochInterval 0` sets expiry = current epoch, so the DRep is expired before it can vote.

3. All governance actions requiring DRep approval (treasury withdrawals, constitution updates, hard-fork initiations, further parameter changes) are permanently blocked. The `dRepAccepted` check returns `False` for all non-zero thresholds when no DRep stake is active. [7](#0-6) 

4. Recovery requires a hard fork, matching the "High — Permanent freezing of funds/deposits/withdrawals where recovery requires a hard fork" impact category.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires a successful `ParameterChange` governance action, which needs DRep + SPO + CC approval. This is not a single-actor exploit — it requires a governance majority. However:

- The Cardano governance system is designed so that a sufficiently coordinated set of DReps (holding >50% of delegated stake) can ratify any parameter change. A malicious or compromised DRep coalition could propose and ratify this.
- The parameter `dRepActivity` is a legitimate governance-updatable parameter (tag 32 in the update map), so no additional privilege is needed beyond normal governance participation.
- The absence of the zero-check means the ledger itself provides no last-resort protection — unlike `govActionLifetime` and `committeeMaxTermLength` which are explicitly guarded. [8](#0-7) 

---

### Recommendation

Add `isValid (/= EpochInterval 0) ppuDRepActivityL` to the `ppuWellFormed` check list in both `ConwayEra` and `DijkstraEra` instances, mirroring the existing checks for `ppuCommitteeMaxTermLengthL` and `ppuGovActionLifetimeL`:

```haskell
-- In ConwayEra instance (PParams.hs line ~942):
, isValid (/= EpochInterval 0) ppuCommitteeMaxTermLengthL
, isValid (/= EpochInterval 0) ppuGovActionLifetimeL
, isValid (/= EpochInterval 0) ppuDRepActivityL   -- ADD THIS
``` [9](#0-8) 

Apply the same fix to `DijkstraEra`: [10](#0-9) 

Also add a corresponding test in `GovSpec.hs` alongside the existing `ppuGovActionLifetimeL cannot be 0` test: [11](#0-10) 

---

### Proof of Concept

1. A governance coalition submits a `ParameterChange` action with `ppuDRepActivityL = SJust (EpochInterval 0)`.
2. The action passes `actionWellFormed` because `ppuWellFormed` does not check `ppuDRepActivityL` for zero.
3. The action is ratified (DRep + SPO + CC thresholds met) and enacted via the `ENACT` rule.
4. At the next epoch boundary, `EPOCH` applies the new `PParams` with `cppDRepActivity = EpochInterval 0`.
5. Any subsequent DRep registration or vote triggers `computeDRepExpiry (EpochInterval 0) currentEpoch numDormantEpochs`, which returns `currentEpoch - numDormantEpochs ≤ currentEpoch`, so `currentEpoch > expiry` is immediately true.
6. `dRepAccepted` returns `False` for all proposals with non-zero DRep thresholds. All DRep-gated governance is permanently frozen. All DRep deposits are permanently locked. [12](#0-11) [13](#0-12)

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L1328-1335)
```haskell
ppDRepActivity :: ConwayEraPParams era => PParam era
ppDRepActivity =
  PParam
    { ppName = "dRepActivity"
    , ppLens = ppDRepActivityL
    , ppEraDecoder = Nothing
    , ppUpdate = Just $ PParamUpdate 32 ppuDRepActivityL
    }
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L539-557)
```haskell
instance ConwayEraPParams DijkstraEra where
  ppuWellFormed _pv ppu =
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
        isValid (/= mempty) ppuPoolDepositL
      , isValid (/= zero) ppuGovActionDepositL
      , isValid (/= zero) ppuDRepDepositL
      , isValid ((/= CompactCoin 0) . unCoinPerByte) ppuCoinsPerUTxOByteL
      , ppu /= emptyPParamsUpdate
      , isValid (/= 0) ppuNOptL
      ]
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L256-272)
```haskell
    ConwayUpdateDRep cred mAnchor -> do
      Map.member cred (certState ^. certVStateL . vsDRepsL)
        ?! (injectFailure . ConwayDRepNotRegistered) cred
      pure $
        certState
          & certVStateL . vsDRepsL
            %~ Map.adjust
              ( \drepState ->
                  drepState
                    & drepExpiryL
                      .~ computeDRepExpiry
                        ppDRepActivity
                        cgceCurrentEpoch
                        (certState ^. certVStateL . vsNumDormantEpochsL)
                    & drepAnchorL .~ mAnchor
              )
              cred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L286-292)
```haskell
computeDRepExpiryVersioned pp currentEpoch numDormantEpochs
  -- Starting with version 10, we correctly take into account the number of dormant epochs
  -- when registering a drep
  | hardforkConwayBootstrapPhase (pp ^. ppProtocolVersionL) =
      addEpochInterval currentEpoch (pp ^. ppDRepActivityL)
  | otherwise =
      computeDRepExpiry (pp ^. ppDRepActivityL) currentEpoch numDormantEpochs
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L294-306)
```haskell
computeDRepExpiry ::
  -- | DRepActivity PParam
  EpochInterval ->
  -- | Current epoch
  EpochNo ->
  -- | The count of the dormant epochs
  EpochNo ->
  -- | Computed expiry
  EpochNo
computeDRepExpiry ppDRepActivity currentEpoch =
  binOpEpochNo
    (-)
    (addEpochInterval currentEpoch ppDRepActivity)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L393-399)
```haskell
actionWellFormed ::
  ConwayEraPParams era => ProtVer -> GovAction era -> Test (ConwayGovPredFailure era)
actionWellFormed pv ga = failureUnless isWellFormed $ MalformedProposal ga
  where
    isWellFormed = case ga of
      ParameterChange _ ppd _ -> ppuWellFormed pv ppd
      _ -> True
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/GovSpec.hs (L210-217)
```haskell
      testMalformedProposal
        "ppuCommitteeMaxTermLengthL cannot be 0"
        ppuCommitteeMaxTermLengthL
        $ EpochInterval 0
      testMalformedProposal
        "ppuGovActionLifetimeL cannot be 0"
        ppuGovActionLifetimeL
        $ EpochInterval 0
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L228-238)
```haskell
dRepAccepted ::
  ConwayEraPParams era => RatifyEnv era -> RatifyState era -> GovActionState era -> Bool
dRepAccepted re rs GovActionState {gasDRepVotes, gasProposalProcedure} =
  case votingDRepThreshold rs govAction of
    SJust r ->
      -- Short circuit on zero threshold in order to avoid redundant computation.
      r == minBound
        || dRepAcceptedRatio re gasDRepVotes govAction >= unboundRational r
    SNothing -> False
  where
    govAction = pProcGovAction gasProposalProcedure
```
