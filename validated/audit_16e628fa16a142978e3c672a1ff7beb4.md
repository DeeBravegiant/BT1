### Title
Unbounded `drepDelegs` Iteration in `ConwayUnRegDRep` Allows Attacker-Inflated Delegator Set to Freeze DRep Deposit Refund - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

### Summary
In the `ConwayUnRegDRep` case of `conwayGovCertTransition`, the `clearDRepDelegations` helper iterates over the entire `drepDelegs` set stored in `DRepState` using an unbounded `foldr`. Because any unprivileged user can append their own staking credential to that set by submitting a `DelegVote` or `DelegStakeVote` certificate, an attacker can inflate the set to an arbitrarily large size. When the legitimate DRep owner later submits a `ConwayUnRegDRep` certificate to reclaim their deposit, the ledger rule must traverse the full inflated set in a single synchronous step, making the transaction impossible to include within practical block-validation time budgets and permanently freezing the DRep's deposit.

### Finding Description

**Root cause — unbounded iteration over an attacker-controlled set**

`DRepState` carries a `drepDelegs :: Set (Credential Staking)` field that records every staking credential currently delegated to the DRep. [1](#0-0) 

When a DRep unregisters, `conwayGovCertTransition` calls `clearDRepDelegations`, which performs an O(n · log m) traversal over the entire `drepDelegs` set (n delegators, m total accounts) in one atomic step: [2](#0-1) 

There is no cap, chunk size, or pulsing mechanism applied here — unlike the reward-calculation pulser or the DRep-distribution pulser, this work is done entirely within the single ledger-rule invocation triggered by the transaction.

**Attacker-controlled growth path**

Any unprivileged user can add their own credential to `drepDelegs` by submitting a delegation certificate. In `processDelegationInternal` (called from `conwayDelegTransition`): [3](#0-2) 

The only prerequisite is that the delegating credential is registered and the target DRep exists. No permission from the DRep is required. The same insertion path is taken for `ConwayRegDelegCert` (register-and-delegate in one step): [4](#0-3) 

**Exploit flow**

1. Attacker registers N staking credentials (each costs the `ppKeyDeposit`, currently ~2 ADA).
2. Attacker submits N `DelegVote (DRepCredential targetDRep)` certificates, inserting each credential into `drepDelegs` of the victim DRep.
3. Victim DRep submits `ConwayUnRegDRep` to reclaim their deposit.
4. `clearDRepDelegations (drepDelegs dRepState)` must call `Map.adjust` N times against the full accounts map. For large N this exceeds practical block-validation time budgets, causing the transaction to be un-includable.
5. The attacker can continuously re-delegate (or register fresh credentials) to keep `drepDelegs` large, making the condition permanent.

Unlike the reward calculation or DRep-distribution computation — both of which are explicitly spread over many blocks via a `Pulsable` pulser — `clearDRepDelegations` has no such mechanism: [5](#0-4) [6](#0-5) 

### Impact Explanation

The DRep's deposit (paid at `ConwayRegDRep` time) cannot be refunded because the `ConwayUnRegDRep` transaction cannot be included in any block once `drepDelegs` is sufficiently large. The deposit is effectively frozen for as long as the attacker maintains the inflated delegator set. Recovery would require either a protocol change that adds a bound or a hard fork to forcibly return the deposit — matching the **High** impact category of permanent freezing of deposits.

Additionally, if different node implementations apply different practical time-out thresholds for block validation, the same block containing the `ConwayUnRegDRep` transaction could be accepted by some nodes and rejected by others, producing deterministic ledger divergence.

### Likelihood Explanation

The cost to the attacker is N × `ppKeyDeposit` (≈ 2 ADA each) plus transaction fees. Registering 500,000 credentials costs roughly 1,000,000 ADA (~$400,000 at mid-2024 prices), which is within reach of a motivated adversary targeting a high-value DRep. The attack is permissionless, requires no privileged access, and can be sustained indefinitely by re-delegating. There is no protocol-level countermeasure that limits `drepDelegs` size.

### Recommendation

1. **Bound `drepDelegs` at delegation time**: Introduce a protocol parameter `ppMaxDRepDelegators` and reject `DelegVote` certificates that would push a DRep's delegator count above it.
2. **Lazy / pulsed cleanup**: Instead of clearing all delegations synchronously in `ConwayUnRegDRep`, mark the DRep as "unregistering" and clear delegations lazily (e.g., at the next epoch boundary via the existing pulsing infrastructure), similar to how `clearDRepDelegations` is already handled at the hard-fork migration in `updateDRepDelegations`.
3. **Alternatively, remove the reverse-delegation set**: The `drepDelegs` set is a reverse index maintained for convenience. If it is removed and replaced with a forward-only lookup (credential → DRep), the `clearDRepDelegations` step becomes unnecessary and the attack surface disappears entirely.

### Proof of Concept

```
-- Attacker script (pseudocode):
for i in 1..N:
    register staking credential C_i  (deposit: ppKeyDeposit)
    submit DelegVote C_i (DRepCredential victimDRep)
    -- Each submission calls processDelegationInternal, which does:
    --   certVStateL . vsDRepsL %~ Map.adjust (drepDelegsL %~ Set.insert C_i) victimDRep
    -- Growing drepDelegs to size N.

-- Victim DRep submits:
ConwayUnRegDRep victimDRep refund
-- Triggers clearDRepDelegations (drepDelegs dRepState)
-- = foldr (Map.adjust (dRepDelegationAccountStateL .~ Nothing)) accountsMap [C_1..C_N]
-- O(N * log(|accounts|)) work in a single ledger-rule step.
-- For N = 500,000 this exceeds practical block-validation budgets;
-- the transaction cannot be included and the deposit is frozen.
``` [7](#0-6) [8](#0-7) [1](#0-0)

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/DRep.hs (L166-171)
```haskell
data DRepState = DRepState
  { drepExpiry :: !EpochNo
  , drepAnchor :: !(StrictMaybe Anchor)
  , drepDeposit :: !(CompactForm Coin)
  , drepDelegs :: !(Set (Credential Staking))
  }
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L293-301)
```haskell
    ConwayRegDelegCert stakeCred delegatee deposit -> do
      checkDepositAgainstPParams deposit
      checkStakeKeyNotRegistered stakeCred
      checkStakeDelegateeRegistered delegatee
      pure $
        processDelegationInternal (pvMajor pv < natVersion @10) stakeCred Nothing delegatee $
          certState
            & certDStateL . accountsL
              %~ registerConwayAccount stakeCred ppKeyDepositCompact (Just delegatee)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L338-345)
```haskell
    delegStake stakePool cState =
      cState
        & certDStateL . accountsL
          %~ adjustAccountState (stakePoolDelegationAccountStateL ?~ stakePool) stakeCred
        & maybe
          (certPStateL . psStakePoolsL %~ Map.adjust (spsDelegatorsL %~ Set.insert stakeCred) stakePool)
          (\accountState -> certPStateL %~ unDelegReDelegStakePool stakeCred accountState (Just stakePool))
          mAccountState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Deleg.hs (L363-365)
```haskell
                | isNothing mAccountState || preserveIncorrectDelegation ->
                    certVStateL . vsDRepsL
                      %~ Map.adjust (drepDelegsL %~ Set.insert stakeCred) dRepCred
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/PulsingReward.hs (L82-114)
```haskell
-- To prevent a huge pause, at the stability point, we spread out the
-- Calculation of rewards over many blocks. We do this in 3 phases. Phase 1
-- of a reward upate is a pure computation, computing some parameters which
-- become fixed at the time when we reach the stability point. One of these
-- parameters is a Pulser, i.e. a computation that when pulseM'ed computes
-- a portion of what is required, so that the whole compuation can be spread out in time.

startStep ::
  forall era.
  (EraGov era, EraCertState era) =>
  EpochSize ->
  BlocksMade ->
  EpochState era ->
  Coin ->
  ActiveSlotCoeff ->
  NonZero Word64 ->
  PulsingRewUpdate
startStep slotsPerEpoch b@(BlocksMade b') es@(EpochState acnt ls ss nm) maxSupply asc secparam =
  let SnapShot activeStake totalActiveStake stakePoolSnapShots = ssStakeGo ss
      numStakeCreds = fromIntegral (VMap.size $ unActiveStake activeStake)
      k = toIntegerNonZero secparam
      -- We expect approximately 10k-many blocks to be produced each epoch.
      -- The reward calculation begins (4k/f)-many slots into the epoch,
      -- and we guarantee that it ends (2k/f)-many slots before the end
      -- of the epoch (to allow tools such as db-sync to see the reward
      -- values in advance of them being applied to the ledger state).
      --
      -- Therefore to evenly space out the reward calculation, we divide
      -- the number of stake credentials by 4k in order to determine how many
      -- stake credential rewards we should calculate each block.
      -- If it does not finish in this amount of time, the calculation is
      -- forced to completion.
      pulseSize = max 1 (ceiling (numStakeCreds %. (knownNonZero @4 `mulNonZero` k)))
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L175-199)
```haskell
-- | We iterate over a pulse-sized chunk of the Accounts.
--
-- For each staking credential in the chunk that has delegated to a DRep, add
-- the stake distribution, rewards, and proposal deposits for that credential to
-- the DRep distribution, if the DRep is a DRepCredential (also, AlwaysAbstain
-- or AlwaysNoConfidence) and a member of the registered DReps. If the
-- DRepCredential is not a member of the registered DReps, ignore and skip that
-- DRep.
--
-- For each staking credential in the chunk that has delegated to an SPO,
-- add only the proposal deposits for that credential to the stake pool
-- distribution, since the rewards and stake are already added to it by the
-- SNAP rule.
--
-- Give or take, this operation has roughly
-- @
--   O (a * (log(b) + log(c) + log(d) + log(e) + log(f)))
-- @
-- complexity, where,
--   (a) is the size of the chunk of the Accounts, which is the pulse-size, iterate over
--   (b) is the size of the StakeDistr, lookup
--   (c) is the size of the DRepDistr, insertWith
--   (d) is the size of the dpProposalDeposits, lookup
--   (e) is the size of the registered DReps, lookup
--   (f) is the size of the PoolDistr, insert
```
