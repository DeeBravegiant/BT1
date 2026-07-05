### Title
Unbounded Iteration Over All Registered DReps in `updateDormantDRepExpiry` Triggered by Governance Proposal Submission - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`)

---

### Summary

The `updateDormantDRepExpiry` function in the Conway `CERTS` rule applies `Map.map updateExpiry` to the **entire** `vsDReps` map — every registered DRep — whenever a transaction contains at least one governance proposal and `vsNumDormantEpochs > 0`. There is no protocol parameter bounding the number of registered DReps. Any unprivileged user can submit a governance proposal, triggering O(n\_dreps) ledger-rule computation that is not bounded by `maxTxSize`, `maxTxExUnits`, or any other protocol parameter, causing block-validation work to grow without limit as the DRep set grows.

---

### Finding Description

**Root cause — unbounded `Map.map` over `vsDReps`**

In `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`, `updateDormantDRepExpiry` is:

```haskell
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry   -- iterates ALL registered DReps
``` [1](#0-0) 

This is called unconditionally for every DRep in the ledger state. The caller `updateDormantDRepExpiries` fires it whenever the transaction body contains at least one proposal procedure:

```haskell
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
``` [2](#0-1) 

`updateDormantDRepExpiries` is invoked in the `CERTS` transition rule for every transaction that carries proposals: [3](#0-2) 

and again in the Dijkstra `ENTITIES` rule: [4](#0-3) 

**No upper bound on DRep count**

`vsDReps` is a plain `Map (Credential DRepRole) DRepState` with no size cap: [5](#0-4) 

The only barrier to DRep registration is the `ppDRepDeposit` protocol parameter — a financial cost, not a hard limit. There is no `maxDReps` parameter anywhere in the Conway or Dijkstra parameter sets.

**Exploit path**

1. Register N DReps (permissionless; each costs `ppDRepDeposit`). Alternatively, wait for the ecosystem to accumulate many legitimate DReps.
2. Allow dormant epochs to accumulate — no governance proposals need to be submitted during this period, so `vsNumDormantEpochs` increments each epoch boundary.
3. Submit a single governance proposal. The `CERTS`/`ENTITIES` rule calls `updateDormantDRepExpiry`, which executes `Map.map updateExpiry` over all N DReps in a single ledger-rule evaluation step.

The work is O(N) in the number of registered DReps and is performed entirely inside the ledger rules — outside the scope of `maxTxExUnits` or `maxBlockExUnits`, which only meter Plutus script execution.

---

### Impact Explanation

**Medium.** An attacker-controlled governance proposal transaction triggers O(n\_dreps) ledger-rule computation with no protocol-enforced upper bound, exceeding intended validation limits. Block-validation time grows proportionally to the number of registered DReps. As the DRep set grows (which is expected and encouraged by the governance design), a single cheap governance proposal can force every validating node to iterate over the entire DRep map. In a sufficiently large DRep ecosystem this degrades block-validation throughput, risks nodes falling behind the chain tip, and could cause deterministic but unexpectedly slow ledger-state transitions — matching the "attacker-controlled transactions exceed intended validation limits" Medium impact category.

---

### Likelihood Explanation

**Medium.** The trigger (submitting a governance proposal) is cheap and permissionless. The precondition (many registered DReps + accumulated dormant epochs) is realistic: the Conway governance design actively incentivises DRep registration, and dormant epochs accumulate naturally during any quiet governance period. An attacker does not need to register all DReps themselves; they can wait for the ecosystem to grow and then time the proposal submission to coincide with a dormant period. The capital cost of registering DReps is a deterrent but not a hard limit, and the attack can be amplified by legitimate ecosystem growth at zero additional cost to the attacker.

---

### Recommendation

1. **Add a protocol parameter `maxDReps`** (or enforce a hard cap) to bound the size of `vsDReps`, analogous to `maxCollateralInputs` bounding collateral set size.
2. **Make dormant-epoch expiry updates lazy**: instead of eagerly updating all DReps on every proposal transaction, record the pending dormant-epoch delta and apply it per-DRep only when that DRep is individually accessed (registration, update, unregistration, ratification lookup).
3. **Alternatively**, move the bulk update to the epoch boundary (where it already runs as part of `EPOCH`/`NEWEPOCH`) and remove the per-transaction `Map.map` sweep entirely, relying on the epoch-boundary computation which is already expected to be expensive.

---

### Proof of Concept

```
-- Setup (off-chain):
-- 1. Register N DReps (N large, e.g. 100,000 at current ppDRepDeposit).
-- 2. Submit no governance proposals for M epochs so vsNumDormantEpochs = M > 0.

-- Attack transaction (on-chain):
-- 3. Submit any valid governance proposal (e.g. InfoAction).
--    Cost: ppGovActionDeposit (one-time).

-- Ledger rule execution path:
--    LEDGER -> CERTS -> conwayCertsTransition (Empty certificates branch)
--      -> updateDormantDRepExpiries tx currentEpoch
--        -> updateDormantDRepExpiry currentEpoch vState
--          -> vsDRepsL %~ Map.map updateExpiry   -- O(N) work, N = |vsDReps|
```

The `Map.map updateExpiry` call at line 319 of `Certs.hs` iterates every entry in `vsDReps` with no bound check, directly analogous to the unbounded `for` loop over `tokenToGates[tokenId]` described in the external report. [6](#0-5)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L237-241)
```haskell
          pure $
            certState
              & updateDormantDRepExpiries tx currentEpoch
              & updateVotingDRepExpiries tx currentEpoch (pp ^. ppDRepActivityL)
              & certDStateL . accountsL %~ drainAccounts withdrawals
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L257-267)
```haskell
updateDormantDRepExpiries ::
  ( EraTx era
  , ConwayEraTxBody era
  , ConwayEraCertState era
  ) =>
  Tx l era -> EpochNo -> CertState era -> CertState era
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L306-328)
```haskell
-- | Update dormant expiry for all DReps that are active.
-- And also reset the `numDormantEpochs` counter.
updateDormantDRepExpiry ::
  -- | Current Epoch
  EpochNo ->
  VState era ->
  VState era
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry
  where
    numDormantEpochs = vState ^. vsNumDormantEpochsL
    updateExpiry =
      drepExpiryL
        %~ \currentExpiry ->
          let actualExpiry = binOpEpochNo (+) numDormantEpochs currentExpiry
           in if actualExpiry < currentEpoch
                then currentExpiry
                else actualExpiry
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L203-207)
```haskell
  let certStateBeforeCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
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
