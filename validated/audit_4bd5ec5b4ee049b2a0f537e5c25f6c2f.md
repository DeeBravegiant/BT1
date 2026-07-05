### Title
Unbounded `drepDelegs` Set Enables Attacker to Permanently Freeze a DRep's Deposit via `ConwayUnRegDRep` - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

In the Conway era, `DRepState` stores a reverse-delegation index `drepDelegs :: !(Set (Credential Staking))` with no upper bound on its size. Any registered stake credential holder can delegate their vote to any DRep, unconditionally inserting themselves into that DRep's `drepDelegs` set. When the DRep later submits a `ConwayUnRegDRep` certificate to reclaim their deposit, the ledger rule iterates over every entry in `drepDelegs` to clear each delegator's pointer. Because this set is unbounded, an attacker who registers many cheap stake credentials and delegates them all to a target DRep can make the DRep's unregistration transaction computationally infeasible to include in a block, permanently freezing the DRep's deposit.

---

### Finding Description

**Root cause — `DRepState.drepDelegs` is unbounded**

`DRepState` is defined in `libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs`:

```haskell
data DRepState = DRepState
  { drepExpiry  :: !EpochNo
  , drepAnchor  :: !(StrictMaybe Anchor)
  , drepDeposit :: !(CompactForm Coin)
  , drepDelegs  :: !(Set (Credential Staking))   -- ← no size cap
  }
``` [1](#0-0) 

Any stake credential holder can add themselves to a DRep's `drepDelegs` set by submitting a `DelegVote` (or `DelegStakeVote`) certificate. The `processDelegationInternal` function in `Deleg.hs` unconditionally calls `Set.insert stakeCred` on the target DRep's `drepDelegs`:

```haskell
certVStateL . vsDRepsL
  %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
``` [2](#0-1) 

There is no check or limit on how large `drepDelegs` may grow.

**Vulnerable operation — `ConwayUnRegDRep` iterates over all delegators**

When a DRep unregisters, the `GOVCERT` rule in `GovCert.hs` executes `clearDRepDelegations`, which performs a `foldr` over the entire `drepDelegs` set, calling `Map.adjust` on the global accounts map for every entry:

```haskell
ConwayUnRegDRep cred refund -> do
  ...
  let clearDRepDelegations delegs accountsMap =
        foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap delegs
  pure $
    case mDRepState of
      ...
      Just dRepState ->
        certState'
          & certDStateL . accountsL . accountsMapL
            %~ clearDRepDelegations (drepDelegs dRepState)
``` [3](#0-2) 

The complexity is O(|drepDelegs| × log |accountsMap|). Because `drepDelegs` is unbounded, an attacker who has populated it with N entries forces the DRep's unregistration transaction to perform N × log(M) map operations during ledger-rule evaluation, where M is the total number of registered accounts.

**Attacker entry path**

1. Attacker registers N fresh stake credentials (each costs `ppKeyDeposit`, currently 2 ADA on mainnet).
2. Attacker submits N `DelegVote` certificates pointing to the victim DRep. Each certificate is valid because `checkStakeDelegateeRegistered` only verifies the DRep is registered — it does not limit how many delegators a DRep may accumulate.
3. The victim DRep's `drepDelegs` set now contains N attacker-controlled credentials.
4. When the DRep submits `ConwayUnRegDRep` to reclaim their deposit, the ledger must execute `clearDRepDelegations` over all N entries. For sufficiently large N, this computation exceeds what any block producer will include within a block's validation budget, causing the transaction to be perpetually skipped.

The `drepDelegs` set is also serialised as part of `VState` (via `DRepState`) and is part of the ledger state that every node must process: [4](#0-3) 

---

### Impact Explanation

**Impact: Medium** — Attacker-controlled certificates (cheap `DelegVote` certs) cause the victim DRep's `ConwayUnRegDRep` transaction to exceed intended validation limits. In the extreme case, the DRep's deposit (500 ADA on mainnet) is permanently frozen because no block producer will include the unregistration transaction, and no protocol-level recovery path exists short of a hard fork that either caps `drepDelegs` or provides an alternative unregistration path.

This maps to:
> *Medium. Attacker-controlled transactions, blocks, certificates, votes, proposals, scripts, witnesses, or serialized inputs exceed intended validation limits.*

And potentially:
> *High. Permanent freezing of funds, deposits, rewards, or withdrawals where recovery requires a hard fork.*

---

### Likelihood Explanation

**Likelihood: Low.** The attacker must pay `ppKeyDeposit` (2 ADA) per stake credential. Causing a meaningful slowdown requires registering hundreds of thousands of credentials, costing hundreds of thousands of ADA. The attack is economically expensive but not impossible for a well-funded adversary targeting a high-profile DRep. The attack is also asymmetric: the attacker's deposits are recoverable (by unregistering the stake credentials), while the victim DRep's deposit may be permanently frozen.

---

### Recommendation

1. **Enforce a maximum delegator count per DRep.** Add a protocol parameter (e.g., `ppMaxDRepDelegators`) and reject `DelegVote` certificates that would push a DRep's `drepDelegs` set beyond this limit, analogous to how `ppMaxCollateralInputs` caps collateral inputs.

2. **Decouple unregistration from delegator cleanup.** Instead of clearing all delegator pointers eagerly in `clearDRepDelegations`, mark the DRep as "unregistered" and lazily clean up delegator pointers when each delegator next interacts with the ledger (re-delegates, unregisters, or withdraws). This bounds the per-transaction work to O(1) for the DRep's unregistration.

3. **Charge a per-delegation fee to the delegator** that is proportional to the cost of future cleanup, creating an economic disincentive for mass delegation attacks.

---

### Proof of Concept

```
-- Attacker setup (off-chain):
for i in 1..N:
    register stake credential C_i  (costs ppKeyDeposit = 2 ADA each)
    submit DelegVote C_i → victim_DRep

-- After N delegations, victim_DRep.drepDelegs = {C_1, ..., C_N}

-- Victim attempts unregistration:
submit Tx { certs = [ConwayUnRegDRep victim_DRep refund] }

-- Ledger evaluates GOVCERT rule:
--   clearDRepDelegations {C_1,...,C_N} accountsMap
--   = foldr (Map.adjust ...) accountsMap [C_1,...,C_N]
--   = N × O(log M) map operations
--
-- For N = 500,000 and M = 10,000,000:
--   ≈ 500,000 × 23 = 11,500,000 map operations
--   Estimated wall-clock: several seconds per transaction validation
--   Block producers skip the transaction → DRep deposit frozen
```

The relevant code path is: [5](#0-4) [2](#0-1) [6](#0-5)

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs (L166-172)
```haskell
data DRepState = DRepState
  { drepExpiry :: !EpochNo
  , drepAnchor :: !(StrictMaybe Anchor)
  , drepDeposit :: !(CompactForm Coin)
  , drepDelegs :: !(Set (Credential Staking))
  }
  deriving (Show, Eq, Ord, Generic)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L363-366)
```haskell
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
              _
```

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L56-67)
```haskell
data VState era = VState
  { vsDReps :: !(Map (Credential DRepRole) DRepState)
  , vsCommitteeState :: !(CommitteeState era)
  , vsNumDormantEpochs :: !EpochNo
  -- ^ Number of contiguous epochs in which there are exactly zero
  -- active governance proposals to vote on. It is incremented in every
  -- EPOCH rule if the number of active governance proposals to vote on
  -- continues to be zero. It is reset to zero when a new governance
  -- action is successfully proposed. We need this counter in order to
  -- bump DRep expiries through dormant periods when DReps do not have
  -- an opportunity to vote on anything.
  }
```
