### Title
Stale Reverse DRep Delegation Causes `ConwayUnRegDRep` to Silently Wipe Valid Delegations During Bootstrap Phase - (`File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

During the Conway bootstrap phase (protocol version 9), when a stake credential redelegates from DRep A to DRep B, DRep A's `drepDelegs` reverse-delegation set is **not** cleaned up (known bug #4772, intentionally preserved for backward compatibility). When DRep A subsequently unregisters via `ConwayUnRegDRep`, the `clearDRepDelegations` helper in `GovCert.hs` unconditionally clears `dRepDelegationAccountStateL` for every credential in the stale `drepDelegs` set — including credentials that have already validly redelegated to DRep B. This silently nullifies those credentials' vote delegations, removing their stake from DRep B's governance vote weight without their consent or knowledge.

---

### Finding Description

**Root cause — stale reverse delegation created during bootstrap:**

In `processDelegationInternal`, when `preserveIncorrectDelegation` is `True` (i.e., `pvMajor pv < natVersion @10`), a redelegation from DRep A to DRep B only *adds* the stake credential to DRep B's `drepDelegs` set; it never *removes* it from DRep A's `drepDelegs` set: [1](#0-0) 

The condition that triggers the buggy path: [2](#0-1) 

**Exploitation path — `ConwayUnRegDRep` uses the stale set:**

When DRep A unregisters, `clearDRepDelegations` iterates over `drepDelegs dRepState` and unconditionally sets `dRepDelegationAccountStateL` to `Nothing` for every credential in that set: [3](#0-2) 

There is no guard checking whether the credential's *current* forward delegation still points to the unregistering DRep. A credential that redelegated to DRep B will have its `dRepDelegationAccountStateL` cleared to `Nothing` by DRep A's unregistration.

**Effect on governance ratification:**

`computeDRepDistr` builds the DRep stake distribution used by `dRepAcceptedRatio` by reading `accountState ^. dRepDelegationAccountStateL`. Once that field is `Nothing`, the credential's stake is excluded from every DRep's distribution: [4](#0-3) [5](#0-4) 

The `DRepState.drepDelegs` field that drives the unregistration cleanup: [6](#0-5) 

---

### Impact Explanation

A malicious DRep (DRep A) can:
1. Register as a DRep and attract delegators.
2. Wait for some delegators to redelegate to a competing DRep B (e.g., one that is about to push a governance action over the ratification threshold).
3. Submit a `ConwayUnRegDRep` certificate to unregister.
4. The ledger clears `dRepDelegationAccountStateL` for all stale entries in DRep A's `drepDelegs`, silently removing those delegators' stake from DRep B's vote weight.

This modifies governance vote weights outside design parameters. Depending on the margin, it can prevent a legitimate governance action (parameter change, hard-fork initiation, committee update, treasury withdrawal) from reaching its ratification threshold, or allow a borderline action to pass by reducing the effective "No" weight. This matches the **Medium** allowed impact: *attacker-controlled certificates modify governance vote weights outside design parameters*.

---

### Likelihood Explanation

- Exploitable only during the Conway bootstrap phase (`pvMajor == 9`). On mainnet, which has transitioned to PV10, the `updateDRepDelegations` hard-fork cleanup removes stale entries, so the window is closed for mainnet.
- On any testnet or private network still at PV9, or during the historical bootstrap window, the attack is fully reachable with a single unprivileged `ConwayUnRegDRep` transaction signed by the DRep's own key — no privileged access required.
- The attacker only needs to have previously attracted at least one delegator who subsequently redelegated elsewhere.

---

### Recommendation

In `clearDRepDelegations` (or its call site in `ConwayUnRegDRep`), guard the clear operation so it only nullifies `dRepDelegationAccountStateL` when the credential's current forward delegation still points to the unregistering DRep:

```haskell
-- Current (buggy):
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs

-- Fixed:
clearDRepDelegations unregDRepCred delegs accountsMap =
  foldr
    ( Map.adjust $ \as ->
        if as ^. dRepDelegationAccountStateL == Just (DRepCredential unregDRepCred)
          then as & dRepDelegationAccountStateL .~ Nothing
          else as
    )
    accountsMap
    delegs
```

This mirrors the guard already present in `unDelegReDelegDRep`: [7](#0-6) 

---

### Proof of Concept

**Setup (bootstrap phase, `pvMajor == 9`):**

1. Alice registers her stake credential and delegates to DRep A (`RegDepositDelegTxCert` with `DelegVote (DRepCredential drepA)`).
   - `accountState ^. dRepDelegationAccountStateL == Just (DRepCredential drepA)`
   - `drepDelegs (vsDReps ! drepA)` contains Alice.

2. Alice redelegates to DRep B (`DelegTxCert` with `DelegVote (DRepCredential drepB)`).
   - Because `preserveIncorrectDelegation = True` (PV9), `processDelegationInternal` only inserts Alice into DRep B's `drepDelegs`; it does **not** remove Alice from DRep A's `drepDelegs`.
   - `accountState ^. dRepDelegationAccountStateL == Just (DRepCredential drepB)` ✓
   - `drepDelegs (vsDReps ! drepA)` **still** contains Alice (stale). [2](#0-1) 

3. DRep A submits `ConwayUnRegDRep drepA refund`.
   - `clearDRepDelegations (drepDelegs dRepAState) accountsMap` iterates over the stale set, finds Alice, and sets `Alice.dRepDelegationAccountStateL = Nothing`. [3](#0-2) 

4. At the next epoch boundary, `computeDRepDistr` processes Alice's account:
   - `accountState ^. dRepDelegationAccountStateL == Nothing` → Alice's stake is not added to any DRep's distribution. [4](#0-3) 

5. DRep B's `reDRepDistr` entry is reduced by Alice's stake. If DRep B was voting Yes on a governance action near the threshold, the action may now fail to ratify — an outcome that would not have occurred without DRep A's malicious unregistration.

### Citations

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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs (L166-171)
```haskell
data DRepState = DRepState
  { drepExpiry :: !EpochNo
  , drepAnchor :: !(StrictMaybe Anchor)
  , drepDeposit :: !(CompactForm Coin)
  , drepDelegs :: !(Set (Credential Staking))
  }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L129-143)
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
  where
    addNewDelegation =
      case mNewDRep of
        Just (DRepCredential dRepCred) ->
          Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
        _ -> id
```
