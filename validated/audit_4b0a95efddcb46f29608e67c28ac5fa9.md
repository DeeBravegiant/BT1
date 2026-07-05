### Title
Expired DRep Self-Revival via Vote Submission Bypasses Activity Expiry Enforcement - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`)

---

### Summary

`updateVotingDRepExpiries` unconditionally resets the expiry of any DRep that appears in a transaction's voting procedures, without first checking whether that DRep is already expired. This allows an expired DRep to self-revive by submitting a vote, bypassing the DRep activity/expiry mechanism and causing their delegated stake to count in governance ratification.

---

### Finding Description

The Conway era enforces a DRep activity requirement: a DRep that does not vote for `ppDRepActivity` epochs becomes expired and is excluded from ratification vote counting in `dRepAcceptedRatio`. However, the expiry check is only applied at ratification time (RATIFY rule), not when a vote is submitted (GOV rule / CERTS rule).

**Root cause — `updateVotingDRepExpiries`** in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`:

```haskell
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
``` [1](#0-0) 

For every `DRepVoter cred` in the transaction's voting procedures, `Map.adjust` unconditionally overwrites `drepExpiry` with a freshly computed future epoch — **regardless of whether the DRep is already expired**. There is no guard of the form `if actualExpiry >= currentEpoch`.

**Contrast with `updateDormantDRepExpiry`** in the same file, which explicitly refuses to bump an already-expired DRep:

```haskell
updateExpiry =
  drepExpiryL
    %~ \currentExpiry ->
      let actualExpiry = binOpEpochNo (+) numDormantEpochs currentExpiry
       in if actualExpiry < currentEpoch
            then currentExpiry   -- ← expired DRep is NOT bumped
            else actualExpiry
``` [2](#0-1) 

The asymmetry is the bug: dormant-epoch bumps guard against reviving expired DReps; vote-triggered bumps do not.

**GOV rule does not check DRep expiry when accepting votes.** In `conwayGovTransition`, `knownDReps` is the full `vsDReps` map (which retains expired DReps until they explicitly unregister), and `checkVotersAreValid` only verifies that the action type permits DRep voting — it never checks `drepExpiry`:

```haskell
knownDReps = vsDReps certVState
...
DRepVoter cred -> DRepVoter <$> internMap cred knownDReps
...
checkVotersAreValid currentEpoch committeeState votes =
  checkDisallowedVotes votes DisallowedVoters $ \gas ->
    \case
      DRepVoter {} -> isDRepVotingAllowed (gasAction gas)
``` [3](#0-2) [4](#0-3) 

**Execution order in the LEDGER rule** confirms the self-revival path: `updateVotingDRepExpiries` is applied to `certState` before the GOV rule runs, so by the time ratification evaluates `drepExpiry`, the expired DRep's expiry has already been reset to a future epoch:

```haskell
certState' <-
  ...
  pure $
    certState
      & updateDormantDRepExpiries tx curEpochNo
      & updateVotingDRepExpiries tx curEpochNo (pp ^. ppDRepActivityL)
      ...
certStateAfterCERTS <- trans @(EraRule "CERTS" era) $ TRC (..., certState', ...)
proposalsState     <- trans @(EraRule "GOV"   era) $ TRC (..., certStateAfterCERTS, ...)
``` [5](#0-4) 

The same path exists in the pre-PV11 CERTS rule: [6](#0-5) 

At ratification, `dRepAcceptedRatio` checks `reCurrentEpoch > drepExpiry drepState` to exclude expired DReps: [7](#0-6) 

But because the expiry was already reset by `updateVotingDRepExpiries`, the DRep passes this check and its delegated stake is counted.

---

### Impact Explanation

An expired DRep — one that should be excluded from governance ratification — can unilaterally re-activate itself by submitting a transaction containing any vote. After self-revival, the DRep's full delegated stake is counted in `dRepAcceptedRatio`, potentially tipping the yes/no ratio for `TreasuryWithdrawals`, `ParameterChange`, `HardForkInitiation`, `NewConstitution`, or `UpdateCommittee` proposals. This constitutes an unauthorized governance action being enacted, matching the **Critical** impact tier: *Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.*

---

### Likelihood Explanation

Any expired DRep credential holder can trigger this with a single ordinary transaction containing a `VotingProcedures` field. No privileged access, no key compromise, no majority collusion, and no external dependency is required. The attacker controls the entry path entirely.

---

### Recommendation

Add an expiry guard inside `updateVotingDRepExpiries` before bumping the expiry, mirroring the guard already present in `updateDormantDRepExpiry`:

```haskell
DRepVoter cred ->
  Map.adjust
    ( \drepState ->
        let actualExpiry =
              binOpEpochNo (+) numDormantEpochs (drepState ^. drepExpiryL)
         in if actualExpiry < currentEpoch
              then drepState  -- expired DRep: do not revive
              else drepState & drepExpiryL
                    .~ computeDRepExpiry drepActivity currentEpoch numDormantEpochs
    )
    cred
    dreps
```

Alternatively, add an explicit expiry check in the GOV rule's voter validation (`checkVotersAreValid`) to reject votes from expired DReps outright, consistent with how `committeeAcceptedRatio` treats expired committee members.

---

### Proof of Concept

1. Register a DRep and let `ppDRepActivity + 1` epochs pass without voting → `isDRepExpired drep` returns `True`.
2. Submit a transaction with `VotingProcedures` containing a `VoteYes` from the expired DRep on any live governance action.
3. Observe that `updateVotingDRepExpiries` resets `drepExpiry` to `currentEpoch + ppDRepActivity`.
4. At the next epoch boundary, `dRepAcceptedRatio` counts the DRep's delegated stake in the yes-vote numerator and denominator, as if the DRep had never expired.
5. A governance action that would have failed ratification (e.g., a `TreasuryWithdrawals` proposal just below the DRep threshold) now passes because the revived DRep's stake tips the ratio above the threshold.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L237-241)
```haskell
          pure $
            certState
              & updateDormantDRepExpiries tx currentEpoch
              & updateVotingDRepExpiries tx currentEpoch (pp ^. ppDRepActivityL)
              & certDStateL . accountsL %~ drainAccounts withdrawals
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L278-292)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L322-328)
```haskell
    updateExpiry =
      drepExpiryL
        %~ \currentExpiry ->
          let actualExpiry = binOpEpochNo (+) numDormantEpochs currentExpiry
           in if actualExpiry < currentEpoch
                then currentExpiry
                else actualExpiry
```

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L593-596)
```haskell
      internVoter = \case
        CommitteeVoter hotCred -> CommitteeVoter <$> internSet hotCred knownCommitteeMembers
        DRepVoter cred -> DRepVoter <$> internMap cred knownDReps
        StakePoolVoter poolId -> StakePoolVoter <$> internMap poolId knownStakePools
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L382-420)
```haskell
          certState' <-
            if hardforkConwayMoveWithdrawalsAndDRepChecksToLedgerRule $ pp ^. ppProtocolVersionL
              then do
                let withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
                Shelley.testIncompleteAndMissingWithdrawals (certState ^. certDStateL . accountsL) withdrawals
                pure $
                  certState
                    & updateDormantDRepExpiries tx curEpochNo
                    & updateVotingDRepExpiries tx curEpochNo (pp ^. ppDRepActivityL)
                    & certDStateL . accountsL %~ drainAccounts withdrawals
              else pure certState

          certStateAfterCERTS <-
            trans @(EraRule "CERTS" era) $
              TRC
                ( CertsEnv tx pp curEpochNo committee committeeProposals
                , certState'
                , StrictSeq.fromStrict $ txBody ^. certsTxBodyL
                )

          -- Votes and proposals from signal tx
          let govSignal =
                GovSignal
                  { gsVotingProcedures = txBody ^. votingProceduresTxBodyL
                  , gsProposalProcedures = txBody ^. proposalProceduresTxBodyL
                  , gsCertificates = txBody ^. certsTxBodyL
                  }
          proposalsState <-
            trans @(EraRule "GOV" era) $
              TRC
                ( GovEnv
                    (txIdTxBody txBody)
                    curEpochNo
                    pp
                    (govState ^. constitutionGovStateL . constitutionGuardrailsScriptHashL)
                    certStateAfterCERTS
                    (govState ^. committeeGovStateL)
                , proposals
                , govSignal
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L264-267)
```haskell
          case Map.lookup cred reDRepState of
            Nothing -> (yes, tot) -- drep is not registered, so we don't consider it
            Just drepState
              | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
```
