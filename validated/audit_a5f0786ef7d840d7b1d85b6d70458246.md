### Title
Stale Reverse DRep Delegation Causes Incorrect Forward-Delegation Erasure on DRep Unregistration — (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version major < 10), when a stake credential re-delegates from DRep A to DRep B, the reverse-delegation entry in DRep A's `drepDelegs` set is not removed. When DRep A subsequently unregisters via `ConwayUnRegDRep`, the `clearDRepDelegations` helper iterates over the stale `drepDelegs` and unconditionally nullifies the forward delegation of every listed credential — including credentials that have already re-delegated to DRep B. This incorrectly strips those credentials of their active DRep delegation, reducing DRep B's governance voting power without authorization.

---

### Finding Description

**Bidirectional mapping structure**

The Conway era maintains two cross-referencing structures:

- **Forward**: `AccountState.dRepDelegation` (`stakeCred → DRep`) stored per-credential in `DState.dsAccounts`.
- **Reverse**: `DRepState.drepDelegs` (`DRep → Set (Credential Staking)`) stored per-DRep in `VState.vsDReps`.

Both sides must be kept consistent. The analog to the Solidity `vouchers`/`vouchees` index mismatch is exactly this pair.

**Root cause — stale reverse delegation during bootstrap**

`processDelegationInternal` in `Deleg.hs` takes a `preserveIncorrectDelegation :: Bool` flag. When `True` (bootstrap phase, `pvMajor pv < natVersion @10`), the `delegVote` branch only *inserts* the new reverse delegation and never *removes* the old one:

```haskell
| isNothing mAccountState || preserveIncorrectDelegation ->
    certVStateL . vsDRepsL
      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
```

The correct post-bootstrap path calls `unDelegReDelegDRep`, which atomically removes the old entry and inserts the new one. During bootstrap that call is skipped entirely.

After re-delegation, DRep A's `drepDelegs` still contains `stakeCred` even though `stakeCred`'s forward delegation now points to DRep B.

**Exploitation via `ConwayUnRegDRep`**

`GovCert.hs` handles DRep unregistration:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
...
certState'
  & certDStateL . accountsL . accountsMapL
    %~ clearDRepDelegations (drepDelegs dRepState)
```

`clearDRepDelegations` unconditionally sets `dRepDelegationAccountStateL .~ Nothing` for every credential in `drepDelegs`, with no check that the credential's *current* forward delegation still points to the DRep being unregistered. Because DRep A's `drepDelegs` contains the stale `stakeCred`, this call nullifies `stakeCred`'s delegation to DRep B.

**Attack sequence (unprivileged)**

1. Attacker registers DRep A (pays deposit, fully refundable on unregistration).
2. One or more stake credentials delegate to DRep A (legitimately or induced).
3. Those credentials later re-delegate to DRep B (bootstrap phase, `pvMajor pv < natVersion @10`). Their forward delegation is updated; DRep A's `drepDelegs` retains the stale entries.
4. Attacker submits `ConwayUnRegDRep` for DRep A, recovering the deposit.
5. `clearDRepDelegations` iterates over the stale `drepDelegs` and sets `dRepDelegationAccountStateL .~ Nothing` for each affected credential.
6. Those credentials now have no DRep delegation. DRep B's voting stake is reduced by their combined stake.

The attacker's net cost is zero (deposit is refunded). The operation is a standard, unprivileged certificate transaction.

---

### Impact Explanation

`computeDRepDistr` (used by the DRep pulser for ratification) reads the forward delegation from each `AccountState`:

```haskell
addToDRepDistr accountState stakeAndDeposits distr = fromMaybe distr $ do
  dRep <- accountState ^. dRepDelegationAccountStateL
  ...
```

After the attack, affected credentials return `Nothing` from `dRepDelegationAccountStateL`, so their stake is excluded from every DRep's distribution. DRep B's effective voting power is reduced by the sum of those credentials' stake. This can shift governance ratification outcomes — causing actions that should pass to fail, or (if the attacker also controls DRep B's competitor) causing actions that should fail to pass.

This maps to the allowed impact: **Medium — attacker-controlled certificates modify governance voting power outside design parameters**, with potential escalation to **Critical — unauthorized governance action enacted** if the stake shift crosses a ratification threshold.

---

### Likelihood Explanation

- The bootstrap phase is active for all Conway nodes running protocol version major < 10.
- Any user can register a DRep for the cost of `ppDRepDeposit` (fully refundable).
- The attacker does not need to control any existing delegators; they only need to wait for *any* delegator to re-delegate away from their DRep, which is a normal user action.
- The attack is a single `ConwayUnRegDRep` certificate, indistinguishable from a legitimate unregistration.
- The hard fork to version 10 runs `updateDRepDelegations` to repair stale entries, but until that hard fork the ledger state is vulnerable.

---

### Recommendation

In `clearDRepDelegations` (or its call site in `ConwayUnRegDRep`), verify that each credential's current forward delegation still points to the DRep being unregistered before nullifying it:

```haskell
clearDRepDelegations drepCred delegs accountsMap =
  foldr
    ( \cred acc ->
        Map.adjust
          ( \as ->
              if as ^. dRepDelegationAccountStateL == Just (DRepCredential drepCred)
                then as & dRepDelegationAccountStateL .~ Nothing
                else as
          )
          cred
          acc
    )
    accountsMap
    delegs
```

This mirrors the guard already present in `unDelegReDelegDRep` (`if Just dRep == mNewDRep then id else ...`) and closes the inconsistency window without waiting for the version-10 hard fork.

---

### Proof of Concept

**State before attack (bootstrap phase, `pvMajor pv = 9`)**

| Structure | Contents |
|---|---|
| `accountState(stakeCred).dRepDelegation` | `Just (DRepCredential drepB)` |
| `DRepState(drepA).drepDelegs` | `{stakeCred}` ← **stale** |
| `DRepState(drepB).drepDelegs` | `{stakeCred}` |

**Transactions submitted by attacker**

```
Tx 1: ConwayUnRegDRep drepA refund
```

**`clearDRepDelegations` execution**

```
delegs = drepDelegs(drepA) = {stakeCred}
Map.adjust (dRepDelegationAccountStateL .~ Nothing) stakeCred accountsMap
```

**State after attack**

| Structure | Contents |
|---|---|
| `accountState(stakeCred).dRepDelegation` | `Nothing` ← **incorrectly cleared** |
| `DRepState(drepB).drepDelegs` | `{stakeCred}` ← **orphaned** |

`computeDRepDistr` now skips `stakeCred` entirely. DRep B's voting stake is reduced by `stakeCred`'s stake + rewards + proposal deposits. If this stake was pivotal for a pending governance action, the ratification outcome changes.

---

**Key file references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L320-377)
```haskell
processDelegationInternal ::
  ConwayEraCertState era =>
  -- | Preserve the buggy behavior where DRep delegations are not updated correctly (See #4772)
  Bool ->
  -- | Delegator
  Credential Staking ->
  -- | Account state for the above stake credential
  Maybe (AccountState era) ->
  -- | New delegatee
  Delegatee ->
  CertState era ->
  CertState era
processDelegationInternal preserveIncorrectDelegation stakeCred mAccountState newDelegatee =
  case newDelegatee of
    DelegStake sPool -> delegStake sPool
    DelegVote dRep -> delegVote dRep
    DelegStakeVote sPool dRep -> delegVote dRep . delegStake sPool
  where
    delegStake stakePool cState =
      cState
        & certDStateL . accountsL
          %~ adjustAccountState (stakePoolDelegationAccountStateL ?~ stakePool) stakeCred
        & maybe
          (certPStateL . psStakePoolsL %~ Map.adjust (spsDelegatorsL %~ Set.insert stakeCred) stakePool)
          (\accountState -> certPStateL %~ unDelegReDelegStakePool stakeCred accountState (Just stakePool))
          mAccountState

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L117-143)
```haskell
-- | Reverses DRep delegation.
-- To be called when a stake credential is unregistered or its delegation target changes.
-- If the new delegation matches the previous one, this is a noop.
unDelegReDelegDRep ::
  ConwayEraAccounts era =>
  Credential Staking ->
  -- | Account that is losing its current delegation and/or acquiring a new one
  AccountState era ->
  -- | Potential new delegation. In case when stake credential unregisters this must be `Nothing`.
  Maybe DRep ->
  VState era ->
  VState era
unDelegReDelegDRep stakeCred accountState mNewDRep =
  fromMaybe (vsDRepsL %~ addNewDelegation) $ do
    dRep@(DRepCredential dRepCred) <- accountState ^. dRepDelegationAccountStateL
    pure $
      -- There is no need to update set of delegations if delegation is unchanged
      if Just dRep == mNewDRep
        then id
        else
          vsDRepsL %~ addNewDelegation . Map.adjust (drepDelegsL %~ Set.delete stakeCred) dRepCred
  where
    addNewDelegation =
      case mNewDRep of
        Just (DRepCredential dRepCred) ->
          Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
        _ -> id
```
