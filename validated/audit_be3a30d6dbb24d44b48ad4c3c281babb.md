### Title
Stale DRep Reverse Delegation During Bootstrap Phase Allows Attacker to Erase Victim's Vote Delegation via DRep Unregistration - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`, `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version < 10), when a stake credential re-delegates its vote from DRep A to DRep B, the old DRep A's `drepDelegs` reverse-delegation set is not cleaned up (a known preserved bug, issue #4772). When DRep A subsequently unregisters, the `ConwayUnRegDRep` rule uses the stale `drepDelegs` set to clear account delegations — incorrectly erasing the victim's current, valid delegation to DRep B. This cleared state persists through the PV 10 hard fork because `updateDRepDelegations` rebuilds `drepDelegs` from account state, which now shows no delegation. The victim's stake permanently stops contributing to DRep B's voting power until the victim manually re-delegates.

---

### Finding Description

**Root Cause — Stale Reverse Delegation Created in `processDelegationInternal`:**

In `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`, the `processDelegationInternal` function is called with `preserveIncorrectDelegation = True` when `pvMajor pv < natVersion @10` (bootstrap phase):

```haskell
-- Deleg.hs line 287-292
pure $
  processDelegationInternal
    (pvMajor pv < natVersion @10)   -- True during bootstrap
    internedCred
    (Just accountState)
    delegatee
    certState
```

Inside `processDelegationInternal`, the `delegVote` branch handles re-delegation:

```haskell
-- Deleg.hs lines 347-377
delegVote dRep cState =
  let handleReverseDelegation =
        case dRepToCred dRep of
          Just dRepCred
            | isNothing mAccountState || preserveIncorrectDelegation ->
                -- Only ADDS to new DRep's drepDelegs; does NOT remove from old DRep's drepDelegs
                certVStateL . vsDRepsL
                  %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
          _
            | Just accountState <- mAccountState ->
                -- Correct path: calls unDelegReDelegDRep which removes from old DRep
                certVStateL %~ unDelegReDelegDRep stakeCred accountState (Just dRep)
            | otherwise -> id
```

When `preserveIncorrectDelegation = True`, re-delegating from DRep A → DRep B:
- Account state: correctly updated to `DRepCredential drepBCred`
- DRep B's `drepDelegs`: correctly gains `stakeCred`
- **DRep A's `drepDelegs`: still contains `stakeCred` (stale)**

**Exploitation — Stale Entry Triggers Incorrect Delegation Erasure in `ConwayUnRegDRep`:**

In `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`, when DRep A unregisters, the rule uses `drepDelegs` to clear account delegations:

```haskell
-- GovCert.hs lines 244-254
let
  certState' = certState & certVStateL . vsDRepsL %~ Map.delete cred
  clearDRepDelegations delegs accountsMap =
    foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
pure $
  case mDRepState of
    Nothing -> certState'
    Just dRepState ->
      certState'
        & certDStateL . accountsL . accountsMapL
          %~ clearDRepDelegations (drepDelegs dRepState)
```

`drepDelegs dRepState` for DRep A still contains `stakeCred` (stale). `clearDRepDelegations` sets `dRepDelegationAccountStateL .~ Nothing` for `stakeCred`, **erasing the victim's current valid delegation to DRep B**.

**Persistence Through Hard Fork:**

At the PV 10 hard fork, `updateDRepDelegations` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/HardFork.hs` rebuilds `drepDelegs` from account state:

```haskell
-- HardFork.hs lines 82-105
updateDRepDelegations certState =
  ...
  adjustDelegations ds stakeCred accountState =
    case accountState ^. dRepDelegationAccountStateL of
      Just (DRepCredential dRep) -> ...  -- adds to drepDelegs
      _ -> (ds, accountState)            -- Nothing: no restoration
```

Since the victim's account state now shows `Nothing`, `updateDRepDelegations` does not restore the delegation. The victim's stake permanently stops contributing to DRep B's voting power in post-bootstrap governance.

---

### Impact Explanation

After the PV 10 hard fork, DRep votes are required for ratification of governance actions. The `computeDRepDistr` function in `DRepPulser.hs` reads `dRepDelegationAccountStateL` from account state to build the DRep stake distribution used in `dRepAcceptedRatio`. With the victim's delegation cleared, their stake is excluded from DRep B's voting weight. If enough stake is affected, governance actions that DRep B supports may fail to reach the required threshold, or actions DRep B opposes may pass. This constitutes unauthorized influence over governance ratification outcomes.

**Allowed impact matched:** *Critical — Unauthorized governance action is enacted* (by artificially reducing a DRep's voting power below or above a ratification threshold).

---

### Likelihood Explanation

- DRep registration is permissionless; any party can register as a DRep with a deposit.
- During bootstrap (PV 9), users legitimately delegate to DReps in preparation for post-bootstrap governance, and re-delegation is common.
- The attacker only needs to: (1) register as a DRep, (2) attract delegations, (3) wait for delegators to re-delegate to other DReps during bootstrap, (4) unregister their DRep.
- Step 4 returns the attacker's deposit, making the attack economically neutral.
- The victim has no on-chain signal that their delegation was cleared.
- The window is the entire Conway bootstrap phase (PV 9), which may still be active on mainnet given the presence of the `dijkstra` era in this repository.

---

### Recommendation

In `GovCert.hs`, the `ConwayUnRegDRep` handler should not use `drepDelegs` to clear account delegations. Instead, it should verify that the account's current `dRepDelegationAccountStateL` actually points to the unregistering DRep before clearing it:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr
    (\stakeCred acc ->
      Map.adjust
        (\as -> if as ^. dRepDelegationAccountStateL == Just (DRepCredential cred)
                then as & dRepDelegationAccountStateL .~ Nothing
                else as)
        stakeCred acc)
    accountsMap
    delegs
```

Alternatively, iterate over all accounts and clear only those whose `dRepDelegationAccountStateL` matches the unregistering DRep credential, rather than trusting the potentially stale `drepDelegs` set.

---

### Proof of Concept

1. Bootstrap phase (PV 9) is active.
2. Attacker registers DRep A (`ConwayRegDRep`).
3. Victim S registers stake credential and delegates vote to DRep A (`ConwayRegDelegCert` with `DelegVote (DRepCredential drepACred)`).
   - Account state: `dRepDelegationAccountStateL = Just (DRepCredential drepACred)`
   - DRep A's `drepDelegs = {S}`
4. Victim S re-delegates vote to DRep B (`ConwayDelegCert` with `DelegVote (DRepCredential drepBCred)`).
   - `processDelegationInternal` called with `preserveIncorrectDelegation = True`
   - Account state: `dRepDelegationAccountStateL = Just (DRepCredential drepBCred)` ✓
   - DRep B's `drepDelegs = {S}` ✓
   - DRep A's `drepDelegs = {S}` ← **stale, not removed**
5. Attacker unregisters DRep A (`ConwayUnRegDRep`).
   - `clearDRepDelegations {S} accountsMap` executes
   - Victim S's account: `dRepDelegationAccountStateL .~ Nothing` ← **incorrectly cleared**
6. Hard fork to PV 10 (`updateDRepDelegations`).
   - S has `dRepDelegationAccountStateL = Nothing` → not added to any DRep's `drepDelegs`
7. Post-bootstrap governance: S's stake contributes to no DRep's voting power. DRep B's effective stake is permanently reduced. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L285-292)
```haskell
        Just (internedCred, accountState) -> do
          pure $
            processDelegationInternal
              (pvMajor pv < natVersion @10)
              internedCred
              (Just accountState)
              delegatee
              certState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L347-377)
```haskell
    delegVote dRep cState =
      let handleReverseDelegation =
            case dRepToCred dRep of
              Just dRepCred
                -- This is the case where we only add the new reverse delegation and do not remove
                -- the old one, which is the behavior that we want:
                --
                -- 1) for new accounts, since there is no old reverse delegation to remove
                --
                -- 2) in the bootstrap phase, in order to preserve the incorrect behavior, where old reverse
                --   delegation for the prior DRep was wrongfully retained. It is important to note
                --   that in case when the new delegation was to a predefined DRep, the reverse
                --   delegations where handled correctly even in the boostrap phase
                --
                -- For reference here is the original bug report:
                --   https://github.com/IntersectMBO/cardano-ledger/issues/4772
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
              _
                -- AccountState existed before this delegation, therefore we need to properly handle
                -- potential undelegation of the old DRep
                | Just accountState <- mAccountState ->
                    certVStateL %~ unDelegReDelegDRep stakeCred accountState (Just dRep)
                -- If this is a fresh registration with delegation to a predefined DRep, there are
                -- no extra steps that need to be done
                | otherwise -> id
       in cState
            & certDStateL . accountsL
              %~ adjustAccountState (dRepDelegationAccountStateL ?~ dRep) stakeCred
            & handleReverseDelegation
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L244-254)
```haskell
        certState' =
          certState & certVStateL . vsDRepsL %~ Map.delete cred
        clearDRepDelegations delegs accountsMap =
          foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
      pure $
        case mDRepState of
          Nothing -> certState'
          Just dRepState ->
            certState'
              & certDStateL . accountsL . accountsMapL
                %~ clearDRepDelegations (drepDelegs dRepState)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/HardFork.hs (L82-105)
```haskell
updateDRepDelegations :: ConwayEraCertState era => CertState era -> CertState era
updateDRepDelegations certState =
  let accountsMap = certState ^. certDStateL . accountsL . accountsMapL
      dReps =
        -- Reset all delegations in order to remove any inconsistencies
        -- Delegations will be reset accordingly below.
        Map.map (\dRepState -> dRepState {drepDelegs = Set.empty}) $
          certState ^. certVStateL . vsDRepsL
      (dRepsWithDelegations, accountsWithoutUnknownDRepDelegations) =
        Map.mapAccumWithKey adjustDelegations dReps accountsMap
      adjustDelegations ds stakeCred accountState =
        case accountState ^. dRepDelegationAccountStateL of
          Just (DRepCredential dRep) ->
            let addDelegation _ dRepState =
                  Just $ dRepState {drepDelegs = Set.insert stakeCred (drepDelegs dRepState)}
             in case Map.updateLookupWithKey addDelegation dRep ds of
                  (Nothing, _) -> (ds, accountState & dRepDelegationAccountStateL .~ Nothing)
                  (Just _, ds') -> (ds', accountState)
          _ -> (ds, accountState)
   in certState
        -- Remove dangling delegations to non-existent DReps:
        & certDStateL . accountsL . accountsMapL .~ accountsWithoutUnknownDRepDelegations
        -- Populate DRep delegations with delegatees
        & certVStateL . vsDRepsL .~ dRepsWithDelegations
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L228-241)
```haskell
    addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
      dRep <- accountState ^. dRepDelegationAccountStateL
      let
        balance = accountState ^. balanceAccountStateL
        updatedDistr = Map.insertWith (<>) dRep (stakeAndDeposits <> balance) distr
      Just $ case dRep of
        DRepAlwaysAbstain -> updatedDistr
        DRepAlwaysNoConfidence -> updatedDistr
        DRepCredential cred
          -- TODO: Potential optimization. Avoid this membership check, since delegation is
          -- guaranteed to exist. I believe it would also be safe for PV9, but we need to verify
          -- that it is in fact true due to #4772
          | Map.member cred regDReps -> updatedDistr
          | otherwise -> distr
```
