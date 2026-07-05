### Title
Missing `validateRefScriptSize` Check in `dijkstraSubLedgersTransition` Allows Sub-Transactions to Exceed Reference Script Size Limit — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs`)

---

### Summary

The Dijkstra era's `SUBLEDGER` transition rule (`dijkstraSubLedgersTransition`) omits the `validateRefScriptSize` check that is present in the Conway `LEDGER` rule (`conwayLedgerTransitionTRC`). This allows an unprivileged user to submit a sub-transaction whose total reference script size exceeds the `ppMaxRefScriptSizePerTx` protocol parameter limit, bypassing an intended resource constraint that every top-level transaction must satisfy.

---

### Finding Description

In `conwayLedgerTransitionTRC`, when a transaction is valid (`isValid == True`), the rule applies two resource/integrity checks before processing certificates and governance:

```haskell
runTest $ validateTreasuryValue txBody (chainAccountState ^. casTreasuryL)
runTest $ validateRefScriptSize pp (utxoState ^. utxoL) tx
```

`validateRefScriptSize` computes the total non-distinct reference script size for the transaction and rejects it if it exceeds `ppMaxRefScriptSizePerTx`:

```haskell
validateRefScriptSize pp utxo tx =
  let totalRefScriptSize = txNonDistinctRefScriptsSize utxo tx
      maxRefScriptSizePerTx = fromIntegral @Word32 @Int $ pp ^. ppMaxRefScriptSizePerTxG
   in failureUnless (totalRefScriptSize <= maxRefScriptSizePerTx) $ ...
```

In `dijkstraSubLedgersTransition`, when `topIsValid == IsValid True`, only `validateTreasuryValue` is applied — `validateRefScriptSize` is entirely absent:

```haskell
if topIsValid == IsValid True
  then do
    runTest $ Conway.validateTreasuryValue txBody (chainAccountState ^. casTreasuryL)
    -- validateRefScriptSize is NOT called here
    certStateAfterSubEntities <- trans @(EraRule "SUBENTITIES" era) $ ...
```

The omission is explicitly confirmed by the `conwayToDijkstraSubLedgerPredFailure` injection function, which maps the corresponding failure constructor to a runtime `error`:

```haskell
Conway.ConwayTxRefScriptsSizeTooBig _ -> error "Impossible: `ConwayTxRefScriptsSizeTooBig` for SUBLEDGER"
```

This `error` call would cause a node panic if the failure were ever injected from an outer context, and it confirms the developers treated the check as intentionally absent — but without a documented rationale for why sub-transactions are exempt from this limit.

---

### Impact Explanation

An attacker can craft a Dijkstra top-level transaction containing a sub-transaction whose reference scripts total more than `ppMaxRefScriptSizePerTx` bytes. Because `validateRefScriptSize` is never called in `dijkstraSubLedgersTransition`, the ledger accepts and fully processes the sub-transaction, including executing any Plutus scripts that reference those oversized scripts. This bypasses the protocol parameter that was introduced specifically to bound per-transaction script evaluation cost.

**Matched impact:** *Medium — Attacker-controlled transactions exceed intended validation limits.*

---

### Likelihood Explanation

High. Any unprivileged transaction sender can construct a Dijkstra sub-transaction with arbitrarily large reference scripts. No privilege, key compromise, or governance majority is required. The check is completely absent with no compensating control elsewhere in the `SUBLEDGER` rule.

---

### Recommendation

Add the `validateRefScriptSize` call to `dijkstraSubLedgersTransition` inside the `topIsValid == IsValid True` branch, immediately after `validateTreasuryValue`, mirroring the Conway `LEDGER` rule:

```haskell
runTest $ Conway.validateRefScriptSize pp (utxoState ^. utxoL) tx
```

Additionally, remove or replace the `error "Impossible: ConwayTxRefScriptsSizeTooBig for SUBLEDGER"` branch in `conwayToDijkstraSubLedgerPredFailure` with a proper failure constructor so that the failure can be reported rather than causing a node panic.

---

### Proof of Concept

**Conway LEDGER** applies the check (line 365): [1](#0-0) 

**Dijkstra SUBLEDGER** omits it entirely (lines 248–284): [2](#0-1) 

The error mapping confirms the check is intentionally absent (line 375): [3](#0-2) 

**Attack path:**
1. Attacker constructs a Dijkstra top-level transaction containing a sub-transaction.
2. The sub-transaction references UTxO entries whose combined script sizes exceed `ppMaxRefScriptSizePerTx`.
3. The top-level `LEDGER` rule processes the outer transaction; the `SUBLEDGER` rule processes the inner sub-transaction.
4. `dijkstraSubLedgersTransition` calls `validateTreasuryValue` but never `validateRefScriptSize`.
5. The sub-transaction is accepted and its scripts are executed, bypassing the resource limit that every top-level transaction must satisfy.

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L364-365)
```haskell
          runTest $ validateTreasuryValue txBody (chainAccountState ^. casTreasuryL)
          runTest $ validateRefScriptSize pp (utxoState ^. utxoL) tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L248-284)
```haskell
  (utxoStateBeforeSubUtxow, certStateFinal) <-
    if topIsValid == IsValid True
      then do
        runTest $ Conway.validateTreasuryValue txBody (chainAccountState ^. casTreasuryL)

        certStateAfterSubEntities <-
          trans @(EraRule "SUBENTITIES" era) $
            TRC
              ( SubCertsEnv tx pp curEpochNo committee (proposalsWithPurpose grCommitteeL proposals)
              , certState
              , StrictSeq.fromStrict $ txBody ^. certsTxBodyL
              )
        let govEnv =
              Conway.GovEnv
                (txIdTxBody txBody)
                curEpochNo
                pp
                (govState ^. constitutionGovStateL . constitutionGuardrailsScriptHashL)
                certStateAfterSubEntities
                committee
        let govSignal =
              Conway.GovSignal
                { Conway.gsVotingProcedures = txBody ^. votingProceduresTxBodyL
                , Conway.gsProposalProcedures = txBody ^. proposalProceduresTxBodyL
                , Conway.gsCertificates = txBody ^. certsTxBodyL
                }
        proposalsState <-
          trans @(EraRule "SUBGOV" era) $
            TRC
              ( govEnv
              , proposals
              , govSignal
              )
        pure
          ( utxoState & utxosGovStateL . proposalsGovStateL .~ proposalsState
          , certStateAfterSubEntities
          )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L375-375)
```haskell
  Conway.ConwayTxRefScriptsSizeTooBig _ -> error "Impossible: `ConwayTxRefScriptsSizeTooBig` for SUBLEDGER"
```
