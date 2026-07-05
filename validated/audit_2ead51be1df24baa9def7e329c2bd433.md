### Title
Enacted `ParameterChange`/`TreasuryWithdrawals` Bypasses New Constitution's Guardrails When `NewConstitution` Is Co-Enacted in the Same Epoch - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs`)

---

### Summary

The guardrails script hash embedded in a `ParameterChange` or `TreasuryWithdrawals` governance action is validated only at proposal submission time (GOV rule). Neither the RATIFY rule nor the ENACT rule re-validates it against the active constitution. Because `NewConstitution` carries a higher enactment priority than `ParameterChange` and `TreasuryWithdrawals`, a `NewConstitution` that changes the guardrails script can be enacted first within the same epoch boundary, after which the already-ratified `ParameterChange`/`TreasuryWithdrawals` is enacted without being subject to the new guardrails. This is the direct Cardano analog of the Llama `authorizedScripts` bug: the "execution mode" (which guardrails govern the action) is fixed at submission time and can be silently changed by a separate governance action with a different voter set.

---

### Finding Description

**Step 1 — Guardrails check is submission-only.**

In `conwayGovTransition` the GOV rule calls `checkGuardrailsScriptHash` against the *current* constitution at the moment the transaction is processed:

```haskell
-- Guardrails script hash check
runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy
``` [1](#0-0) 

`checkGuardrailsScriptHash` simply compares the hash embedded in the proposal against the constitution's `constitutionGuardrailsScriptHash` at that instant:

```haskell
checkGuardrailsScriptHash expectedHash actualHash =
  failureUnless (actualHash == expectedHash) $
    InvalidGuardrailsScriptHash actualHash expectedHash
``` [2](#0-1) 

The Plutus guardrails script itself is also executed as a phase-2 witness at submission time (UTXOW rule). After submission, neither the hash nor the script is re-evaluated.

**Step 2 — RATIFY performs no guardrails re-check.**

`ratifyTransition` only tests five conditions before calling ENACT; none of them involve the guardrails script hash or the current constitution:

```haskell
if prevActionAsExpected gas ensPrevGovActionIds
  && validCommitteeTerm govAction ensCurPParams reCurrentEpoch
  && not rsDelayed
  && withdrawalCanWithdraw govAction ensTreasury
  && acceptedByEveryone env st gas
``` [3](#0-2) 

**Step 3 — ENACT silently discards the guardrails hash.**

The third field of `ParameterChange` (the guardrails script hash) is pattern-matched with `_` and never used:

```haskell
ParameterChange _ ppup _ ->
  st
    & ensCurPParamsL %~ (`applyPPUpdates` ppup)
    & ensPrevPParamUpdateL .~ SJust (GovPurposeId govActionId)
``` [4](#0-3) 

Likewise for `TreasuryWithdrawals`:

```haskell
TreasuryWithdrawals wdrls _ ->
  ...
``` [5](#0-4) 

**Step 4 — Priority ordering guarantees `NewConstitution` is enacted before `ParameterChange`/`TreasuryWithdrawals`.**

```haskell
actionPriority NewConstitution {}    = 2
actionPriority ParameterChange {}    = 4
actionPriority TreasuryWithdrawals {} = 5
``` [6](#0-5) 

`reorderActions` sorts the RATIFY signal by this priority before processing:

```haskell
reorderActions = SS.fromList . sortOn (actionPriority . gasAction) . toList
``` [7](#0-6) 

When ENACT processes `NewConstitution` it updates `ensConstitutionL` in the running `EnactState`:

```haskell
NewConstitution _ c ->
  st
    & ensConstitutionL .~ c
    & ensPrevConstitutionL .~ SJust (GovPurposeId govActionId)
``` [8](#0-7) 

The subsequent `ParameterChange`/`TreasuryWithdrawals` is then enacted against the updated `EnactState` — but the guardrails hash embedded in the action is never compared to the new constitution's `constitutionGuardrailsScriptHash`.

**The `GovAction` data type confirms the hash is stored but never re-used after submission:**

```haskell
| ParameterChange
    !(StrictMaybe (GovPurposeId 'PParamUpdatePurpose))
    !(PParamsUpdate era)
    !(StrictMaybe ScriptHash)   -- guardrails hash, checked only at submission
| TreasuryWithdrawals
    !(Map AccountAddress Coin)
    !(StrictMaybe ScriptHash)   -- guardrails hash, checked only at submission
``` [9](#0-8) 

---

### Impact Explanation

A `ParameterChange` or `TreasuryWithdrawals` proposal submitted and voted on under constitution guardrails `H1` can be enacted after the constitution transitions to guardrails `H2` (via a co-ratified `NewConstitution`). The enacted proposal is never subject to `H2`'s constraints. Concretely:

- **`ParameterChange`**: Protocol parameters (fees, deposits, voting thresholds, execution-unit prices) can be set to values that `H2` would have rejected, modifying ledger economics outside the new constitution's design parameters. This matches the **Medium** impact: *attacker-controlled proposals modify fees, deposits, or withdrawals outside design parameters*.
- **`TreasuryWithdrawals`**: ADA can be withdrawn from the treasury in amounts or to addresses that `H2` would have prohibited. If the guardrails script enforces a treasury-reserve floor (as shown in the preprocessor source), bypassing it constitutes **direct loss of ADA**, matching the **Critical** impact: *direct loss of ADA through an invalid ledger state transition*. [10](#0-9) 

---

### Likelihood Explanation

The scenario requires two governance actions to be ratified in the same epoch — a realistic occurrence during active governance periods. An unprivileged proposer can:

1. Submit a `ParameterChange` or `TreasuryWithdrawals` that passes the current (permissive) guardrails and gather sufficient DRep + CC votes.
2. Observe that a `NewConstitution` with more restrictive guardrails is also approaching ratification in the same epoch.
3. The priority ordering deterministically ensures the `NewConstitution` is enacted first, after which the `ParameterChange`/`TreasuryWithdrawals` is enacted without re-validation.

No privileged access, leaked keys, or malicious supermajority is required. The two voter sets for the two actions can be entirely disjoint; voters who approved the `NewConstitution` have no mechanism to retroactively block the `ParameterChange` from being enacted under the old guardrails.

---

### Recommendation

Re-validate the guardrails script hash at ratification time within `ratifyTransition`. Before calling ENACT for a `ParameterChange` or `TreasuryWithdrawals`, compare the proposal's embedded guardrails hash against `ensConstitution` in the *current* `rsEnactState` (which already reflects any `NewConstitution` enacted earlier in the same pass). If the hashes diverge, treat the action as not ratifiable in this epoch (add it to `rsExpired` or simply skip it), consistent with how `prevActionAsExpected` blocks actions whose parent chain has been superseded.

---

### Proof of Concept

```
Epoch N:
  Constitution: guardrails = H1 (allows parameter X = 9999)
  
  Tx1: Submit ParameterChange P1 { ppuMaxTxSize = 9999, guardrailsHash = H1 }
       → GOV: H1 == H1 ✓; UTXOW: H1 script executes, allows X=9999 ✓
       → P1 enters Proposals, DReps + CC vote Yes

  Tx2: Submit NewConstitution P2 { guardrailsHash = H2 }
       where H2 script rejects ppuMaxTxSize > 1000
       → P2 enters Proposals, DReps + CC vote Yes

Epoch N+1 boundary (RATIFY, reorderActions):
  Process P2 (priority 2, NewConstitution):
    acceptedByEveryone → True
    ENACT: ensConstitutionL .~ Constitution{guardrailsScriptHash = H2}
    rsEnacted :|> P2

  Process P1 (priority 4, ParameterChange):
    prevActionAsExpected → True
    withdrawalCanWithdraw → True (N/A)
    acceptedByEveryone → True
    ENACT: ensCurPParamsL %~ applyPPUpdates {ppuMaxTxSize = 9999}
           -- H2 is NEVER consulted; ppuMaxTxSize = 9999 is enacted
    rsEnacted :|> P1

Result: ppMaxTxSize = 9999 is live on-chain, violating H2's constraint of ≤ 1000.
        Voters who ratified P2 expected H2 to govern all subsequent enactments.
```

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L420-426)
```haskell
checkGuardrailsScriptHash ::
  StrictMaybe ScriptHash ->
  StrictMaybe ScriptHash ->
  Test (ConwayGovPredFailure era)
checkGuardrailsScriptHash expectedHash actualHash =
  failureUnless (actualHash == expectedHash) $
    InvalidGuardrailsScriptHash actualHash expectedHash
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Gov.hs (L546-558)
```haskell
            -- Guardrails script hash check
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy

            -- The sum of all withdrawals must be positive
            F.fold wdrls /= mempty ?! (injectFailure . ZeroTreasuryWithdrawals) pProcGovAction
          UpdateCommittee _mPrevGovActionId membersToRemove membersToAdd _qrm -> do
            let conflicting = Set.intersection (Map.keysSet membersToAdd) membersToRemove
             in failOnNonEmptySet conflicting (injectFailure . ConflictingCommitteeUpdate)

            let invalidMembers = Map.filter (<= currentEpoch) membersToAdd
             in failOnNonEmptyMap invalidMembers (injectFailure . ExpirationEpochTooSmall)
          ParameterChange _ _ proposalPolicy ->
            runTest $ checkGuardrailsScriptHash @era constitutionPolicy proposalPolicy
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L337-352)
```haskell
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
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs (L89-92)
```haskell
      ParameterChange _ ppup _ ->
        st
          & ensCurPParamsL %~ (`applyPPUpdates` ppup)
          & ensPrevPParamUpdateL .~ SJust (GovPurposeId govActionId)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs (L97-103)
```haskell
      TreasuryWithdrawals wdrls _ ->
        let wdrlsAmount = fold wdrls
            wdrlsNoNetworkId = Map.mapKeys (^. accountAddressCredentialL) wdrls
         in st
              { ensWithdrawals = Map.unionWith (<>) wdrlsNoNetworkId $ ensWithdrawals st
              , ensTreasury = ensTreasury st <-> wdrlsAmount
              }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Enact.hs (L112-115)
```haskell
      NewConstitution _ c ->
        st
          & ensConstitutionL .~ c
          & ensPrevConstitutionL .~ SJust (GovPurposeId govActionId)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L534-541)
```haskell
actionPriority :: GovAction era -> Int
actionPriority NoConfidence {} = 0
actionPriority UpdateCommittee {} = 1
actionPriority NewConstitution {} = 2
actionPriority HardForkInitiation {} = 3
actionPriority ParameterChange {} = 4
actionPriority TreasuryWithdrawals {} = 5
actionPriority InfoAction {} = 6
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L543-544)
```haskell
reorderActions :: SS.StrictSeq (GovActionState era) -> SS.StrictSeq (GovActionState era)
reorderActions = SS.fromList . sortOn (actionPriority . gasAction) . toList
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Procedures.hs (L815-834)
```haskell
data GovAction era
  = ParameterChange
      -- | Previous governance action id of `ParameterChange` type, which corresponds to
      -- `PParamUpdatePurpose`.
      !(StrictMaybe (GovPurposeId 'PParamUpdatePurpose))
      -- | Proposed changes to PParams
      !(PParamsUpdate era)
      -- | Guardrails script hash protection
      !(StrictMaybe ScriptHash)
  | HardForkInitiation
      -- | Previous governance action id of `HardForkInitiation` type, which corresponds
      -- to `HardForkPurpose`
      !(StrictMaybe (GovPurposeId 'HardForkPurpose))
      -- | Proposed new protocol version
      !ProtVer
  | TreasuryWithdrawals
      -- | Proposed treasury withdrawals
      !(Map AccountAddress Coin)
      -- | Guardrails script hash protection
      !(StrictMaybe ScriptHash)
```

**File:** libs/plutus-preprocessor/src/Cardano/Ledger/Plutus/Preprocessor/Source/V3.hs (L230-248)
```haskell
ensureTreasuryReserveQ :: Q [Dec]
ensureTreasuryReserveQ =
  [d|
    ensureTreasuryReserve :: P.BuiltinData -> P.BuiltinUnit
    ensureTreasuryReserve context =
      P.check $
        case unsafeFromBuiltinData context of
          PV3D.ScriptContext
            txInfo
            _
            (PV3D.ProposingScript _ (PV3D.ProposalProcedure _ _ (PV3D.TreasuryWithdrawals withdrawals _))) ->
              let
                totalWithdrawal = PAMD.foldr (P.+) 0 withdrawals
               in
                case PV3D.txInfoCurrentTreasuryAmount txInfo of
                  Just treasury -> treasury P.- totalWithdrawal P.>= 100_000_000
                  _ -> False
          _ -> False
    |]
```
