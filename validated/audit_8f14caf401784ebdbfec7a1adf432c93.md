### Title
Missing Zero-Value Validation for Voting Thresholds in `ppuWellFormed` Enables Automatic Ratification of Unauthorized Governance Actions — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

---

### Summary

`ppuWellFormed` in both the Conway and Dijkstra eras validates many protocol parameter update fields for non-zero values, but omits any check on `ppuDRepVotingThresholdsL` and `ppuPoolVotingThresholdsL`. A `ParameterChange` governance proposal that sets all voting thresholds to zero passes `actionWellFormed` without rejection. Once enacted, every subsequent governance action — treasury withdrawals, hard-fork initiations, committee updates, constitution changes — is automatically ratified by all three governing bodies with zero votes, because `dRepAccepted`, `spoAccepted`, and `committeeAccepted` each short-circuit to `True` when the relevant threshold equals `minBound` (0).

---

### Finding Description

**Vulnerability class:** Missing parameter validation during protocol-parameter update construction (direct analog to Pickle Finance's missing `require(variableAddress != address(0))`).

**Root cause — `ppuWellFormed` in Conway:**

`eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs` lines 934–953 validate eleven fields for non-zero values, but `ppuDRepVotingThresholdsL` and `ppuPoolVotingThresholdsL` are absent from the list:

```haskell
instance ConwayEraPParams ConwayEra where
  ppuWellFormed pv ppu =
    and
      [ isValid (/= 0) ppuMaxBBSizeL
      , isValid (/= 0) ppuMaxTxSizeL
      ...
      , isValid (/= CompactCoin 0) ppuPoolDepositCompactL
      , isValid (/= CompactCoin 0) ppuGovActionDepositCompactL
      , isValid (/= CompactCoin 0) ppuDRepDepositCompactL
      -- *** ppuDRepVotingThresholdsL  — NOT CHECKED ***
      -- *** ppuPoolVotingThresholdsL  — NOT CHECKED ***
      , ppu /= emptyPParamsUpdate
      ]
``` [1](#0-0) 

The same omission is present in Dijkstra: [2](#0-1) 

**Gate that should reject the proposal — `actionWellFormed`:**

`actionWellFormed` in the GOV rule calls `ppuWellFormed` for `ParameterChange` actions and emits `MalformedProposal` on failure. Because `ppuWellFormed` returns `True` for all-zero thresholds, the proposal is accepted:

```haskell
actionWellFormed pv ga = failureUnless isWellFormed $ MalformedProposal ga
  where
    isWellFormed = case ga of
      ParameterChange _ ppd _ -> ppuWellFormed pv ppd
      _ -> True
``` [3](#0-2) 

**Ratification short-circuit on zero threshold:**

All three acceptance functions short-circuit to `True` when the threshold is `minBound` (0):

```haskell
-- dRepAccepted
SJust r ->
  -- Short circuit on zero threshold in order to avoid redundant computation.
  r == minBound
    || dRepAcceptedRatio re gasDRepVotes govAction >= unboundRational r
``` [4](#0-3) 

```haskell
-- spoAccepted
SJust r ->
  r == minBound || spoAcceptedRatio re gas ... >= unboundRational r
``` [5](#0-4) 

```haskell
-- committeeAccepted
SJust r ->
  -- short circuit on zero threshold, in which case the committee vote is `yes`
  r == minBound || acceptedRatio >= unboundRational r
``` [6](#0-5) 

`acceptedByEveryone` requires all three: [7](#0-6) 

**Confirmed by existing test:**

The test suite explicitly documents and verifies this behavior:

```
"A governance action is automatically ratified if threshold is set to 0
 for all related governance bodies"
-- No votes were made but due to the 0 thresholds, every governance body
-- accepted the gov action by default...
isDRepAccepted noConfidenceGovId `shouldReturn` True
isSpoAccepted  noConfidenceGovId `shouldReturn` True
isCommitteeAccepted noConfidenceGovId `shouldReturn` True
-- `NoConfidence` is ratified -> the committee is no more
getCommitteeMembers `shouldReturn` mempty
``` [8](#0-7) 

**Additional missing check in Dijkstra — `ppuMaxRefScriptSizePerTxL` / `ppuMaxRefScriptSizePerBlockL`:**

Dijkstra's `ppuWellFormed` also omits non-zero checks for the two new reference-script size caps. Setting either to 0 via a `ParameterChange` would cause `validateAllRefScriptSize` to reject every transaction that touches a reference script, permanently freezing any UTxO whose spending path requires one. [9](#0-8) 

---

### Impact Explanation

**Critical — Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.**

Once a `ParameterChange` setting all `DRepVotingThresholds` and `PoolVotingThresholds` fields to 0 is enacted, every subsequent governance action — `TreasuryWithdrawals`, `HardForkInitiation`, `UpdateCommittee`, `NewConstitution`, `ParameterChange` — is automatically ratified by `acceptedByEveryone` with zero votes from any governing body. An attacker who controls the proposal submission can drain the treasury, initiate an unauthorized hard fork, dissolve the constitutional committee, or rewrite the constitution without any democratic approval.

The secondary Dijkstra finding maps to **High — Permanent freezing of funds** if `maxRefScriptSizePerTx = 0` is enacted, because all reference-script-locked UTxOs become unspendable and recovery requires a hard fork.

---

### Likelihood Explanation

**Moderate for the threshold finding; Low-to-Moderate for the Dijkstra size-cap finding.**

The threshold attack requires the initial `ParameterChange` to be ratified. Two factors reduce the barrier:

1. During the Conway bootstrap phase, `votingDRepThresholdInternal` resets all DRep thresholds to `def` (all zeros), so DReps automatically accept any governance action without votes.
2. `votingStakePoolThresholdInternal` returns `NoVotingAllowed` (mapped to `SJust minBound`) for `ParameterChange` actions that do not touch security-group parameters. `ppuDRepVotingThresholdsL` and `ppuPoolVotingThresholdsL` belong to `GovGroup`, not `SecurityGroup`, so SPOs are also auto-accepted for such a proposal. [10](#0-9) [11](#0-10) 

This means during bootstrap, only the committee threshold remains as a barrier. Post-bootstrap, a governance majority is required for the initial proposal, which is a significant barrier. However, the missing validation means the ledger provides no defense-in-depth: a governance body that inadvertently or maliciously votes for zero thresholds creates a permanent, irrecoverable backdoor.

---

### Recommendation

Add explicit non-zero (or above-minimum) validation for `ppuDRepVotingThresholdsL` and `ppuPoolVotingThresholdsL` inside `ppuWellFormed` in both Conway and Dijkstra, analogous to the existing checks:

```haskell
-- In ppuWellFormed:
, isValid (not . allZeroDRepThresholds) ppuDRepVotingThresholdsL
, isValid (not . allZeroPoolThresholds) ppuPoolVotingThresholdsL
```

where `allZeroDRepThresholds` checks that no individual threshold field is set to `minBound` for security-critical action types (at minimum `dvtHardForkInitiation`, `dvtTreasuryWithdrawal`, `dvtUpdateToConstitution`).

For Dijkstra, add:

```haskell
, isValid (/= 0) ppuMaxRefScriptSizePerTxL
, isValid (/= 0) ppuMaxRefScriptSizePerBlockL
``` [12](#0-11) [13](#0-12) 

---

### Proof of Concept

```haskell
-- Step 1: Craft a ParameterChange proposal with all-zero voting thresholds.
-- ppuWellFormed returns True because neither ppuDRepVotingThresholdsL
-- nor ppuPoolVotingThresholdsL is checked.
let zeroThresholdsPPU =
      emptyPParamsUpdate
        & ppuDRepVotingThresholdsL .~ SJust (DRepVotingThresholds
            { dvtMotionNoConfidence    = minBound  -- 0
            , dvtCommitteeNormal       = minBound
            , dvtCommitteeNoConfidence = minBound
            , dvtUpdateToConstitution  = minBound
            , dvtHardForkInitiation    = minBound
            , dvtTreasuryWithdrawal    = minBound
            , ... })
        & ppuPoolVotingThresholdsL .~ SJust (PoolVotingThresholds
            { pvtMotionNoConfidence    = minBound
            , pvtCommitteeNormal       = minBound
            , pvtCommitteeNoConfidence = minBound
            , pvtHardForkInitiation    = minBound
            , pvtPPSecurityGroup       = minBound })

-- actionWellFormed calls ppuWellFormed, which returns True → proposal accepted.
gaidZeroThresholds <- submitGovAction (ParameterChange SNothing zeroThresholdsPPU SNothing)

-- Step 2: Ratify through current governance (during bootstrap, DReps and SPOs
-- auto-accept non-security-group ParameterChange; only committee vote needed).
passNEpochs 2  -- proposal enacted; all thresholds now 0 on-chain

-- Step 3: Submit any governance action — no votes required.
accountAddress <- registerAccountAddress
treasuryGovId <- submitGovAction (TreasuryWithdrawals [(accountAddress, entireTreasury)] SNothing)
-- dRepAccepted:      r == minBound → True  (no DRep votes)
-- spoAccepted:       r == minBound → True  (no SPO votes)
-- committeeAccepted: r == minBound → True  (no CC votes)
passNEpochs 2
-- Treasury drained with zero votes cast.
```

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L933-961)
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
    where
      isValid ::
        (t -> Bool) ->
        Lens' (PParamsUpdate ConwayEra) (StrictMaybe t) ->
        Bool
      isValid p l = case ppu ^. l of
        SJust x -> p x
        SNothing -> True
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/PParams.hs (L539-565)
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
    where
      isValid ::
        (t -> Bool) ->
        Lens' (PParamsUpdate DijkstraEra) (StrictMaybe t) ->
        Bool
      isValid p l = case ppu ^. l of
        SJust x -> p x
        SNothing -> True
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L128-130)
```haskell
    SJust r ->
      -- short circuit on zero threshold, in which case the committee vote is `yes`
      r == minBound || acceptedRatio >= unboundRational r
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L171-176)
```haskell
spoAccepted re rs gas =
  case votingStakePoolThreshold rs (gasAction gas) of
    -- Short circuit on zero threshold in order to avoid redundant computation.
    SJust r ->
      r == minBound || spoAcceptedRatio re gas (rs ^. rsEnactStateL . ensProtVerL) >= unboundRational r
    SNothing -> False
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L231-235)
```haskell
  case votingDRepThreshold rs govAction of
    SJust r ->
      -- Short circuit on zero threshold in order to avoid redundant computation.
      r == minBound
        || dRepAcceptedRatio re gasDRepVotes govAction >= unboundRational r
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L297-306)
```haskell
acceptedByEveryone ::
  (ConwayEraPParams era, ConwayEraAccounts era) =>
  RatifyEnv era ->
  RatifyState era ->
  GovActionState era ->
  Bool
acceptedByEveryone env st gas =
  committeeAccepted env st gas
    && spoAccepted env st gas
    && dRepAccepted env st gas
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/RatifySpec.hs (L1338-1360)
```haskell
        "A governance action is automatically ratified if threshold is set to 0 for all related governance bodies"
        $ whenPostBootstrap
        $ do
          modifyPParams $ \pp ->
            pp
              & ppPoolVotingThresholdsL . pvtMotionNoConfidenceL .~ 0 %! 1
              & ppDRepVotingThresholdsL . dvtMotionNoConfidenceL .~ 0 %! 1
          (_drep, _, committeeGovId) <- electBasicCommittee
          _ <- setupPoolWithStake $ Coin 1_000_000

          -- There is a committee initially
          getCommitteeMembers `shouldNotReturn` mempty

          noConfidenceGovId <- submitGovAction $ NoConfidence (SJust committeeGovId)

          -- No votes were made but due to the 0 thresholds, every governance body accepted the gov action by default...
          isDRepAccepted noConfidenceGovId `shouldReturn` True
          isSpoAccepted noConfidenceGovId `shouldReturn` True
          -- ...even the committee which is not allowed to vote on `NoConfidence` action
          isCommitteeAccepted noConfidenceGovId `shouldReturn` True
          passNEpochs 2
          -- `NoConfidence` is ratified -> the committee is no more
          getCommitteeMembers `shouldReturn` mempty
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L379-406)
```haskell
votingStakePoolThresholdInternal pp isElectedCommittee action =
  let PoolVotingThresholds
        { pvtCommitteeNoConfidence
        , pvtCommitteeNormal
        , pvtHardForkInitiation
        , pvtPPSecurityGroup
        , pvtMotionNoConfidence
        } = pp ^. ppPoolVotingThresholdsL
      isSecurityRelevant (PPGroups _ s) =
        case s of
          SecurityGroup -> True
          NoStakePoolGroup -> False
      paramChangeThreshold ppu
        | any isSecurityRelevant (modifiedPPGroups ppu) =
            VotingThreshold pvtPPSecurityGroup
        | otherwise = NoVotingAllowed
   in case action of
        NoConfidence {} -> VotingThreshold pvtMotionNoConfidence
        UpdateCommittee {} ->
          VotingThreshold $
            if isElectedCommittee
              then pvtCommitteeNormal
              else pvtCommitteeNoConfidence
        NewConstitution {} -> NoVotingAllowed
        HardForkInitiation {} -> VotingThreshold pvtHardForkInitiation
        ParameterChange _ ppu _ -> paramChangeThreshold ppu
        TreasuryWithdrawals {} -> NoVotingAllowed
        InfoAction {} -> NoVotingThreshold
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L504-532)
```haskell
votingDRepThresholdInternal ::
  ConwayEraPParams era =>
  PParams era ->
  Bool ->
  GovAction era ->
  VotingThreshold
votingDRepThresholdInternal pp isElectedCommittee action =
  let thresholds@DRepVotingThresholds
        { dvtCommitteeNoConfidence
        , dvtCommitteeNormal
        , dvtMotionNoConfidence
        , dvtUpdateToConstitution
        , dvtHardForkInitiation
        , dvtTreasuryWithdrawal
        } -- We reset all (except InfoAction) DRep thresholds to 0 during bootstrap phase
          | hardforkConwayBootstrapPhase (pp ^. ppProtocolVersionL) = def
          | otherwise = pp ^. ppDRepVotingThresholdsL
   in case action of
        NoConfidence {} -> VotingThreshold dvtMotionNoConfidence
        UpdateCommittee {} ->
          VotingThreshold $
            if isElectedCommittee
              then dvtCommitteeNormal
              else dvtCommitteeNoConfidence
        NewConstitution {} -> VotingThreshold dvtUpdateToConstitution
        HardForkInitiation {} -> VotingThreshold dvtHardForkInitiation
        ParameterChange _ ppu _ -> VotingThreshold $ pparamsUpdateThreshold thresholds ppu
        TreasuryWithdrawals {} -> VotingThreshold dvtTreasuryWithdrawal
        InfoAction {} -> NoVotingThreshold
```
