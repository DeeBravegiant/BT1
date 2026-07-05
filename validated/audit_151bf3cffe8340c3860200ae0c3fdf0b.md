### Title
Guardrails Script Hash Checked Only at Proposal Submission, Not at Ratification/Enactment — (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs` / `Ratify.hs` / `Enact.hs`)

---

### Summary

The Conway governance system checks that a `ParameterChange` or `TreasuryWithdrawals` proposal's embedded guardrails script hash matches the current constitution's guardrails script hash at **proposal submission time** (the `GOV` rule). This check is never repeated at **ratification time** (`RATIFY` rule) or **enactment time** (`ENACT` rule). If the constitution is replaced between submission and ratification, a proposal carrying the old (now-stale) guardrails hash is ratified and enacted without any re-validation, bypassing the new constitution's guardrails entirely.

---

### Finding Description

**Check at submission (GOV rule only)**

In `conwayGovTransition`, for every incoming `ParameterChange` or `TreasuryWithdrawals` proposal, the ledger calls `checkGuardrailsScriptHash` to assert that the proposal's embedded policy hash equals the current constitution's `constitutionGuardrailsScriptHash`:

```haskell
-- Gov.hs lines 546-558
TreasuryWithdrawals wdrls proposalPolicy -> do
    ...
    runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy

ParameterChange _ _ proposalPolicy ->
    runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy
``` [1](#0-0) 

The `checkGuardrailsScriptHash` function itself:

```haskell
checkGuardrailsScriptHash expectedHash actualHash =
  failureUnless (actualHash == expectedHash) $
    InvalidGuardrailsScriptHash actualHash expectedHash
``` [2](#0-1) 

This check is the **only** place the guardrails hash is validated.

**No check at ratification (RATIFY rule)**

The `ratifyTransition` function lists every condition that must hold before a proposal is ratified:

```haskell
if prevActionAsExpected gas ensPrevGovActionIds
  && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
  && not rsDelayed
  && withdrawalCanWithdraw govAction ensTreasury
  && acceptedByEveryone env st gas
``` [3](#0-2) 

There is no guardrails script hash comparison here. The `EnactState` carried through the RATIFY rule does contain the current `ensConstitution` (and therefore the current `constitutionGuardrailsScriptHash`), but it is never compared against the proposal's embedded policy hash. [4](#0-3) 

**No check at enactment (ENACT rule)**

`enactmentTransition` for `ParameterChange` and `TreasuryWithdrawals` simply applies the state change with no guardrails validation:

```haskell
ParameterChange _ ppup _ ->
    st & ensCurPParamsL %~ (`applyPPUpdates` ppup)
       & ensPrevPParamUpdateL .~ SJust (GovPurposeId govActionId)
TreasuryWithdrawals wdrls _ ->
    ...ensTreasury = ensTreasury st <-> wdrlsAmount
``` [5](#0-4) 

---

### Impact Explanation

The guardrails script is the on-chain mechanism by which the constitution enforces hard limits on protocol parameter changes and treasury withdrawals (e.g., preventing fees from being set to zero, or withdrawals from exceeding a safe bound). When the constitution is replaced via a `NewConstitution` action, the new guardrails script hash (`H2`) becomes the authoritative constraint. Any `ParameterChange` or `TreasuryWithdrawals` proposal that was submitted under the old constitution (with hash `H1`) and that would **fail** the new guardrails script can still be ratified and enacted, because the RATIFY and ENACT rules never re-check the hash.

This constitutes an **unauthorized governance/protocol-parameter/treasury action being enacted**, matching the Critical impact tier: *"Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted."*

---

### Likelihood Explanation

The scenario requires two concurrent live proposals: a `NewConstitution` and a `ParameterChange`/`TreasuryWithdrawals`. Both are normal governance operations. The `NewConstitution` action is a delaying action (see `delayingAction` in `Ratify.hs` line 287), so it prevents other proposals from being ratified in the same epoch it is enacted. However, the stale-hash proposal remains in the `Proposals` forest and is eligible for ratification in the **next** epoch, when the new constitution is already active but the guardrails hash check is never re-run. [6](#0-5) 

Any DRep or SPO coalition that can ratify both proposals in sequence can exploit this. No privileged key or supermajority beyond normal ratification thresholds is required.

---

### Recommendation

Add a guardrails script hash consistency check inside `ratifyTransition` (or equivalently inside `enactmentTransition`) for `ParameterChange` and `TreasuryWithdrawals` actions. At ratification time the current constitution is available via `rsEnactState . ensConstitution . constitutionGuardrailsScriptHash`; this should be compared against the proposal's embedded policy hash before the proposal is admitted to enactment. A proposal whose policy hash no longer matches the active constitution's guardrails hash should be treated as expired/invalid rather than ratified.

---

### Proof of Concept

1. **Epoch N** — Constitution has guardrails script hash `H1`. An attacker (or any proposer) submits a `ParameterChange` proposal `P` with `proposalPolicy = SJust H1`. The GOV rule accepts it because `H1 == constitutionPolicy`. The guardrails Plutus script `H1` is executed as a witness in the same transaction and passes (possibly with permissive constraints).

2. **Epoch N+1** — A `NewConstitution` proposal is ratified, replacing the constitution with one whose guardrails script hash is `H2` (more restrictive). Because `NewConstitution` is a delaying action, `rsDelayed = True` and proposal `P` is not ratified this epoch.

3. **Epoch N+2** — The RATIFY rule processes proposal `P`. It checks `prevActionAsExpected`, `validCommitteeTerm`, `withdrawalCanWithdraw`, and `acceptedByEveryone` — all pass. The guardrails hash check (`H1 vs H2`) is **never performed**. `P` is ratified and forwarded to ENACT.

4. **ENACT** — `enactmentTransition` applies `applyPPUpdates` unconditionally. The parameter change that would have been rejected by guardrails script `H2` is now part of the ledger state. [7](#0-6) [8](#0-7)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L420-426)
```haskell
checkGuardrailsScriptHash ::
  StrictMaybe ScriptHash ->
  StrictMaybe ScriptHash ->
  Test (ConwayGovPredFailure era)
checkGuardrailsScriptHash expectedHash actualHash =
  failureUnless (actualHash == expectedHash) $
    InvalidGuardrailsScriptHash actualHash expectedHash
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L546-558)
```haskell
            -- Guardrails script hash check
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy

            -- The sum of all withdrawals must be positive
            F.fold wdrls /= mempty ?! (injectFailure . ZeroTreasuryWithdrawals) pProcGovAction
          UpdateCommittee _mPrevGovActionId membersToRemove membersToAdd _qrm -> do
            let conflicting = Set.intersection (Map.keysSet membersToAdd) membersToRemove
             in failOnNonEmptySet conflicting (injectFailure . ConflictingCommitteeUpdate)

            let invalidMembers = Map.filter (<= currentEpoch) membersToAdd
             in failOnNonEmptyMap invalidMembers (injectFailure . ExpirationEpochTooSmall)
          ParameterChange _ _ proposalPolicy ->
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L283-290)
```haskell
delayingAction :: GovAction era -> Bool
delayingAction NoConfidence {} = True
delayingAction HardForkInitiation {} = True
delayingAction UpdateCommittee {} = True
delayingAction NewConstitution {} = True
delayingAction TreasuryWithdrawals {} = False
delayingAction ParameterChange {} = False
delayingAction InfoAction {} = False
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L334-360)
```haskell
  case rsig of
    gas@GovActionState {gasId, gasExpiresAfter} SSeq.:<| sigs -> do
      let govAction = gasAction gas
      if prevActionAsExpected gas ensPrevGovActionIds
        && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
        && not rsDelayed
        && withdrawalCanWithdraw govAction ensTreasury
        && acceptedByEveryone env st gas
        then do
          newEnactState <-
            trans @(EraRule "ENACT" era) $
              TRC ((), rsEnactState, EnactSignal gasId govAction)
          let
            st' =
              st
                & rsEnactStateL .~ newEnactState
                & rsDelayedL .~ delayingAction govAction
                & rsEnactedL %~ (Seq.:|> gas)
          trans @(RATIFY era) $ TRC (env, st', RatifySignal sigs)
        else do
          -- This action hasn't been ratified yet. Process the remaining actions.
          st' <- trans @(RATIFY era) $ TRC (env, st, RatifySignal sigs)
          -- Finally, filter out actions that have expired.
          if gasExpiresAfter < reCurrentEpoch
            then pure $ st' & rsExpiredL %~ Set.insert gasId
            else pure st'
    SSeq.Empty -> pure $ st & rsEnactStateL . ensTreasuryL .~ Coin 0
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L129-140)
```haskell
data EnactState era = EnactState
  { ensCommittee :: !(StrictMaybe (Committee era))
  -- ^ Constitutional Committee
  , ensConstitution :: !(Constitution era)
  -- ^ Constitution
  , ensCurPParams :: !(PParams era)
  , ensPrevPParams :: !(PParams era)
  , ensTreasury :: !Coin
  , ensWithdrawals :: !(Map (Credential Staking) Coin)
  , ensPrevGovActionIds :: !(GovRelation StrictMaybe)
  -- ^ Last enacted GovAction Ids
  }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs (L83-116)
```haskell
enactmentTransition :: forall era. EraPParams era => TransitionRule (ENACT era)
enactmentTransition = do
  TRC ((), st, EnactSignal govActionId act) <- judgmentContext

  pure $!
    case act of
      ParameterChange _ ppup _ ->
        st
          & ensCurPParamsL %~ (`applyPPUpdates` ppup)
          & ensPrevPParamUpdateL .~ SJust (GovPurposeId govActionId)
      HardForkInitiation _ pv ->
        st
          & ensProtVerL .~ pv
          & ensPrevHardForkL .~ SJust (GovPurposeId govActionId)
      TreasuryWithdrawals wdrls _ ->
        let wdrlsAmount = fold wdrls
            wdrlsNoNetworkId = Map.mapKeys (^. accountAddressCredentialL) wdrls
         in st
              { ensWithdrawals = Map.unionWith (<>) wdrlsNoNetworkId $ ensWithdrawals st
              , ensTreasury = ensTreasury st <-> wdrlsAmount
              }
      NoConfidence _ ->
        st
          & ensCommitteeL .~ SNothing
          & ensPrevCommitteeL .~ SJust (GovPurposeId govActionId)
      UpdateCommittee _ membersToRemove membersToAdd newThreshold -> do
        st
          & ensCommitteeL %~ SJust . updatedCommittee membersToRemove membersToAdd newThreshold
          & ensPrevCommitteeL .~ SJust (GovPurposeId govActionId)
      NewConstitution _ c ->
        st
          & ensConstitutionL .~ c
          & ensPrevConstitutionL .~ SJust (GovPurposeId govActionId)
      InfoAction -> st
```
