### Title
Unbounded Iteration Over All Registered DReps During Governance Proposal Transaction Validation - (File: eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs)

### Summary

When a transaction contains a governance proposal and `numDormantEpochs > 0`, the `updateDormantDRepExpiry` function performs a full `Map.map` traversal over every registered DRep in `vsDReps`. Because the number of registered DReps is unbounded (limited only by the DRep deposit cost), an attacker who registers many DReps can cause any subsequent governance-proposal transaction to perform O(N) work proportional to the total DRep count, exceeding intended per-transaction validation limits.

### Finding Description

`updateDormantDRepExpiry` in `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs` unconditionally maps over the entire `vsDReps` map when `numDormantEpochs ≠ 0`:

```haskell
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry   -- iterates ALL registered DReps
``` [1](#0-0) 

This function is invoked from `updateDormantDRepExpiries`, which fires whenever a transaction body contains at least one `ProposalProcedure`:

```haskell
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
``` [2](#0-1) 

This is called in two places during transaction validation:

1. Inside `conwayCertsTransition` (the `CERTS` rule): [3](#0-2) 

2. Inside the `LEDGER` rule (`conwayLedgerTransition`): [4](#0-3) 

The `vsDReps` map is the global map of all registered DReps stored in `VState`: [5](#0-4) 

There is no protocol-enforced cap on the number of registered DReps. The `DRepState` type stores each DRep's expiry, anchor, deposit, and delegator set: [6](#0-5) 

A secondary unbounded iteration exists in `ConwayUnRegDRep` processing: `clearDRepDelegations` iterates over the entire `drepDelegs` set of a DRep being unregistered, performing one `Map.adjust` per delegator: [7](#0-6) 

### Impact Explanation

Every transaction that includes a governance proposal triggers a full linear scan of all registered DReps during ledger-rule evaluation. With N registered DReps, each such transaction costs O(N) in the `updateDormantDRepExpiry` path. Because block validation is deterministic and unbounded in wall-clock time for native ledger rules, a sufficiently large N causes proposal-bearing transactions to consume disproportionate validation time. This exceeds the intended per-transaction validation budget and can cause block-producing nodes to fall behind chain tip, degrading chain quality. This matches the **Medium** impact: attacker-controlled proposals exceed intended validation limits.

### Likelihood Explanation

The DRep deposit is a fixed ADA amount (currently 500 ADA per DRep). An attacker with sufficient ADA can register thousands of DReps, hold them (deposits are refundable on unregistration), and then submit or wait for a governance proposal transaction to trigger the expensive scan. The dormant-epoch precondition (`numDormantEpochs > 0`) is easily satisfied by waiting for a period with no active proposals. The attack is fully reachable by an unprivileged transaction sender with no special keys.

### Recommendation

1. **Lazy/deferred expiry update**: Instead of eagerly updating all DRep expiries on every proposal transaction, store `numDormantEpochs` as an offset and compute the effective expiry lazily at the point of use (e.g., during ratification or expiry checks), similar to how the `vsActualDRepExpiry` helper already works: [8](#0-7) 

2. **Cap the DRep registry size**: Introduce a protocol parameter bounding the maximum number of simultaneously registered DReps.

3. **For `clearDRepDelegations`**: Remove the `drepDelegs` reverse-index from `DRepState` and instead rely solely on the forward delegation stored in each account's `AccountState`, eliminating the O(D) scan on DRep unregistration.

### Proof of Concept

**Setup:**
1. Attacker registers N DReps (e.g., N = 10,000), paying N × `ppDRepDeposit` ADA.
2. No governance proposals are submitted for several epochs, causing `numDormantEpochs` to accumulate to a value > 0.

**Trigger:**
3. Any user (including the attacker) submits a transaction containing a `ProposalProcedure`.
4. During `LEDGER` rule evaluation, `updateDormantDRepExpiries` fires because `hasProposals = True`.
5. `updateDormantDRepExpiry` executes `vsDRepsL %~ Map.map updateExpiry` over all N DReps.
6. Validation cost is O(N × log N) for the map traversal, with N fully attacker-controlled.

**Recovery:** Attacker unregisters all N DReps, recovering deposits. The attack cost is only the opportunity cost of locked ADA.

The `Map.map` call at line 319 of `Certs.hs` is the exact root cause — it has no bound on the size of `vsDReps`: [9](#0-8)

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L313-328)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L387-392)
```haskell
                pure $
                  certState
                    & updateDormantDRepExpiries tx curEpochNo
                    & updateVotingDRepExpiries tx curEpochNo (pp ^. ppDRepActivityL)
                    & certDStateL . accountsL %~ drainAccounts withdrawals
              else pure certState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L145-146)
```haskell
vsDRepsL :: Lens' (VState era) (Map (Credential DRepRole) DRepState)
vsDRepsL = lens vsDReps (\vs u -> vs {vsDReps = u})
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L154-156)
```haskell
vsActualDRepExpiry :: Credential DRepRole -> VState era -> Maybe EpochNo
vsActualDRepExpiry cred vs =
  binOpEpochNo (+) (vsNumDormantEpochs vs) . drepExpiry <$> Map.lookup cred (vsDReps vs)
```

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
