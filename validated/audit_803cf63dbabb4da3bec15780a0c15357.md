### Title
Unbounded O(n) Iteration Over All Registered DReps in `updateDormantDRepExpiry` Triggered by Any Governance Proposal — (`eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Certs.hs`)

---

### Summary

The `updateDormantDRepExpiry` function performs an unconditional `Map.map` traversal over the entire `vsDReps` map (all registered DReps in the ledger state) whenever a transaction contains at least one governance proposal and `vsNumDormantEpochs > 0`. Because the number of registered DReps is unbounded by any protocol parameter, an attacker who pre-registers a large number of DReps and then submits a governance proposal during a dormant period can force every validating node to perform an O(|vsDReps|) computation inside the per-transaction ledger rule, causing block-processing time to grow without bound and potentially exceeding the slot-time budget available to nodes.

---

### Finding Description

**Root cause — `updateDormantDRepExpiry`** [1](#0-0) 

```haskell
updateDormantDRepExpiry currentEpoch vState =
  if numDormantEpochs == EpochNo 0
    then vState
    else
      vState
        & vsNumDormantEpochsL .~ EpochNo 0
        & vsDRepsL %~ Map.map updateExpiry   -- ← O(|vsDReps|) over ALL DReps
```

The `Map.map updateExpiry` call visits every entry in `vsDReps` regardless of how many DReps are registered. There is no early-exit, no chunk limit, and no protocol parameter that caps the total DRep count.

**Trigger — `updateDormantDRepExpiries`** [2](#0-1) 

```haskell
updateDormantDRepExpiries tx currentEpoch =
  let hasProposals = not . OSet.null $ tx ^. bodyTxL . proposalProceduresTxBodyL
   in if hasProposals
        then certVStateL %~ updateDormantDRepExpiry currentEpoch
        else id
```

Any transaction that carries at least one `ProposalProcedure` activates this path. Governance proposals are permissionless — any party that pays `ppGovActionDeposit` may submit one.

**Call sites — both pre- and post-hardfork**

Pre-hardfork (CERTS rule, `Empty` certificate list branch): [3](#0-2) 

Post-hardfork (LEDGER rule, `hardforkConwayMoveWithdrawalsAndDRepChecksToLedgerRule` branch): [4](#0-3) 

**DRep state storage — unbounded map** [5](#0-4) 

`vsDReps :: !(Map (Credential DRepRole) DRepState)` has no size cap in the protocol parameters. The only cost to register a DRep is `ppDRepDeposit` (500 ADA on mainnet), making large-scale registration economically feasible for a well-funded attacker.

**Analogous loop structure to the reported vulnerability**

The original report describes `_requestExitsBasedOnRedeemDemandAfterRebalancings` iterating over all validators in a nested loop. Here, `Map.map updateExpiry` is a single-pass O(n) traversal, but the same structural problem applies: the work performed per transaction is proportional to a global ledger-state quantity that the transaction author does not control and that grows over time.

---

### Impact Explanation

Every Cardano node must evaluate the LEDGER (or CERTS) rule for every transaction in every block it validates. If `|vsDReps|` is large (e.g., tens of thousands of registered DReps) and `numDormantEpochs > 0`, a single governance-proposal transaction forces all nodes to iterate over the entire DRep map. Because Cardano's native ledger rules have no per-transaction CPU budget analogous to Ethereum gas, there is no mechanism to reject the transaction as "too expensive" — the node must complete the traversal or crash. With a sufficiently large DRep set, the processing time for one block can exceed the slot duration, causing nodes to fall behind the chain tip. This constitutes an attacker-controlled transaction exceeding intended validation limits (the slot-time processing budget), matching the **Medium** allowed impact: *"Attacker-controlled transactions … exceed intended validation limits."*

---

### Likelihood Explanation

- Governance proposals are permissionless; any party paying `ppGovActionDeposit` can submit one.
- DRep registration is permissionless; any party paying `ppDRepDeposit` per credential can register.
- Dormant epochs accumulate automatically whenever no governance proposals are active — a natural condition on mainnet between governance cycles.
- The attacker controls the timing: register DReps, wait for dormancy, then submit a proposal.
- The capital cost scales linearly with the number of DReps registered, making the attack expensive but not impossible for a motivated adversary.

---

### Recommendation

1. **Cap the per-transaction DRep-expiry update work.** Instead of `Map.map` over all DReps, process only a bounded chunk per transaction (analogous to the pulsing strategy already used for reward computation in `PulsingReward.hs`).

2. **Introduce a `maxDReps` protocol parameter** to bound `|vsDReps|` at the protocol level, preventing unbounded growth of the map.

3. **Amortize the dormant-epoch bump.** Rather than applying the full dormant-epoch adjustment eagerly on the first proposal after a dormant period, apply it lazily at DRep-expiry query time (as is already done in `queryDRepState` via `updateDormantDRepExpiry'`), and remove the eager `Map.map` from the transaction-processing path entirely. [6](#0-5) 

---

### Proof of Concept

1. Register `N` DRep credentials (each paying `ppDRepDeposit`), where `N` is large (e.g., 50,000).
2. Allow `k ≥ 1` epochs to pass with no governance proposals, so `vsNumDormantEpochs` increments to `k`.
3. Submit a transaction containing a single `InfoAction` governance proposal (paying `ppGovActionDeposit`).
4. During LEDGER/CERTS rule evaluation, `updateDormantDRepExpiries` detects `hasProposals = True` and calls `updateDormantDRepExpiry`, which executes `Map.map updateExpiry` over all `N` DRep entries.
5. Observe that node CPU time for processing this single transaction scales linearly with `N`, and with `N` large enough, block-processing time exceeds the slot duration.

The relevant code path is deterministic and identical on all nodes, so no ledger divergence occurs — but all nodes are equally slowed, making the attack network-wide.

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L383-392)
```haskell
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

**File:** libs/cardano-ledger-api/src/Cardano/Ledger/Api/State/Query.hs (L188-194)
```haskell
queryDRepState nes creds
  | null creds = updateDormantDRepExpiry' vState ^. vsDRepsL
  | otherwise = updateDormantDRepExpiry' vStateFiltered ^. vsDRepsL
  where
    vStateFiltered = vState & vsDRepsL %~ (`Map.restrictKeys` creds)
    vState = nes ^. nesEsL . esLStateL . lsCertStateL . certVStateL
    updateDormantDRepExpiry' = Conway.updateDormantDRepExpiry (nes ^. nesELL)
```
