### Title
Stale Reverse DRep Delegation During Bootstrap Causes Incorrect Account Delegation Clearing on DRep Unregistration — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version < 10), when a stake credential redelegates its vote from DRep A to DRep B, the old reverse-delegation entry in DRep A's `drepDelegs` set is not removed. When DRep A subsequently unregisters, the `ConwayUnRegDRep` handler in `GovCert.hs` uses the stale `drepDelegs` set to clear account delegations, incorrectly nullifying the vote delegation of accounts that had already moved to DRep B. This reduces DRep B's effective voting power in the DRep distribution used for governance ratification.

---

### Finding Description

**Root cause — `processDelegationInternal`, Deleg.hs lines 347–377:**

When `preserveIncorrectDelegation` is `True` (i.e., `pvMajor pv < natVersion @10`), the `delegVote` branch only inserts the new reverse delegation into the new DRep's `drepDelegs` set. It never removes the old reverse delegation from the previous DRep's `drepDelegs` set:

```haskell
| isNothing mAccountState || preserveIncorrectDelegation ->
    certVStateL . vsDRepsL
      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
``` [1](#0-0) 

Compare this to the correct post-bootstrap path (line 370), which calls `unDelegReDelegDRep` and properly removes the old entry:

```haskell
| Just accountState <- mAccountState ->
    certVStateL %~ unDelegReDelegDRep stakeCred accountState (Just dRep)
``` [2](#0-1) 

`unDelegReDelegDRep` in `VState.hs` correctly deletes the old credential from the previous DRep's set before adding it to the new one:

```haskell
vsDRepsL %~ addNewDelegation . Map.adjust (drepDelegsL %~ Set.delete stakeCred) dRepCred
``` [3](#0-2) 

**Trigger — `ConwayUnRegDRep`, GovCert.hs lines 244–254:**

When a DRep unregisters, the handler iterates over its `drepDelegs` set and sets `dRepDelegationAccountStateL` to `Nothing` for every credential in that set:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
...
certState' & certDStateL . accountsL . accountsMapL
  %~ clearDRepDelegations (drepDelegs dRepState)
``` [4](#0-3) 

Because DRep A's `drepDelegs` still contains the stale credential (the user who had already redelegated to DRep B), when DRep A unregisters, the user's account `dRepDelegationAccountStateL` is set to `Nothing` — even though the account is now correctly delegated to DRep B.

This is explicitly confirmed by the test in `DelegSpec.hs`:

```haskell
-- we need to preserve the buggy behavior until the bootstrap phase is over.
ifBootstrap
  ( do
      accounts <- getsNES $ nesEsL . esLStateL . lsCertStateL . certDStateL . accountsL
      expectNothingExpr (lookupDRepDelegation cred accounts)  -- delegation is CLEARED
      expecteReverseDRepDelegation cred drepCred2 True
  )
  (expectDelegatedVote cred (DRepCredential drepCred2))
``` [5](#0-4) 

---

### Impact Explanation

`computeDRepDistr` in `DRepPulser.hs` builds the DRep stake distribution used by `dRepAcceptedRatio` in `Ratify.hs` for governance ratification. It reads `dRepDelegationAccountStateL` directly from each account state:

```haskell
addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
  dRep <- accountState ^. dRepDelegationAccountStateL
  ...
``` [6](#0-5) 

When a user's account delegation is incorrectly cleared to `Nothing`, their stake is excluded from the DRep distribution entirely. This reduces DRep B's effective voting power. Depending on whether DRep B voted Yes or No:

- If DRep B voted **Yes**: its stake in the numerator is reduced, potentially dropping the acceptance ratio below the threshold and **blocking a legitimate governance action** (e.g., a `HardForkInitiation` or `ParameterChange`, which are the only actions permitted during bootstrap per `isBootstrapAction`).
- If DRep B voted **No** or abstained: only the denominator shrinks, potentially **pushing the acceptance ratio above the threshold** and causing an **unauthorized governance action to be enacted**.

Both scenarios fall within the allowed Critical impact: *"Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted."*

---

### Likelihood Explanation

The attack is fully permissionless and executable by any unprivileged transaction sender during the Conway bootstrap phase:

1. Attacker registers as a DRep (requires only the `ppDRepDeposit` deposit, which is refunded on unregistration).
2. Attacker creates stake credentials and delegates them to their own DRep.
3. Attacker redelegates those credentials to a target DRep B (the one whose voting power they wish to reduce).
4. Attacker submits `UnRegDRepTxCert` to unregister their DRep, recovering the deposit.
5. All credentials that passed through the attacker's DRep have their account delegations cleared to `Nothing`, silently removing their stake from DRep B's distribution.

No privileged access, social engineering, or majority is required. The deposit is fully recovered, making the attack economically free.

---

### Recommendation

The root fix is already present in `updateDRepDelegations` (`HardFork.hs` lines 82–105), which resets all `drepDelegs` sets and repopulates them from account states when transitioning to protocol version 10: [7](#0-6) 

However, the bug remains exploitable for the entire duration of the bootstrap phase. The immediate mitigation for `ConwayUnRegDRep` is to not rely on the potentially stale `drepDelegs` set when clearing account delegations. Instead, the handler should only clear an account's delegation if `dRepDelegationAccountStateL` currently points to the unregistering DRep — i.e., add a guard:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr
    (Map.adjust (\as ->
      if as ^. dRepDelegationAccountStateL == Just (DRepCredential unregCred)
        then as & dRepDelegationAccountStateL .~ Nothing
        else as))
    accountsMap
    delegs
```

This mirrors the correct behavior already implemented in `unDelegReDelegDRep`.

---

### Proof of Concept

**Setup (bootstrap phase, `pvMajor pv == 9`):**

1. User registers `cred` and delegates to DRep A: `RegDepositDelegTxCert cred (DelegVote (DRepCredential drepCredA)) deposit`
   - DRep A's `drepDelegs` = `{cred}`; account: `dRepDelegationAccountStateL = Just drepCredA`

2. User redelegates to DRep B: `DelegTxCert cred (DelegVote (DRepCredential drepCredB))`
   - DRep A's `drepDelegs` = `{cred}` **(stale — not cleaned up)**
   - DRep B's `drepDelegs` = `{cred}`
   - Account: `dRepDelegationAccountStateL = Just drepCredB` ✓

**Attack:**

3. Attacker submits `UnRegDRepTxCert drepCredA deposit`
   - `clearDRepDelegations {cred} accountsMap` fires
   - Account: `dRepDelegationAccountStateL = Nothing` ✗ (incorrectly cleared)

**Consequence:**

4. Next epoch, `computeDRepDistr` iterates accounts. For `cred`: `dRepDelegationAccountStateL = Nothing` → stake is not added to any DRep's distribution.
5. DRep B's effective voting power is reduced by `cred`'s stake, silently corrupting the governance ratification ratio for any active proposal.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L363-365)
```haskell
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L369-370)
```haskell
                | Just accountState <- mAccountState ->
                    certVStateL %~ unDelegReDelegDRep stakeCred accountState (Just dRep)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L137-137)
```haskell
          vsDRepsL %~ addNewDelegation . Map.adjust (drepDelegsL %~ Set.delete stakeCred) dRepCred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L246-254)
```haskell
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

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/DelegSpec.hs (L313-323)
```haskell
      impAnn "Check that unregistration of previous delegation does not affect current delegation" $ do
        unRegisterDRep drepCred
        -- we need to preserve the buggy behavior until the boostrap phase is over.
        ifBootstrap
          ( do
              -- we cannot `expectNotDelegatedVote` because the delegation is still in the DRepState of the other drep
              accounts <- getsNES $ nesEsL . esLStateL . lsCertStateL . certDStateL . accountsL
              expectNothingExpr (lookupDRepDelegation cred accounts)
              expecteReverseDRepDelegation cred drepCred2 True
          )
          (expectDelegatedVote cred (DRepCredential drepCred2))
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L228-232)
```haskell
    addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
      dRep <- accountState ^. dRepDelegationAccountStateL
      let
        balance = accountState ^. balanceAccountStateL
        updatedDistr = Map.insertWith (<>) dRep (stakeAndDeposits <> balance) distr
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
