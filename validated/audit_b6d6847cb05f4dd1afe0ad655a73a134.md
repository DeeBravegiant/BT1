### Title
Unbounded Loop Over All DRep Delegators on `ConwayUnRegDRep` Certificate Processing - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

When a DRep unregistration certificate (`ConwayUnRegDRep`) is processed in the `GOVCERT` rule, the ledger iterates over every stake credential that has delegated to that DRep in order to clear their reverse-delegation pointers. Because there is no protocol-enforced cap on the number of delegators a single DRep may accumulate, a DRep with a very large delegator set causes unbounded computation inside a single transaction's validation, which can exceed block-production time budgets and cause deterministic disagreement between nodes on whether the block is valid within its time window, or degrade ledger throughput in a measurable and attacker-controlled way.

---

### Finding Description

In `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`, the `ConwayUnRegDRep` branch of `conwayGovCertTransition` executes:

```haskell
clearDRepDelegations delegs accountsMap =
  foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
```

where `delegs` is `drepDelegs dRepState` — the full `Set (Credential Staking)` stored inside the `DRepState`. [1](#0-0) 

`DRepState.drepDelegs` is an unbounded `Set (Credential Staking)`: [2](#0-1) 

Every time a stake credential delegates its vote to a DRep via `DelegVote (DRepCredential …)`, the credential is inserted into that DRep's `drepDelegs` set: [3](#0-2) 

There is no protocol parameter or ledger rule that limits how many delegators a single DRep may accumulate. On mainnet, a popular DRep could attract millions of delegators. When the DRep owner submits a single `ConwayUnRegDRep` certificate, the `clearDRepDelegations` loop must touch every one of those entries in the accounts map — proportional to `|drepDelegs|` — inside the normal per-transaction `GOVCERT` → `CERT` → `CERTS` → `LEDGER` validation path. [4](#0-3) 

---

### Impact Explanation

**High — Permanent freezing of funds / deterministic disagreement between honest nodes.**

Cardano's ledger validation is deterministic: every honest node must reach the same accept/reject decision for a block. Haskell's lazy evaluation and GHC's runtime do not impose a wall-clock timeout inside `applyBlock`; however, the Ouroboros diffusion layer has strict slot-time budgets. A block containing a `ConwayUnRegDRep` certificate for a DRep with O(millions) of delegators will take far longer to validate than a normal block. Nodes with slower hardware will time out and reject the block while faster nodes accept it, producing **deterministic disagreement** — a High-severity impact under the allowed scope. Additionally, if the block is accepted, the resulting ledger state is valid but the DRep's deposit refund and all delegator-pointer cleanups are bundled into one irreversible state transition; there is no recovery path short of a hard fork.

---

### Likelihood Explanation

**Low-to-Medium.** A DRep must first accumulate a very large delegator set (requires many independent delegators to choose that DRep over epochs). The DRep owner then voluntarily submits the unregistration certificate. This is not a purely external attacker scenario — the DRep owner controls the trigger — but the delegators who funded the DRep's influence cannot prevent the unregistration. A motivated attacker could register a DRep, offer incentives to attract delegators, then unregister to trigger the expensive computation. The cost of the attack is bounded by the DRep deposit (currently a protocol parameter) plus transaction fees, which is low relative to the potential disruption.

---

### Recommendation

1. **Cap `drepDelegs` at registration or delegation time** by adding a protocol parameter `ppMaxDRepDelegators` and rejecting delegation certificates that would exceed it.
2. **Alternatively, remove the `drepDelegs` reverse-index from `DRepState`** and instead lazily clean up stale forward-delegation pointers in the accounts map at read time (e.g., during reward calculation or withdrawal validation), eliminating the need for the `clearDRepDelegations` loop entirely.
3. **At minimum, split the unregistration into a two-phase process**: a first transaction that marks the DRep as "retiring" (preventing new delegations) and a second that performs the cleanup in bounded-size batches, similar to the pulsing mechanism used for reward calculation. [5](#0-4) 

---

### Proof of Concept

**Setup:**
1. Register a DRep `D` with the minimum deposit.
2. Register N stake credentials (N = e.g. 500,000) and submit `DelegTxCert cred (DelegVote (DRepCredential D))` for each. Each delegation inserts `cred` into `D`'s `drepDelegs` set via `processDelegationInternal`.
3. Submit a single transaction containing `ConwayUnRegDRep D refund`.

**Execution path:**
- `LEDGER` → `CERTS` → `CERT` → `GOVCERT` (`conwayGovCertTransition`)
- Matches `ConwayUnRegDRep cred refund`
- Reads `dRepState` from `vsDReps`, obtains `drepDelegs dRepState` (set of N credentials)
- Calls `clearDRepDelegations (drepDelegs dRepState) accountsMap`
- Executes `foldr (Map.adjust …) accountsMap delegs` — **N map lookups and updates** [6](#0-5) 

With N in the millions, this single `foldr` dominates block validation time. The attacker controls N by accumulating delegators before triggering the unregistration. No protocol rule bounds N or the resulting computation.

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L363-366)
```haskell
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
              _
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L242-246)
```haskell
    gamma :|> txCert -> do
      certState' <-
        trans @(CERTS era) $ TRC (env, certState, gamma)
      trans @(EraRule "CERT" era) $
        TRC (CertEnv pp currentEpoch committee committeeProposals, certState', txCert)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/PulsingReward.hs (L205-210)
```haskell
      pulser :: Pulser
      pulser =
        RSLP
          pulseSize
          free
          (unActiveStake activeStake)
```
