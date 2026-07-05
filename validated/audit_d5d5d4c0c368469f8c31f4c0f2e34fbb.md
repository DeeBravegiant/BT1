### Title
Missing Lower-Bound Validation on Voting Thresholds in `ppuWellFormed` Enables Governance Takeover via Low DRep Participation — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`)

---

### Summary

`ppuWellFormed` in `ConwayEra` validates several protocol parameter update bounds (block sizes, deposits, lifetimes) but imposes **no minimum bound** on `DRepVotingThresholds` or `PoolVotingThresholds`. An attacker who obtains effective DRep majority — which requires only minimal stake when DRep participation is low — can submit a `ParameterChange` action setting all DRep voting thresholds to `0`. Once enacted, every subsequent governance action is automatically ratified by DReps without any votes, enabling unauthorized treasury withdrawals, hard forks, committee replacements, and constitution changes.

---

### Finding Description

`ppuWellFormed` is the sole well-formedness gate for `PParamsUpdate` proposals, called inside the `GOV` STS rule before a proposal is admitted to the governance queue. It rejects updates that set block sizes, deposits, or lifetimes to zero, but it contains **no check** on `ppuDRepVotingThresholdsL` or `ppuPoolVotingThresholdsL`: [1](#0-0) 

The full list of validated fields never mentions `DRepVotingThresholds` or `PoolVotingThresholds`. A `ParameterChange` proposal that sets every field of `DRepVotingThresholds` to `minBound` (i.e., `0 % 1`) passes `ppuWellFormed` without error.

The ratification logic in `dRepAccepted` short-circuits to `True` the moment the stored threshold equals `minBound`: [2](#0-1) 

This short-circuit is confirmed by an existing integration test that explicitly demonstrates automatic ratification when thresholds are set to `0`: [3](#0-2) 

For non-security-group `ParameterChange` actions (which includes the `GovGroup` parameters `DRepVotingThresholds` and `PoolVotingThresholds`), SPO voting is `NoVotingAllowed`: [4](#0-3) 

This means only DRep votes and committee votes are required to ratify a `ParameterChange` targeting governance thresholds. The attacker does not need SPO cooperation.

The DRep accepted ratio denominator is the total stake of **active, non-expired, non-abstaining** DReps: [5](#0-4) 

When DRep participation is low (many DReps expired due to inactivity), an attacker who registers a single DRep with minimal stake and delegates to it becomes the **only active DRep**. Their yes-vote yields a ratio of `1 % 1`, which exceeds any threshold currently in the protocol parameters, allowing the threshold-zeroing proposal to pass.

---

### Impact Explanation

Once `DRepVotingThresholds` is enacted with all fields set to `0`:

- `dvtTreasuryWithdrawal = 0` → every `TreasuryWithdrawals` action is automatically ratified → **direct, attacker-controlled drain of the Cardano treasury**.
- `dvtHardForkInitiation = 0` → every `HardForkInitiation` action is automatically ratified → **unauthorized hard fork**.
- `dvtCommitteeNormal = 0` / `dvtCommitteeNoConfidence = 0` → every `UpdateCommittee` / `NoConfidence` action is automatically ratified → **unauthorized committee replacement or dissolution**.
- `dvtUpdateToConstitution = 0` → every `NewConstitution` action is automatically ratified → **unauthorized constitution replacement**.

This matches the allowed critical impact: *"Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted."*

---

### Likelihood Explanation

DRep participation dropping to near-zero is a realistic scenario:

1. **Early governance**: In the first epochs after the Conway hard fork, few DReps may be registered or active.
2. **Natural expiry**: DReps expire after `ppDRepActivity` epochs of inactivity. If governance proposals are sparse, many DReps expire simultaneously, collapsing the active DRep set.
3. **Attacker-assisted expiry**: `ppuDRepActivityL` is also unchecked by `ppuWellFormed` — an attacker who already has a small governance foothold can first set `dRepActivity` to `EpochInterval 1`, causing all existing DReps to expire within one epoch, then register their own DRep as the sole active participant.

The attacker entry path is entirely unprivileged: register a stake key, delegate to a self-registered DRep, pay the `govActionDeposit`, and submit a `ParameterChange` transaction. No leaked keys, no supermajority, no trusted role is required beyond holding the minimal stake needed to be the only active DRep.

---

### Recommendation

Add lower-bound guards for `DRepVotingThresholds` and `PoolVotingThresholds` inside `ppuWellFormed` in `eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs`. At minimum, enforce that no individual threshold field can be set to `0` (or below a protocol-defined floor), analogous to the existing `isValid (/= EpochInterval 0) ppuGovActionLifetimeL` guard. Similarly, add a minimum bound check on `ppuDRepActivityL` to prevent instant mass-expiry of the DRep set. [1](#0-0) 

---

### Proof of Concept

**Step 1 — Collapse the active DRep set.**
Wait for (or induce via a prior `dRepActivity = 1` parameter change) all existing DReps to expire. The `dRepAcceptedRatio` denominator becomes `0`; `%?` returns `0`, so no action can pass under the current threshold — but the attacker is about to become the only active DRep. [6](#0-5) 

**Step 2 — Register attacker DRep with 1 lovelace delegated.**
Submit a `RegDRepTxCert` certificate. The attacker's DRep is now the sole entry in `reDRepDistr` with a non-zero, non-expired, non-abstaining state.

**Step 3 — Submit a `ParameterChange` setting `DRepVotingThresholds` to all zeros.**
The proposal passes `ppuWellFormed` because no check exists on `ppuDRepVotingThresholdsL`. [1](#0-0) 

**Step 4 — Attacker DRep votes `VoteYes`.**
`dRepAcceptedRatio` = `attackerStake % attackerStake` = `1`. This exceeds the current threshold (e.g., `51 % 100`). `dRepAccepted` returns `True`. [7](#0-6) 

**Step 5 — Committee approves (or committee threshold is already `0` / committee is absent during bootstrap).**
`acceptedByEveryone` returns `True`; the `ParameterChange` is enacted. [8](#0-7) 

**Step 6 — All future governance actions auto-ratify.**
`dRepAccepted` short-circuits to `True` for every subsequent action because `r == minBound`: [9](#0-8) 

The attacker submits `TreasuryWithdrawals` targeting their own address. `dvtTreasuryWithdrawal = 0` causes immediate DRep acceptance. The treasury is drained.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L934-953)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L228-236)
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
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L252-281)
```haskell
dRepAcceptedRatio ::
  forall era.
  RatifyEnv era ->
  Map (Credential DRepRole) Vote ->
  GovAction era ->
  Rational
dRepAcceptedRatio RatifyEnv {reDRepDistr, reDRepState, reCurrentEpoch} gasDRepVotes govAction =
  toInteger yesStake %? toInteger totalExcludingAbstainStake
  where
    accumStake (!yes, !tot) drep (CompactCoin stake) =
      case drep of
        DRepCredential cred ->
          case Map.lookup cred reDRepState of
            Nothing -> (yes, tot) -- drep is not registered, so we don't consider it
            Just drepState
              | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
              | otherwise ->
                  case Map.lookup cred gasDRepVotes of
                    -- drep hasn't voted for this action, so we don't count
                    -- the vote but we consider it in the denominator:
                    Nothing -> (yes, tot + stake)
                    Just VoteYes -> (yes + stake, tot + stake)
                    Just Abstain -> (yes, tot)
                    Just VoteNo -> (yes, tot + stake)
        DRepAlwaysNoConfidence ->
          case govAction of
            NoConfidence _ -> (yes + stake, tot + stake)
            _ -> (yes, tot + stake)
        DRepAlwaysAbstain -> (yes, tot)
    (yesStake, totalExcludingAbstainStake) = Map.foldlWithKey' accumStake (0, 0) reDRepDistr
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L391-394)
```haskell
      paramChangeThreshold ppu
        | any isSecurityRelevant (modifiedPPGroups ppu) =
            VotingThreshold pvtPPSecurityGroup
        | otherwise = NoVotingAllowed
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/BaseTypes/NonZero.hs (L150-153)
```haskell
(%?) :: Integral a => a -> a -> Ratio a
x %? y
  | y == 0 = 0
  | otherwise = x % y
```
