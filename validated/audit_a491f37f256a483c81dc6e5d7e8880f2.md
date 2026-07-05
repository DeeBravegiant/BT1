### Title
Expired DRep Can Vote and Have Vote Counted at Ratification via Missing Expiry Check in GOV Rule — (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs`)

### Summary

The Conway governance `GOV` rule accepts votes from DReps whose activity period has already expired. No expiry check is performed at vote-submission time. Separately, `updateVotingDRepExpiries` in the `CERTS` rule unconditionally refreshes the expiry of any DRep that appears in a transaction's voting procedures, including already-expired ones. The combination allows an expired DRep to submit a vote, have their expiry silently refreshed, and have that vote counted during ratification at the next epoch boundary — exactly the "stale credential votes and the system counts it" pattern from the external report.

---

### Finding Description

**Vulnerability class:** Stale/expired state validation bypass — governance vote accepted without checking voter's remaining validity period.

**Root cause — missing expiry check in `checkVotersAreValid`:**

`checkVotersAreValid` is the only per-voter predicate called during vote processing in the GOV rule:

```haskell
checkVotersAreValid currentEpoch committeeState votes =
  checkDisallowedVotes votes DisallowedVoters $ \gas ->
    \case
      CommitteeVoter {} -> isCommitteeVotingAllowed currentEpoch committeeState (gasAction gas)
      DRepVoter {}      -> isDRepVotingAllowed (gasAction gas)   -- ← only checks action type
      StakePoolVoter {} -> isStakePoolVotingAllowed (gasAction gas)
```

`isDRepVotingAllowed` only tests whether the **governance action type** permits DRep voting; it never inspects the DRep's `drepExpiry` field or compares it against `currentEpoch`. [1](#0-0) [2](#0-1) 

**Voter existence check does not filter expired DReps:**

The `VotersDoNotExist` predicate only verifies that the DRep credential is present in the registered-DRep map (`vsDRepsL`). Expired DReps remain in that map; they are never removed on expiry. So an expired DRep passes this check. [3](#0-2) 

**`updateVotingDRepExpiries` unconditionally refreshes expiry for any voting DRep:**

In the `CERTS` transition rule, after all certificates are processed, `updateVotingDRepExpiries` iterates over every `DRepVoter` in the transaction's voting procedures and calls `Map.adjust` to overwrite their stored expiry with a freshly computed value — with no guard against the DRep already being expired:

```haskell
updateVSDReps vsDReps =
  Map.foldlWithKey'
    ( \dreps voter _ -> case voter of
        DRepVoter cred ->
          Map.adjust
            (drepExpiryL .~ computeDRepExpiry drepActivity currentEpoch numDormantEpochs)
            cred
            dreps          -- ← no check: is cred currently expired?
        _ -> dreps
    )
    vsDReps
    (unVotingProcedures $ tx ^. bodyTxL . votingProceduresTxBodyL)
``` [4](#0-3) 

**Ratification correctly excludes expired DReps — but only by stored expiry:**

`dRepAcceptedRatio` in the `RATIFY` rule skips DReps whose stored `drepExpiry < reCurrentEpoch`. Because the expiry was refreshed by `updateVotingDRepExpiries` during the epoch in which the expired DRep voted, the DRep is no longer expired at the next epoch boundary when ratification runs, so their vote is counted. [5](#0-4) 

---

### Impact Explanation

An expired DRep can cast a vote on any live governance proposal (parameter change, treasury withdrawal, hard-fork initiation, committee update, no-confidence motion, new constitution). Their vote is stored in `gasDRepVotes` of the `GovActionState`. Because voting also refreshes their expiry, the ratification pulser at the next epoch boundary sees them as active and counts their stake toward the yes/no/abstain tally. If the expired DRep's stake is the marginal amount needed to push a proposal over its threshold, an otherwise-failing governance action is enacted — constituting an **unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action** (Critical impact class).

---

### Likelihood Explanation

Any DRep whose `drepExpiry` epoch has passed can exploit this by simply submitting a transaction containing a `VotingProcedures` entry for a live proposal. No privileged access, key compromise, or majority collusion is required. The attacker controls the transaction entirely. The window is every epoch after the DRep's expiry until the proposal itself expires.

---

### Recommendation

Add an expiry guard inside `checkVotersAreValid` (or as a separate `Test` called alongside it) that rejects votes from DReps whose stored expiry, adjusted for dormant epochs, is strictly less than `currentEpoch`:

```haskell
checkDRepVotersAreActive ::
  EpochNo -> VState era -> [(Voter, GovActionState era)] -> Test (ConwayGovPredFailure era)
checkDRepVotersAreActive currentEpoch vState votes =
  checkDisallowedVotes votes ExpiredDRepVoter $ \_ -> \case
    DRepVoter cred ->
      case Map.lookup cred (vState ^. vsDRepsL) of
        Nothing        -> False
        Just drepState ->
          let actualExpiry = binOpEpochNo (+)
                               (vState ^. vsNumDormantEpochsL)
                               (drepState ^. drepExpiryL)
           in currentEpoch <= actualExpiry
    _ -> True
```

Additionally, `updateVotingDRepExpiries` should skip DReps whose actual expiry (stored expiry + dormant epochs) is already less than `currentEpoch`, so that expired DReps cannot self-resurrect by voting.

---

### Proof of Concept

**Setup:** `ppDRepActivityL = EpochInterval 2`, `ppGovActionLifetimeL = EpochInterval 5`.

1. **Epoch 0:** DRep `D` registers. Expiry set to epoch 2.
2. **Epoch 3:** DRep `D` is expired (`currentEpoch 3 > drepExpiry 2`). A governance proposal `P` (e.g., `ParameterChange`) is submitted.
3. **Epoch 3 (same tx):** `D` submits a vote `VoteYes` on `P`.
   - `checkVotersAreValid` passes: `isDRepVotingAllowed ParameterChange = True`.
   - `VotersDoNotExist` passes: `D` is still in `vsDRepsL`.
   - `updateVotingDRepExpiries` refreshes `D`'s expiry to `3 + 2 = 5`.
4. **Epoch 4 boundary:** Ratification pulser is built with `dpDRepState` containing `D` with `drepExpiry = 5`. `reCurrentEpoch = 4`. Since `4 <= 5`, `D` is active. `D`'s `VoteYes` stake is counted in `dRepAcceptedRatio`.
5. If `D`'s stake is sufficient to meet the threshold, `P` is enacted — despite `D` being expired at the time of voting.

The expired DRep's vote influences (or decides) a governance outcome it should have been ineligible to affect, directly analogous to the Magicsea H-5 pattern of voting with a stale/expired position.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L364-376)
```haskell
checkVotersAreValid ::
  forall era.
  ConwayEraPParams era =>
  EpochNo ->
  CommitteeState era ->
  [(Voter, GovActionState era)] ->
  Test (ConwayGovPredFailure era)
checkVotersAreValid currentEpoch committeeState votes =
  checkDisallowedVotes votes DisallowedVoters $ \gas ->
    \case
      CommitteeVoter {} -> isCommitteeVotingAllowed currentEpoch committeeState (gasAction gas)
      DRepVoter {} -> isDRepVotingAllowed (gasAction gas)
      StakePoolVoter {} -> isStakePoolVotingAllowed (gasAction gas)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L593-604)
```haskell
      internVoter = \case
        CommitteeVoter hotCred -> CommitteeVoter <$> internSet hotCred knownCommitteeMembers
        DRepVoter cred -> DRepVoter <$> internMap cred knownDReps
        StakePoolVoter poolId -> StakePoolVoter <$> internMap poolId knownStakePools
      (unknownVoters, knownVoters) =
        bimap Set.fromList Map.fromList $
          partitionEithers
            [ maybe (Left voter) (\v -> Right (v, votes)) (internVoter voter)
            | (voter, votes) <- Map.toList (unVotingProcedures gsVotingProcedures)
            ]

  failOnNonEmpty unknownVoters (injectFailure . VotersDoNotExist)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L481-491)
```haskell
isDRepVotingAllowed ::
  ConwayEraPParams era =>
  GovAction era ->
  Bool
isDRepVotingAllowed =
  isVotingAllowed . votingDRepThresholdInternal pp isElectedCommittee
  where
    -- Information about presence of committee or values in PParams are irrelevant for
    -- knowing if voting is allowed or not:
    pp = emptyPParams
    isElectedCommittee = False
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L272-292)
```haskell
updateVotingDRepExpiries ::
  ( EraTx era
  , ConwayEraTxBody era
  , ConwayEraCertState era
  ) =>
  Tx l era -> EpochNo -> EpochInterval -> CertState era -> CertState era
updateVotingDRepExpiries tx currentEpoch drepActivity certState =
  let numDormantEpochs = certState ^. certVStateL . vsNumDormantEpochsL
      updateVSDReps vsDReps =
        Map.foldlWithKey'
          ( \dreps voter _ -> case voter of
              DRepVoter cred ->
                Map.adjust
                  (drepExpiryL .~ computeDRepExpiry drepActivity currentEpoch numDormantEpochs)
                  cred
                  dreps
              _ -> dreps
          )
          vsDReps
          (unVotingProcedures $ tx ^. bodyTxL . votingProceduresTxBodyL)
   in certState & certVStateL . vsDRepsL %~ updateVSDReps
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L258-281)
```haskell
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
