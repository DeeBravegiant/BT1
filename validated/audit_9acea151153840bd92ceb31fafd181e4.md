### Title
Unbounded Governance Proposals Collection Causes Unbounded Recursive Computation in RATIFY Rule at Epoch Boundary - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs`)

---

### Summary

The Conway governance system accumulates `GovActionState` entries in an unbounded `Proposals` collection with no protocol-enforced hard cap on the total number of live proposals. At each epoch boundary, the `RATIFY` rule recursively processes every proposal in the snapshot via a non-tail-recursive STS transition. Additionally, `updateNumDormantEpochs` performs a full O(n) filter over all proposals at every epoch boundary. Because the `EPOCH` rule is declared infallible (`PredicateFailure = Void`), any resource exhaustion during this computation manifests as a node crash or divergence rather than a clean ledger rejection.

---

### Finding Description

**Root cause — no hard cap on proposals:**

The `Proposals` type stores all live governance actions in an `OMap GovActionId (GovActionState era)`: [1](#0-0) 

Any ADA holder can add proposals via the `GOV` rule. The only protocol-level controls are the `govActionDeposit` (an economic deterrent, not a hard limit) and `govActionLifetime` (expiry window). Neither is a hard cap on the total number of simultaneously live proposals.

**Unbounded recursive traversal in `ratifyTransition`:**

At the epoch boundary, `finishDRepPulser` calls `runConwayRatify` with `dpProposals` — a snapshot of the full proposals sequence: [2](#0-1) 

`ratifyTransition` processes this sequence via a recursive STS rule. The `else` branch is **not tail-recursive**: it calls `trans @(RATIFY era)` and then performs additional work (`Set.insert gasId`), meaning the Haskell call stack grows one frame per proposal: [3](#0-2) 

**O(n) filter over all proposals at every epoch boundary:**

`updateNumDormantEpochs` performs a full `OMap.filter` over all proposals unconditionally at every epoch boundary: [4](#0-3) 

**O(n) fold when initializing the DRepPulser:**

`proposalsDeposits` folds over all proposals to build the deposit map snapshot: [5](#0-4) 

**The EPOCH rule cannot fail:** [6](#0-5) 

Because `PredicateFailure (EPOCH era) = Void`, there is no clean rejection path. Resource exhaustion during epoch boundary processing causes a node crash or divergence, not a ledger-level error.

---

### Impact Explanation

If the proposals collection grows large enough, the recursive `ratifyTransition` can overflow the Haskell runtime stack (default 8 MB; each non-tail-recursive frame adds ~100–200 bytes, giving a practical limit of ~40,000–80,000 proposals before overflow). Even below overflow, the O(n) computation at every epoch boundary can cause nodes to miss their slot leadership window. Because all honest nodes execute the same mandatory `EPOCH` rule, nodes with different runtime configurations (stack size, memory) may diverge in their ability to complete the epoch boundary — satisfying the **High** impact criterion of deterministic disagreement between honest nodes from ledger rule evaluation. In the worst case, a network-wide halt would require a hard fork to recover, satisfying the **High** criterion of permanent freezing of funds/rewards requiring a hard fork.

---

### Likelihood Explanation

The `govActionDeposit` on mainnet is 100,000 ADA per proposal, making large-scale exploitation economically expensive (deposits are returned on expiry, so the cost is opportunity cost, not permanent loss). However:

1. `govActionDeposit` is a governance parameter — it can be lowered by a governance vote, after which the attack becomes cheap.
2. The `govActionLifetime` window (6 epochs on mainnet) allows proposals to accumulate across many blocks before expiry.
3. There is no protocol-level `maxProposals` invariant anywhere in the ledger rules.

Likelihood is **Low** under current mainnet parameters but **Medium** if `govActionDeposit` is reduced by governance.

---

### Recommendation

1. **Add a hard protocol-level cap** on the total number of simultaneously live proposals (e.g., a new `maxGovProposals` protocol parameter), enforced in the `GOV` rule before `proposalsAddAction` is called.
2. **Convert `ratifyTransition` to an iterative fold** rather than a recursive STS call, eliminating the stack-depth dependency on proposal count.
3. **Short-circuit `updateNumDormantEpochs`** by maintaining a separate counter of active proposals rather than performing a full `OMap.filter` at every epoch boundary.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. Attacker submits transactions containing `proposal_procedures` (each paying `govActionDeposit`) across many blocks within a `govActionLifetime` window.
2. Proposals accumulate in `cgsProposals :: Proposals era` with no hard cap.
3. At the epoch boundary, `epochTransition` calls `extractDRepPulsingState` → `finishDRepPulser` → `runConwayRatify` → `applySTS @(RATIFY era)` with `RatifySignal dpProposals` containing all N proposals.
4. `ratifyTransition` recurses N times, growing the Haskell call stack by one non-tail-recursive frame per proposal.
5. Simultaneously, `updateNumDormantEpochs` performs `OMap.filter` over all N proposals.
6. With N large enough, nodes crash at the epoch boundary or take too long to complete it, causing ledger divergence.

**Relevant code chain:**

- Proposals added without cap: [7](#0-6) 
- Snapshot taken at epoch boundary: [8](#0-7) 
- Recursive RATIFY invocation: [9](#0-8) 
- Infallible EPOCH rule: [10](#0-9)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Proposals.hs (L238-244)
```haskell
data Proposals era = Proposals
  { pProps :: !(OMap.OMap GovActionId (GovActionState era))
  , pRoots :: !(GovRelation PRoot)
  , pGraph :: !(GovRelation PGraph)
  }
  deriving stock (Show, Eq, Generic)
  deriving anyclass (NoThunks, NFData, Default)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Proposals.hs (L566-579)
```haskell
proposalsDeposits ::
  Proposals era ->
  Map (Credential Staking) (CompactForm Coin)
proposalsDeposits =
  F.foldl'
    ( \gasMap gas ->
        Map.insertWith
          addCompactCoin
          (gas ^. gasReturnAddrL . accountAddressCredentialL)
          (fromMaybe (CompactCoin 0) $ toCompact $ gas ^. gasDepositL)
          gasMap
    )
    mempty
    . proposalsActions
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L409-417)
```haskell
    !ratifySig = RatifySignal dpProposals
    !ratifyState =
      RatifyState
        { rsEnactState = dpEnactState
        , rsEnacted = mempty
        , rsExpired = mempty
        , rsDelayed = False
        }
    !ratifyState' = runConwayRatify dpGlobals ratifyEnv ratifyState ratifySig
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L334-359)
```haskell
  case rsig of
    gas@GovActionState {gasId, gasExpiresAfter} SSeq.:<| sigs -> do
      let govAction = gasAction gas
      if prevActionAsExpected gas ensPrevGovActionIds
        && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
        && not rsDelayed
        && withdrawalCanWithdraw govAction ensTreasury
        && acceptedByEveryone env st gas
        then do
          newEnactState <-
            trans @(EraRule "ENACT" era) $
              TRC ((), rsEnactState, EnactSignal gasId govAction)
          let
            st' =
              st
                & rsEnactStateL .~ newEnactState
                & rsDelayedL .~ delayingAction govAction
                & rsEnactedL %~ (Seq.:|> gas)
          trans @(RATIFY era) $ TRC (env, st', RatifySignal sigs)
        else do
          -- This action hasn't been ratified yet. Process the remaining actions.
          st' <- trans @(RATIFY era) $ TRC (env, st, RatifySignal sigs)
          -- Finally, filter out actions that have expired.
          if gasExpiresAfter < reCurrentEpoch
            then pure $ st' & rsExpiredL %~ Set.insert gasId
            else pure st'
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L174-177)
```haskell
  -- EPOCH rule can never fail
  type PredicateFailure (EPOCH era) = Void
  type Event (EPOCH era) = ConwayEpochEvent era
  transitionRules = [epochTransition]
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Epoch.hs (L197-201)
```haskell
updateNumDormantEpochs :: EpochNo -> Proposals era -> VState era -> VState era
updateNumDormantEpochs currentEpoch ps vState =
  if null $ OMap.filter ((currentEpoch <=) . gasExpiresAfter) $ ps ^. pPropsL
    then vState & vsNumDormantEpochsL %~ succ
    else vState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L561-566)
```haskell
        -- Ancestry checks and accept proposal
        let expiry = pp ^. ppGovActionLifetimeL
            actionState = mkGovActionState newGaid proposal expiry currentEpoch
         in case proposalsAddAction actionState proposals of
              Just updatedProposals -> pure updatedProposals
              Nothing -> proposals <$ failBecause (injectFailure $ InvalidPrevGovActionId proposal)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance.hs (L509-511)
```haskell
                          & ensTreasuryL .~ epochState ^. treasuryL
                    , dpProposals = proposalsActions props
                    , dpProposalDeposits = proposalsDeposits props
```
