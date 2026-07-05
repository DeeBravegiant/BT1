### Title
`govActionLifetime` Parameter Change Does Not Update Existing Proposals' `gasExpiresAfter` — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs`)

---

### Summary

When the `govActionLifetime` protocol parameter is changed via a `ParameterChange` governance action, the stored `gasExpiresAfter` field in all existing in-flight `GovActionState` records is **not recalculated**. Each proposal's expiry epoch is fixed at submission time and never updated. This is the direct Cardano analog of the Olympus `setReserveFactor` bug: a parameter that governs a threshold is updated, but the dependent stored state derived from that parameter is left stale.

---

### Finding Description

**Root cause — `mkGovActionState` in `Gov.hs`:**

When a governance proposal is submitted, the `GOV` transition rule calls `mkGovActionState`, which computes `gasExpiresAfter` once and stores it permanently in `GovActionState`:

```haskell
mkGovActionState actionId proposal expiryInterval curEpoch =
  GovActionState
    { ...
    , gasExpiresAfter = addEpochInterval curEpoch expiryInterval  -- fixed at submission
    }
``` [1](#0-0) 

The `expiryInterval` is taken directly from the current `ppGovActionLifetimeL` at submission time:

```haskell
let expiry = pp ^. ppGovActionLifetimeL
    actionState = mkGovActionState newGaid proposal expiry currentEpoch
``` [2](#0-1) 

**Consumption — `RATIFY` rule uses `gasExpiresAfter` directly:**

The `RATIFY` transition rule checks expiry by comparing the stored `gasExpiresAfter` against the current epoch with no recalculation:

```haskell
if gasExpiresAfter < reCurrentEpoch
  then pure $ st' & rsExpiredL %~ Set.insert gasId
  else pure st'
``` [3](#0-2) 

**Missing update — no propagation on `govActionLifetime` change:**

`govActionLifetime` is a fully updatable Conway protocol parameter: [4](#0-3) 

When a `ParameterChange` governance action is enacted and `govActionLifetime` is modified via `conwayApplyPPUpdates`, there is no code path that iterates over the `Proposals` store and recalculates `gasExpiresAfter` for existing entries. [5](#0-4) 

The `GovActionState` data type stores `gasExpiresAfter` as a plain `!EpochNo` field with no lazy recomputation: [6](#0-5) 

---

### Impact Explanation

**Scenario — `govActionLifetime` decreased:**

1. Attacker (any transaction sender) submits a `NoConfidence`, `UpdateCommittee`, or `HardForkInitiation` proposal while `govActionLifetime` is large (e.g., 100 epochs). The stored `gasExpiresAfter = submissionEpoch + 100`.
2. The governance community subsequently enacts a `ParameterChange` reducing `govActionLifetime` to, say, 1 epoch, intending to shorten the window for all active proposals.
3. Because `gasExpiresAfter` is never recalculated, the attacker's proposal remains live for the original 100-epoch window — far beyond the newly intended 1-epoch limit.
4. The attacker has 100 epochs to accumulate DRep/SPO votes and ratify the action.

This allows a governance proposal to remain active and ratifiable well beyond the limit the protocol parameter was changed to enforce, directly violating the intended validation bound on proposal lifetime. The impact maps to:

> **Medium — Attacker-controlled proposals exceed intended validation limits outside design parameters.**

In the worst case (e.g., a `NoConfidence` or `HardForkInitiation` action that accumulates sufficient votes over the extended window), the impact escalates to:

> **Critical — Unauthorized governance or hard-fork action is enacted.**

**Scenario — `govActionLifetime` increased:**

Existing proposals expire sooner than the new parameter would allow, causing legitimate proposals to be dropped prematurely. This is a correctness impact but does not meet the Critical/High threshold on its own.

---

### Likelihood Explanation

- **Entry path is unprivileged**: any transaction sender can submit a governance proposal (`ProposalProcedure`) — no special role required.
- **Trigger condition**: a subsequent `ParameterChange` enacted by governance decreases `govActionLifetime`. This is a realistic operational scenario (e.g., the community decides to tighten proposal windows after observing long-lived contentious proposals).
- **Exploitation window**: the attacker's proposal silently retains its original expiry; no on-chain signal indicates the mismatch. The attacker simply waits and continues soliciting votes.
- **Constraint**: the attacker must still accumulate sufficient DRep/SPO stake to ratify the action, which limits likelihood to **Low–Medium** for the Critical path and **Medium** for the validation-limit path.

---

### Recommendation

After a `ParameterChange` action is enacted and `govActionLifetime` changes, the epoch boundary (`EPOCH`/`NEWEPOCH`) or the `ENACT` rule should iterate over all active proposals in `Proposals` and recompute `gasExpiresAfter` using the new parameter:

```haskell
-- pseudocode in ENACT or epoch boundary
updateProposalExpiries :: EpochInterval -> EpochNo -> Proposals era -> Proposals era
updateProposalExpiries newLifetime currentEpoch =
  over proposalsActionsMapL (Map.map recalc)
  where
    recalc gas = gas { gasExpiresAfter =
        addEpochInterval (gasProposedIn gas) newLifetime }
```

Alternatively, `gasExpiresAfter` should not be stored at all; instead, the `RATIFY` rule should compute expiry dynamically as `gasProposedIn gas + currentGovActionLifetime`, so it always reflects the current parameter. This is the cleaner fix and avoids the stale-state class of bug entirely.

---

### Proof of Concept

1. At epoch `E`, `govActionLifetime = 100`. Attacker submits a `NoConfidence` proposal. `gasExpiresAfter` is stored as `E + 100`. [2](#0-1) 

2. At epoch `E+2`, a `ParameterChange` reducing `govActionLifetime` to `1` is ratified and enacted via `conwayApplyPPUpdates`. The new `PParams` are applied to `ensCurPParams` in `EnactState`. [7](#0-6) 

3. The `NoConfidence` proposal's `gasExpiresAfter` remains `E + 100`. No code in `ENACT`, `EPOCH`, or `NEWEPOCH` updates it. [8](#0-7) 

4. In every subsequent `RATIFY` invocation, the check `gasExpiresAfter < reCurrentEpoch` passes `False` until epoch `E + 101`, giving the attacker 98 extra epochs beyond the newly intended 1-epoch limit to accumulate votes. [9](#0-8) 

5. The test infrastructure confirms `gasExpiresAfter` is set once at submission and used verbatim: [10](#0-9)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L409-418)
```haskell
mkGovActionState actionId proposal expiryInterval curEpoch =
  GovActionState
    { gasId = actionId
    , gasCommitteeVotes = mempty
    , gasDRepVotes = mempty
    , gasStakePoolVotes = mempty
    , gasProposalProcedure = proposal
    , gasProposedIn = curEpoch
    , gasExpiresAfter = addEpochInterval curEpoch expiryInterval
    }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L562-566)
```haskell
        let expiry = pp ^. ppGovActionLifetimeL
            actionState = mkGovActionState newGaid proposal expiry currentEpoch
         in case proposalsAddAction actionState proposals of
              Just updatedProposals -> pure updatedProposals
              Nothing -> proposals <$ failBecause (injectFailure $ InvalidPrevGovActionId proposal)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L335-359)
```haskell
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L1182-1224)
```haskell
conwayApplyPPUpdates ::
  forall era.
  ConwayPParams Identity era ->
  ConwayPParams StrictMaybe era ->
  ConwayPParams Identity era
conwayApplyPPUpdates pp ppu =
  ConwayPParams
    { cppTxFeePerByte = ppApplyUpdate cppTxFeePerByte
    , cppTxFeeFixed = ppApplyUpdate cppTxFeeFixed
    , cppMaxBBSize = ppApplyUpdate cppMaxBBSize
    , cppMaxTxSize = ppApplyUpdate cppMaxTxSize
    , cppMaxBHSize = ppApplyUpdate cppMaxBHSize
    , cppKeyDeposit = ppApplyUpdate cppKeyDeposit
    , cppPoolDeposit = ppApplyUpdate cppPoolDeposit
    , cppEMax = ppApplyUpdate cppEMax
    , cppNOpt = ppApplyUpdate cppNOpt
    , cppA0 = ppApplyUpdate cppA0
    , cppRho = ppApplyUpdate cppRho
    , cppTau = ppApplyUpdate cppTau
    , cppProtocolVersion = cppProtocolVersion pp
    , cppMinPoolCost = ppApplyUpdate cppMinPoolCost
    , cppCoinsPerUTxOByte = ppApplyUpdate cppCoinsPerUTxOByte
    , cppCostModels =
        case cppCostModels ppu of
          THKD SNothing -> cppCostModels pp
          THKD (SJust costModelUpdate) ->
            THKD $ updateCostModels (unTHKD (cppCostModels pp)) costModelUpdate
    , cppPrices = ppApplyUpdate cppPrices
    , cppMaxTxExUnits = ppApplyUpdate cppMaxTxExUnits
    , cppMaxBlockExUnits = ppApplyUpdate cppMaxBlockExUnits
    , cppMaxValSize = ppApplyUpdate cppMaxValSize
    , cppCollateralPercentage = ppApplyUpdate cppCollateralPercentage
    , cppMaxCollateralInputs = ppApplyUpdate cppMaxCollateralInputs
    , cppPoolVotingThresholds = ppApplyUpdate cppPoolVotingThresholds
    , cppDRepVotingThresholds = ppApplyUpdate cppDRepVotingThresholds
    , cppCommitteeMinSize = ppApplyUpdate cppCommitteeMinSize
    , cppCommitteeMaxTermLength = ppApplyUpdate cppCommitteeMaxTermLength
    , cppGovActionLifetime = ppApplyUpdate cppGovActionLifetime
    , cppGovActionDeposit = ppApplyUpdate cppGovActionDeposit
    , cppDRepDeposit = ppApplyUpdate cppDRepDeposit
    , cppDRepActivity = ppApplyUpdate cppDRepActivity
    , cppMinFeeRefScriptCostPerByte = ppApplyUpdate cppMinFeeRefScriptCostPerByte
    }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/PParams.hs (L1301-1308)
```haskell
ppGovActionLifetime :: ConwayEraPParams era => PParam era
ppGovActionLifetime =
  PParam
    { ppName = "govActionLifetime"
    , ppLens = ppGovActionLifetimeL
    , ppEraDecoder = Nothing
    , ppUpdate = Just $ PParamUpdate 29 ppuGovActionLifetimeL
    }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Procedures.hs (L219-228)
```haskell
data GovActionState era = GovActionState
  { gasId :: !GovActionId
  , gasCommitteeVotes :: !(Map (Credential HotCommitteeRole) Vote)
  , gasDRepVotes :: !(Map (Credential DRepRole) Vote)
  , gasStakePoolVotes :: !(Map (KeyHash StakePool) Vote)
  , gasProposalProcedure :: !(ProposalProcedure era)
  , gasProposedIn :: !EpochNo
  , gasExpiresAfter :: !EpochNo
  }
  deriving (Ord, Generic)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/ImpTest.hs (L696-698)
```haskell
        , gasProposedIn = curEpochNo
        , gasExpiresAfter = addEpochInterval curEpochNo (pp ^. ppGovActionLifetimeL)
        }
```
