### Title
Unbounded O(N) Iteration Over All Registered DReps Triggered by Any Governance Proposal Transaction - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`)

---

### Summary

The Conway ledger rule `updateDormantDRepExpiry` performs a full `Map.map` traversal over every registered DRep in `vsDReps` whenever a transaction contains at least one governance proposal and `vsNumDormantEpochs > 0`. Because the number of registered DReps is unbounded by any protocol parameter, an attacker who pre-registers a large number of DReps can force every subsequent governance-proposal-bearing transaction to perform O(N) ledger-state work proportional to the total DRep count, exceeding intended per-transaction validation cost bounds.

---

### Finding Description

**Root cause — `updateDormantDRepExpiry`:** [1](#0-0) 

```haskell
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry   -- O(N) over ALL registered DReps
```

`Map.map updateExpiry` is a full traversal of `vsDReps :: Map (Credential DRepRole) DRepState`. There is no early-exit, no pagination, and no protocol parameter bounding the map's size.

**Trigger — `updateDormantDRepExpiries`:** [2](#0-1) 

```haskell
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
```

Any transaction that carries at least one `ProposalProcedure` activates the full DRep sweep, provided `vsNumDormantEpochs > 0`.

**Call sites in the LEDGER rule (post-PV11) and CERTS rule (pre-PV11):** [3](#0-2) [4](#0-3) 

Both call sites are guarded by `hardforkConwayMoveWithdrawalsAndDRepChecksToLedgerRule` so they are mutually exclusive, but the O(N) sweep is present in both protocol-version branches.

**`VState` holds the unbounded DRep map:** [5](#0-4) 

```haskell
data VState era = VState
  { vsDReps :: !(Map (Credential DRepRole) DRepState)
  , vsCommitteeState :: !(CommitteeState era)
  , vsNumDormantEpochs :: !EpochNo
  }
```

No protocol parameter caps `Map.size vsDReps`. The only economic barrier is `ppDRepDeposit`, a governance-adjustable parameter.

**`vsNumDormantEpochs` accumulates silently:** [6](#0-5) 

The counter increments every epoch in which there are zero active governance proposals. An attacker can let it grow arbitrarily large before submitting a proposal, ensuring the guard `numDormantEpochs == EpochNo 0` is false and the sweep fires.

---

### Impact Explanation

Every Cardano node must re-execute the LEDGER rule for every transaction in every block it validates. When `updateDormantDRepExpiry` fires, it performs O(N) pure Haskell map work — outside the Plutus `maxBlockExUnits` budget — proportional to the total number of registered DReps. Native ledger-rule computation has no per-transaction CPU cap in the protocol parameters. With a sufficiently large DRep set, block validation time for any block containing a governance proposal grows without bound, potentially causing nodes to exceed their slot-time budget, miss leadership slots, or fall behind the chain tip. In the extreme, this constitutes attacker-controlled transactions exceeding intended validation limits (Medium impact per scope).

---

### Likelihood Explanation

The attack requires pre-registering many DReps, each paying `ppDRepDeposit` (currently 500 ADA on mainnet). Registering 50,000 DReps costs ~25,000,000 ADA at current parameters. This is a high but not impossible economic barrier, and `ppDRepDeposit` is itself a governance-adjustable parameter — a future parameter reduction would lower the cost proportionally. The dormant-epoch precondition is naturally satisfied during any quiet governance period. The attacker does not need any privileged role: DRep registration and governance proposal submission are both permissionless certificate/transaction operations.

---

### Recommendation

1. **Lazy / deferred expiry**: Store `numDormantEpochs` as a global offset and compute each DRep's effective expiry on-demand (`vsActualDRepExpiry` already does this). Eliminate the eager `Map.map updateExpiry` sweep entirely; the stored `drepExpiry` field need not be mutated at proposal time.
2. **Protocol parameter cap**: Introduce a `maxDReps` protocol parameter to bound `Map.size vsDReps`, analogous to `maxCollateralInputs` for collateral.
3. **Merge the two update pipelines**: `updateDormantDRepExpiries` and `updateVotingDRepExpiries` are called back-to-back and both traverse `vsDReps`; merging them into a single pass halves the constant factor.

---

### Proof of Concept

1. Register N DReps (N = 100,000; cost = N × `ppDRepDeposit`).
2. Allow several epochs to pass with no governance proposals so `vsNumDormantEpochs` accumulates to, say, 10.
3. Submit a single transaction containing one `ProposalProcedure` (any valid governance action).
4. The LEDGER rule calls `updateDormantDRepExpiries`, which calls `updateDormantDRepExpiry`, which executes `Map.map updateExpiry` over all 100,000 DRep entries.
5. Every validating node must perform this O(100,000) traversal as part of block validation for that block — with no protocol-level bound on the work, and no way for nodes to skip or batch it. [7](#0-6)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L222-241)
```haskell
  case certificates of
    Empty ->
      if hardforkConwayMoveWithdrawalsAndDRepChecksToLedgerRule $ pp ^. ppProtocolVersionL
        then pure certState
        else do
          network <- liftSTS $ asks networkId
          let accounts = certState ^. certDStateL . accountsL
              withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
          failOnJust
            (withdrawalsThatDoNotDrainAccounts withdrawals network accounts)
            ( \(invalid, incomplete) ->
                WithdrawalsNotInRewardsCERTS $
                  Withdrawals $
                    unWithdrawals invalid <> fmap mismatchSupplied incomplete
            )
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs (L308-328)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L382-392)
```haskell
          certState' <-
            if hardforkConwayMoveWithdrawalsAndDRepChecksToLedgerRule $ pp ^. ppProtocolVersionL
              then do
                let withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
                Shelley.testIncompleteAndMissingWithdrawals (certState ^. certDStateL . accountsL) withdrawals
                pure $
                  certState
                    & updateDormantDRepExpiries tx curEpochNo
                    & updateVotingDRepExpiries tx curEpochNo (pp ^. ppDRepActivityL)
                    & certDStateL . accountsL %~ drainAccounts withdrawals
              else pure certState
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
