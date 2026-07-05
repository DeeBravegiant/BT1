### Title
Expired Governance Action Can Be Ratified and Enacted Due to Missing Expiry Check in Ratification Path — (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs`)

---

### Summary

In `ratifyTransition`, the expiry guard `gasExpiresAfter < reCurrentEpoch` is placed exclusively in the **non-ratification** (`else`) branch. When a governance action satisfies all ratification conditions, it is enacted through the `then` branch with **no expiry check at all**. A proposal whose `gasExpiresAfter` epoch has already passed can therefore be ratified and enacted at the next epoch boundary, producing an unauthorized ledger state transition.

---

### Finding Description

`ratifyTransition` in `Ratify.hs` processes each `GovActionState` from the `RatifySignal`:

```haskell
gas@GovActionState {gasId, gasExpiresAfter} SSeq.:<| sigs -> do
  let govAction = gasAction gas
  if prevActionAsExpected gas ensPrevGovActionIds
      && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
      && not rsDelayed
      && withdrawalCanWithdraw govAction ensTreasury
      && acceptedByEveryone env st gas
    then do
      -- *** No expiry check here — action is enacted unconditionally ***
      newEnactState <- trans @(EraRule "ENACT" era) $ ...
      ...
    else do
      st' <- trans @(RATIFY era) $ TRC (env, st, RatifySignal sigs)
      -- Expiry check lives ONLY in the else branch:
      if gasExpiresAfter < reCurrentEpoch
        then pure $ st' & rsExpiredL %~ Set.insert gasId
        else pure st'
``` [1](#0-0) 

The `DRepPulser` is created at the epoch-`eNo` boundary and stores `dpCurrentEpoch` as the epoch the pulser will **complete in** (`eNo+1`). This value becomes `reCurrentEpoch` in the `RatifyEnv`:

```haskell
reCurrentEpoch = dpCurrentEpoch
``` [2](#0-1) 

A proposal with `gasExpiresAfter = eNo` is **not** expired when the pulser snapshot is taken at epoch `eNo` (since `eNo < eNo` is `False`), so it is included in `dpProposals`. When the pulser completes at epoch `eNo+1`, `reCurrentEpoch = eNo+1`, making `gasExpiresAfter < reCurrentEpoch` (`eNo < eNo+1`) `True` — the proposal is expired. Yet if it satisfies all five ratification predicates, the `then` branch fires and the action is enacted with no expiry guard.

The `rsDelayed` flag makes this concretely reachable: when a delaying action (`NoConfidence`, `HardForkInitiation`, `UpdateCommittee`, `NewConstitution`) is ratified at epoch `eNo`, `rsDelayed` is set to `True`, blocking ratification of all other proposals that epoch. [3](#0-2) 

The `RatifyState` documentation itself acknowledges that when `rsDelayed = True`, "each active proposal that has not become invalid will have its expiry date extended by one epoch" — but **no such extension is implemented anywhere in the code**: [4](#0-3) 

The EPOCH rule extracts `rsEnacted` and passes it directly to `proposalsApplyEnactment` without any post-hoc expiry filter: [5](#0-4) 

---

### Impact Explanation

Any governance action type — `ParameterChange`, `HardForkInitiation`, `UpdateCommittee`, `NewConstitution`, `TreasuryWithdrawals`, `NoConfidence` — that has expired but still satisfies vote thresholds can be enacted. This produces an unauthorized ledger state transition: protocol parameters, the constitutional committee, the constitution, or treasury balances are modified by a proposal that the protocol rules require to have been discarded. This matches:

> **Critical. Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.**

---

### Likelihood Explanation

The scenario is concretely reachable without any privileged access beyond normal governance participation:

1. A proposal `P` with `gasExpiresAfter = eNo` accumulates sufficient votes during epoch `eNo`.
2. A delaying action `D` (e.g., `HardForkInitiation`) is also ratified at the epoch `eNo` boundary, setting `rsDelayed = True` and blocking ratification of `P`.
3. The pulser for epoch `eNo+1` is created with `P` still in `dpProposals` and `dpCurrentEpoch = eNo+1`.
4. At the epoch `eNo+1` boundary, `rsDelayed = False`, `P` still satisfies all vote thresholds, and the `then` branch enacts `P` — despite `gasExpiresAfter = eNo < eNo+1 = reCurrentEpoch`.

This is a natural collision between the delay mechanism and proposal expiry, not a contrived edge case.

---

### Recommendation

Add the expiry predicate to the ratification guard in `ratifyTransition` so that an expired action can never enter the `then` (enactment) branch:

```haskell
if prevActionAsExpected gas ensPrevGovActionIds
    && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
    && not rsDelayed
    && withdrawalCanWithdraw govAction ensTreasury
    && acceptedByEveryone env st gas
    && not (gasExpiresAfter < reCurrentEpoch)   -- ADD: reject expired actions
  then ...
```

Additionally, implement the documented expiry-extension behaviour for `rsDelayed = True` so that proposals blocked by a delaying action receive a one-epoch extension of `gasExpiresAfter`, consistent with the `RatifyState` comment. [6](#0-5) 

---

### Proof of Concept

```
Epoch eNo:
  - Proposal P submitted with gasExpiresAfter = eNo, votes sufficient for ratification.
  - Delaying action D (e.g. HardForkInitiation) also reaches ratification threshold.
  - At epoch eNo boundary:
      ratifyTransition processes D first (reorderActions puts delaying actions first).
      D is ratified → rsDelayed := True.
      P is skipped (not rsDelayed = False).
      P is NOT expired (gasExpiresAfter=eNo < reCurrentEpoch=eNo is False).
      P remains in proposals; new pulser created with dpCurrentEpoch = eNo+1.

Epoch eNo+1:
  - Pulser completes with reCurrentEpoch = eNo+1.
  - ratifyTransition processes P:
      prevActionAsExpected = True
      validCommitteeTerm   = True
      not rsDelayed        = True   (reset at epoch boundary)
      withdrawalCanWithdraw = True
      acceptedByEveryone   = True   (votes still recorded)
      → then branch fires, P is enacted.
      → gasExpiresAfter < reCurrentEpoch (eNo < eNo+1 = True) is NEVER checked.

Result: P is enacted despite being expired, producing an unauthorized
        governance state transition.
```

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L334-359)
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
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L398-408)
```haskell
    !ratifyEnv =
      RatifyEnv
        { reInstantStake = dpInstantStake
        , reStakePoolDistr = finalStakePoolDistr
        , reDRepDistr = finalDRepDistr
        , reDRepState = dpDRepState
        , reCurrentEpoch = dpCurrentEpoch
        , reCommitteeState = dpCommitteeState
        , reAccounts = dpAccounts
        , reStakePools = dpStakePools
        }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L263-271)
```haskell
  , rsDelayed :: !Bool
  -- ^ This flag is set to true if one of the proposals that was ratified at the
  -- last epoch boundary was a delaying action. This means that no other
  -- proposals will be ratified this epoch and each active proposal that has not
  -- become invalid will have its expiry date extended by one epoch.
  --
  -- This flag is reset at each epoch boundary before the `RATIFY` rule gets
  -- called, but it might immediately be set to `True` again after the `RATIFY`
  -- rule has finished execution.
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L296-315)
```haskell
    ratifyState@RatifyState {rsEnactState, rsEnacted, rsExpired} =
      extractDRepPulsingState pulsingState

    (chainAccountState2, dState2, EnactState {..}) =
      applyEnactedWithdrawals chainAccountState1 (certState1 ^. certDStateL) rsEnactState

    -- NOTE: It is important that we apply the results of ratification
    -- and enactment from the pulser to the working copy of proposals.
    -- The proposals in the pulser are a subset of the current
    -- proposals, in that, in addition to the proposals in the pulser,
    -- the current proposals now contain new proposals submitted during
    -- the epoch that just passed (we are at its boundary here) and
    -- any votes that were submitted to the already pulsing as well as
    -- newly submitted proposals. We only need to apply the enactment
    -- operations to this superset to get a new set of proposals with:
    -- enacted actions and their sibling subtrees, as well as expired
    -- actions and their subtrees, removed, and with all the votes
    -- intact for the rest of them.
    (newProposals, enactedActions, removedDueToEnactment, expiredActions) =
      proposalsApplyEnactment rsEnacted rsExpired (govState0 ^. proposalsGovStateL)
```
