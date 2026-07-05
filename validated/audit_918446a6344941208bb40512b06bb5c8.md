### Title
Unbounded `clearDRepDelegations` Iteration in `ConwayUnRegDRep` Allows Attacker to Permanently Freeze a DRep's Deposit - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

When a DRep unregisters via `ConwayUnRegDRep`, the GOVCERT rule calls `clearDRepDelegations`, which iterates over every stake credential in `drepDelegs dRepState` to clear their delegation pointers. Because any stake credential holder can unilaterally delegate to any DRep without the DRep's consent, an attacker can register many stake credentials and delegate them all to a victim DRep. This makes the victim's unregistration certificate computationally infeasible to include in a block within the 1-second Cardano slot time, effectively permanently freezing the DRep's deposit.

---

### Finding Description

**Root cause — unbounded linear scan over attacker-controlled set**

In `conwayGovCertTransition`, the `ConwayUnRegDRep` branch (lines 234–254) defines:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
```

where `delegs = drepDelegs dRepState`. [1](#0-0) 

`drepDelegs` is typed as `Set (Credential Staking)` with no upper bound. [2](#0-1) 

**How the set grows — attacker-controlled delegation**

The set is populated by `unDelegReDelegDRep` in `VState.hs`, which calls `Set.insert stakeCred` on the target DRep's `drepDelegs` whenever any stake credential delegates to that DRep. [3](#0-2) 

Delegation is unilateral: the DRep cannot refuse. Any unprivileged actor who registers a stake credential (paying `keyDeposit`, currently 2 ADA on mainnet) can delegate it to any DRep via `ConwayDelegTxCert` or `ConwayRegDepositDelegTxCert`.

**Exploit path**

1. Attacker registers N stake credentials (cost: `keyDeposit × N`).
2. Attacker delegates all N credentials to the victim DRep.
3. Victim's `drepDelegs` set now contains N entries.
4. Victim submits a `ConwayUnRegDRep` certificate.
5. `clearDRepDelegations` performs N `Map.adjust` calls on the global accounts map — O(N × log M) where M = total registered accounts.
6. For large N, this computation exceeds the 1-second Cardano slot time; no block producer can include the transaction without missing their slot.
7. The victim's DRep deposit is permanently locked.

**No bound exists**

There is no protocol-level cap on `drepDelegs` size, no per-certificate computation budget, and no fee mechanism that compensates block producers for the extra computation. Transaction fees are based on byte size, not CPU work; the `ConwayUnRegDRep` certificate is a fixed small size regardless of how many delegators must be cleared. [4](#0-3) 

---

### Impact Explanation

**High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.**

The DRep's deposit (governed by `ppDRepDeposit`, currently 500 ADA on mainnet) is permanently frozen because:

- The unregistration transaction is ledger-valid but computationally infeasible to include in a block once `drepDelegs` is large enough.
- The attacker controls the delegations and can maintain them indefinitely (their own `keyDeposit` is refundable on unregistration, but they simply never unregister).
- The victim DRep has no protocol mechanism to force delegators to undelegate.
- Recovery requires either the attacker to voluntarily undelegate, or a protocol upgrade (hard fork) that caps `drepDelegs` size or changes the unregistration logic.

---

### Likelihood Explanation

Medium. The attacker must lock up `keyDeposit × N` ADA to sustain the attack. To push computation past the 1-second slot boundary requires on the order of 1 million delegators (~2 million ADA locked). This is a significant but not impossible cost for a well-funded adversary targeting a high-value DRep (e.g., one representing a large governance bloc). The attack is economically rational if the attacker benefits from blocking the DRep's exit from governance (e.g., preventing a competing DRep from withdrawing and re-registering with a different key). The attacker's capital is not destroyed — it is only locked for the duration of the attack.

---

### Recommendation

Remove the `drepDelegs` field from `DRepState` and stop eagerly clearing delegation pointers on unregistration. Instead, use a lazy approach: mark the DRep as unregistered in `vsDReps` and let each delegator's next interaction (re-delegation, unregistration, or reward withdrawal) detect the stale pointer and clear it at that time. This eliminates the O(N) scan entirely. Alternatively, enforce a hard cap on `drepDelegs` size via a new protocol parameter (e.g., `maxDelegatorsPerDRep`), rejecting delegation certificates that would exceed it. [2](#0-1) 

---

### Proof of Concept

```
1. Register victim DRep V with ConwayRegDRep (deposit: 500 ADA).
2. Attacker registers 1,000,000 stake credentials S_1 … S_N
   (each paying keyDeposit = 2 ADA; total locked: 2,000,000 ADA).
3. Attacker submits delegation certificates delegating each S_i to V.
   After processing, V.drepDelegs = {S_1, …, S_1000000}.
4. Victim submits a transaction containing ConwayUnRegDRep V.
5. GOVCERT rule invokes clearDRepDelegations {S_1,…,S_1000000} accountsMap.
6. This performs 1,000,000 Map.adjust calls on the global accounts map
   (O(1M × log(total_accounts)) ≈ O(20M) operations in Haskell).
7. Computation time exceeds the 1-second Cardano slot time.
8. No block producer can include the transaction without missing their slot.
9. Victim's 500 ADA DRep deposit is permanently frozen.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L234-254)
```haskell
    ConwayUnRegDRep cred refund -> do
      let mDRepState = Map.lookup cred (certState ^. certVStateL . vsDRepsL)
          drepRefundMismatch = do
            drepState <- mDRepState
            let paidDeposit = drepState ^. drepDepositL
            guard (refund /= paidDeposit)
            pure paidDeposit
      isJust mDRepState ?! (injectFailure . ConwayDRepNotRegistered) cred
      failOnJust drepRefundMismatch $ injectFailure . ConwayDRepIncorrectRefund . Mismatch refund
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
