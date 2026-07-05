### Title
Stale `drepDelegs` in Bootstrap Phase Allows Attacker to Silently Wipe Staker Vote Delegations via DRep Unregistration — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version 9), when a staker re-delegates from DRep A to DRep B, the old DRep A's `drepDelegs` reverse-delegation set is intentionally not cleaned up (`preserveIncorrectDelegation = True`). The `ConwayUnRegDRep` handler in `GovCert.hs` unconditionally clears `dRepDelegationAccountStateL` for every staker credential in `drepDelegs` when a DRep unregisters — without verifying that those stakers' *current* delegation still points to the unregistering DRep. An attacker who controls a DRep can exploit this to silently zero out the vote delegation of any staker who previously delegated to the attacker's DRep and has since re-delegated to a legitimate DRep, reducing that legitimate DRep's effective governance voting power.

---

### Finding Description

**Root cause — two interacting code paths:**

**Path 1 — stale `drepDelegs` accumulation (bootstrap phase).**
In `processDelegationInternal`, the `preserveIncorrectDelegation` flag is set to `pvMajor pv < natVersion @10` for `ConwayDelegCert` and `ConwayRegDelegCert`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs:287-291
processDelegationInternal
  (pvMajor pv < natVersion @10)   -- True during bootstrap (PV9)
  internedCred
  (Just accountState)
  delegatee
  certState
``` [1](#0-0) 

When `preserveIncorrectDelegation` is `True` and the new delegatee is a credential DRep, the `delegVote` branch only *adds* the staker to the new DRep's `drepDelegs` — it never removes the staker from the old DRep's `drepDelegs`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs:363-365
| isNothing mAccountState || preserveIncorrectDelegation ->
    certVStateL . vsDRepsL
      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
``` [2](#0-1) 

After a staker re-delegates from DRep A → DRep B during bootstrap:
- `accountState.dRepDelegationAccountStateL = Just (DRepCredential drepB)` ✓ (correct)
- `drepA.drepDelegs` still contains the staker ✗ (stale)
- `drepB.drepDelegs` also contains the staker ✓

**Path 2 — `ConwayUnRegDRep` blindly trusts `drepDelegs`.**
The unregistration handler iterates over `drepDelegs` and unconditionally sets `dRepDelegationAccountStateL = Nothing` for every staker credential listed there, with no check that the staker's *current* delegation still points to the DRep being removed:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs:246-254
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
``` [3](#0-2) 

When DRep A unregisters, `clearDRepDelegations` walks the stale `drepDelegs` set and sets `dRepDelegationAccountStateL = Nothing` for the staker — even though the staker has already re-delegated to DRep B. The staker's delegation to DRep B is silently wiped.

**Voting power consequence.**
`computeDRepDistr` derives the DRep stake distribution exclusively from `dRepDelegationAccountStateL`:

```haskell
-- eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs:228-229
addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
  dRep <- accountState ^. dRepDelegationAccountStateL
``` [4](#0-3) 

Once `dRepDelegationAccountStateL` is `Nothing`, the staker's stake is excluded from every DRep's distribution. DRep B loses that stake from its effective voting weight.

The existing test suite explicitly confirms this outcome during bootstrap:

```haskell
-- eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/DelegSpec.hs:316-322
ifBootstrap
  ( do
      accounts <- getsNES $ nesEsL . esLStateL . lsCertStateL . certDStateL . accountsL
      expectNothingExpr (lookupDRepDelegation cred accounts)  -- delegation wiped
      expecteReverseDRepDelegation cred drepCred2 True        -- DRep2 still lists staker
  )
``` [5](#0-4) 

---

### Impact Explanation

An attacker who controls a DRep can reduce the effective governance voting power of any legitimate DRep whose delegators previously passed through the attacker's DRep. By timing the unregistration of their DRep after those stakers have re-delegated, the attacker silently removes those stakers' stake from the governance distribution. This can:

- Prevent a legitimate governance action (parameter change, committee election, hard-fork, treasury withdrawal) from reaching its ratification threshold.
- Allow an otherwise-failing governance action to pass by suppressing the voting weight of opposing DReps.

This matches the **Critical** allowed impact: *"Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted."*

---

### Likelihood Explanation

The attack is executable by any unprivileged transaction sender during the Conway bootstrap phase (protocol version 9):

1. Register a DRep (one transaction, pays `ppDRepDeposit`).
2. Attract delegations — in bootstrap, stakers may delegate to any DRep including unregistered ones; the attacker can offer off-chain incentives or simply wait for organic delegations.
3. After delegators re-delegate to a target DRep (a natural event as governance matures), submit `ConwayUnRegDRep` to reclaim the deposit and trigger the wipe.

No privileged access, leaked keys, or supermajority is required. The deposit is fully refunded on unregistration, making the attack economically free beyond transaction fees. The bootstrap phase is a live, multi-epoch window on mainnet.

---

### Recommendation

In `ConwayUnRegDRep` (`GovCert.hs`), `clearDRepDelegations` must verify that each staker's *current* `dRepDelegationAccountStateL` still points to the DRep being unregistered before clearing it:

```haskell
clearDRepDelegations drepCred delegs accountsMap =
  foldr
    ( Map.adjust $ \as ->
        if as ^. dRepDelegationAccountStateL == Just (DRepCredential drepCred)
          then as & dRepDelegationAccountStateL .~ Nothing
          else as
    )
    accountsMap
    delegs
```

This mirrors the fix applied in `unDelegReDelegDRep` (`VState.hs`), which already guards against no-op updates when the delegation is unchanged: [6](#0-5) 

The same guard should be applied consistently in the unregistration path. The `updateDRepDelegations` hardfork transition at PV10 rebuilds `drepDelegs` from scratch and would mask the inconsistency post-bootstrap, but the window during PV9 remains exploitable.

---

### Proof of Concept

**Precondition**: Network is in Conway bootstrap phase (`pvMajor pv == 9`).

1. **Attacker** submits `ConwayRegDRep attackerDRep deposit anchor` → DRep A registered, `drepDelegs = {}`.
2. **Victim staker S** submits `RegDepositDelegTxCert credS (DelegVote (DRepCredential attackerDRep)) deposit` → `credS.dRepDelegationAccountStateL = Just (DRepCredential attackerDRep)`, `drepA.drepDelegs = {credS}`.
3. **Victim staker S** submits `DelegTxCert credS (DelegVote (DRepCredential legitimateDRep))` → `credS.dRepDelegationAccountStateL = Just (DRepCredential legitimateDRep)`, but because `preserveIncorrectDelegation = True`, `drepA.drepDelegs` is **not** updated and still contains `credS`. `drepB.drepDelegs = {credS}`.
4. **Attacker** submits `ConwayUnRegDRep attackerDRep deposit` → `clearDRepDelegations {credS} accountsMap` sets `credS.dRepDelegationAccountStateL = Nothing`.
5. **Result**: `credS`'s stake is excluded from `computeDRepDistr` for `legitimateDRep`. The legitimate DRep's ratification ratio is reduced by `credS`'s full stake weight. Steps 1–4 can be repeated with fresh DRep credentials to wipe additional delegators.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L287-292)
```haskell
            processDelegationInternal
              (pvMajor pv < natVersion @10)
              internedCred
              (Just accountState)
              delegatee
              certState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L363-365)
```haskell
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L243-254)
```haskell
      let
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L228-229)
```haskell
    addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
      dRep <- accountState ^. dRepDelegationAccountStateL
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/DelegSpec.hs (L316-322)
```haskell
        ifBootstrap
          ( do
              -- we cannot `expectNotDelegatedVote` because the delegation is still in the DRepState of the other drep
              accounts <- getsNES $ nesEsL . esLStateL . lsCertStateL . certDStateL . accountsL
              expectNothingExpr (lookupDRepDelegation cred accounts)
              expecteReverseDRepDelegation cred drepCred2 True
          )
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L129-137)
```haskell
unDelegReDelegDRep stakeCred accountState mNewDRep =
  fromMaybe (vsDRepsL %~ addNewDelegation) $ do
    dRep@(DRepCredential dRepCred) <- accountState ^. dRepDelegationAccountStateL
    pure $
      -- There is no need to update set of delegations if delegation is unchanged
      if Just dRep == mNewDRep
        then id
        else
          vsDRepsL %~ addNewDelegation . Map.adjust (drepDelegsL %~ Set.delete stakeCred) dRepCred
```
