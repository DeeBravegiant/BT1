### Title
Sub-Transaction TxInfos Never Observed by PlutusV4 Guard Script Verifier - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`)

---

### Summary

In the Dijkstra era, the `toPlutusTxInfo` implementation for `PlutusV4` computes the `TxInfo` for each sub-transaction (`_subTxInfosForGuards`) when building the script context for a `GuardingPurpose` script, but then **discards this data entirely** before passing the context to the guard script. The guard script therefore cannot observe the sub-transactions it is supposed to authorize, making its soundness guarantee void.

---

### Finding Description

The Dijkstra era introduces a nested-transaction model where a top-level transaction (`TopTx`) may embed sub-transactions (`SubTx`). A new script purpose, `GuardingPurpose`, allows Plutus scripts to act as guards that authorize the execution of sub-transactions. The design intent is that a guard script receives the full context of the sub-transactions it is guarding so it can make an informed authorization decision.

In `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`, the `EraPlutusTxInfo 'PlutusV4 DijkstraEra` instance implements `toPlutusTxInfo`. For a top-level transaction with a `GuardingPurpose` script, the code at lines 508–525 does the following:

```haskell
Right $ \case
  purpose@(GuardingPurpose AsPurpose) -> do
    _subTxInfosForGuards <-
      forM (OMap.elems (tx ^. bodyTxL . subTransactionsTxBodyL)) $ \subTx -> do
        ...
        left (SubTxContextError txId) $ mkTxInfo purpose
    -- TODO: Include _subTxInfosForGuards
    Right topTxInfo          -- ← sub-tx data silently dropped
  _ -> Right topTxInfo
```

The sub-transaction `TxInfo` values are computed into `_subTxInfosForGuards`, but the `-- TODO: Include _subTxInfosForGuards` comment explicitly acknowledges they are **never included** in the `topTxInfo` returned to the guard script. The guard script receives only the top-level transaction's `PV3.TxInfo`, which contains no representation of the sub-transactions' inputs, outputs, minting, certificates, withdrawals, or any other fields. [1](#0-0) 

The `PV3.TxInfo` type (shared by both PlutusV3 and PlutusV4) has no field for sub-transactions. The intended mechanism for exposing sub-transaction data to the guard script is precisely the `_subTxInfosForGuards` list, which is computed but discarded. [2](#0-1) 

Additionally, `toPlutusScriptPurpose` for PlutusV4 is a stub that panics:

```haskell
toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"
``` [3](#0-2) 

This stub is called inside `toPlutusV4Args` when constructing the final `PlutusArgs 'PlutusV4` for script execution: [4](#0-3) 

The guard mechanism is defined in `getDijkstraScriptsNeeded`, which includes `GuardingPurpose` script hashes in the set of scripts that must be executed: [5](#0-4) 

The `UTXOW` rule enforces that all required guard scripts are present and validates them: [6](#0-5) 

---

### Impact Explanation

The guard script mechanism is the primary security boundary protecting sub-transactions in the Dijkstra era. Sub-transactions can spend UTxO inputs, mint/burn native assets, submit certificates, make withdrawals, and submit governance actions — all of which have direct ledger-state consequences.

Because `_subTxInfosForGuards` is never included in the script context, a PlutusV4 guard script receives a `TxInfo` that is **identical regardless of what sub-transactions are embedded**. A guard script that is designed to approve only a specific set of sub-transactions (e.g., only sub-transactions spending from a particular address, or only sub-transactions that do not mint) will approve **any** set of sub-transactions, because it cannot distinguish between them.

This maps to the allowed impact:
- **Critical**: A malicious transaction author can include sub-transactions that spend UTxO outputs or mint native assets that the guard script was designed to prevent, constituting direct loss or creation of ADA/native assets through an invalid ledger state transition.
- **High**: Sub-transactions performing unauthorized withdrawals, certificate submissions, or governance actions outside design parameters.

---

### Likelihood Explanation

The entry path requires only an unprivileged transaction sender. Any party who can submit a Dijkstra-era transaction can:
1. Construct a top-level transaction with a PlutusV4 guard script that is designed to authorize only specific sub-transactions.
2. Embed arbitrary sub-transactions that the guard script would reject if it could observe them.
3. Since the guard script receives `topTxInfo` with no sub-transaction data, it cannot distinguish the malicious sub-transactions from the authorized ones.

The `toPlutusScriptPurpose` stub (`error "stub: PlutusV4 not yet implemented"`) currently prevents PlutusV4 guard scripts from executing at all (they would panic at the node level). This means the vulnerability is not exploitable in the current state of the Dijkstra era implementation. However, the `_subTxInfosForGuards` omission is an acknowledged incomplete implementation (marked `TODO`) that will become a live vulnerability the moment `toPlutusScriptPurpose` is implemented and PlutusV4 is activated on-chain.

---

### Recommendation

Before activating PlutusV4 on-chain, the `_subTxInfosForGuards` list must be included in the script context passed to guard scripts. Concretely:

1. Extend the `PV3.TxInfo` (or introduce a new `PV4.TxInfo`) to include a field for sub-transaction summaries, or pass `_subTxInfosForGuards` as part of the `ScriptContext` redeemer/script-info for `GuardingPurpose`.
2. Remove the `-- TODO: Include _subTxInfosForGuards` placeholder and wire the computed sub-transaction TxInfos into the returned context.
3. Implement `toPlutusScriptPurpose` for PlutusV4 before enabling the language on-chain.
4. Add conformance tests that verify a guard script can observe and reject specific sub-transaction content.

---

### Proof of Concept

A transaction author submits a Dijkstra-era top-level transaction with:
- A PlutusV4 guard script `G` whose logic is: "approve only if all sub-transactions have zero minting."
- Sub-transaction `S` that mints 1,000,000 units of a native asset.

When the ledger evaluates `G` with `GuardingPurpose`, it calls `toPlutusTxInfo` which computes `_subTxInfosForGuards = [txInfoOf(S)]` but then returns `topTxInfo` — the top-level transaction's `TxInfo` with no sub-transaction data. Script `G` sees `txInfoMint = mempty` (the top-level tx has no minting) and approves. Sub-transaction `S` executes and mints the asset, bypassing the guard's intended restriction.

The root cause is at: [7](#0-6)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L498-498)
```haskell
  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L507-526)
```haskell
        Right $ \case
          purpose@(GuardingPurpose AsPurpose) -> do
            _subTxInfosForGuards <-
              forM (OMap.elems (tx ^. bodyTxL . subTransactionsTxBodyL)) $ \subTx -> do
                let txId = txIdTx subTx
                mkTxInfo <-
                  unPlutusTxInfoResult $
                    case Map.lookup txId (ltiMemoizedSubTransactions lti) of
                      Nothing ->
                        toPlutusTxInfo proxy $
                          lti
                            { ltiTx = subTx
                            , ltiMemoizedSubTransactions = mempty
                            }
                      Just txInfoResults ->
                        lookupTxInfoResult (plutusSLanguage proxy) txInfoResults
                left (SubTxContextError txId) $ mkTxInfo purpose
            -- TODO: Include _subTxInfosForGuards
            Right topTxInfo
          _ -> Right topTxInfo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L551-574)
```haskell
        Right $
          PV3.TxInfo
            { PV3.txInfoInputs = inputsInfo
            , PV3.txInfoOutputs = outputs
            , PV3.txInfoReferenceInputs = refInputsInfo
            , PV3.txInfoFee = 0
            , PV3.txInfoMint = Conway.transMintValue (txBody ^. mintTxBodyL)
            , PV3.txInfoTxCerts = txCerts
            , PV3.txInfoWdrl = Conway.transTxBodyWithdrawals txBody
            , PV3.txInfoValidRange = timeRange
            , PV3.txInfoSignatories = Alonzo.transTxBodyReqSignerHashes txBody
            , PV3.txInfoRedeemers = plutusRedeemers
            , PV3.txInfoData = PV3.unsafeFromList $ Alonzo.transTxWitsDatums (tx ^. witsTxL)
            , PV3.txInfoId = Conway.transTxBodyId txBody
            , PV3.txInfoVotes = Conway.transVotingProcedures (txBody ^. votingProceduresTxBodyL)
            , PV3.txInfoProposalProcedures =
                map (Conway.transProposal proxy) $ toList (txBody ^. proposalProceduresTxBodyL)
            , PV3.txInfoCurrentTreasuryAmount =
                strictMaybe Nothing (Just . transCoinToLovelace) $ txBody ^. currentTreasuryValueTxBodyL
            , PV3.txInfoTreasuryDonation =
                case txBody ^. treasuryDonationTxBodyL of
                  Coin 0 -> Nothing
                  coin -> Just $ transCoinToLovelace coin
            }
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L589-599)
```haskell
toPlutusV4Args proxy pv txInfo plutusPurpose maybeSpendingData redeemerData = do
  scriptPurpose <- toPlutusScriptPurpose proxy pv plutusPurpose
  let scriptInfo =
        Conway.scriptPurposeToScriptInfo scriptPurpose (transDatum <$> maybeSpendingData)
  pure $
    PlutusV4Args $
      PV3.ScriptContext
        { PV3.scriptContextTxInfo = txInfo
        , PV3.scriptContextRedeemer = Babbage.transRedeemer redeemerData
        , PV3.scriptContextScriptInfo = scriptInfo
        }
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L166-176)
```haskell
getDijkstraScriptsNeeded ::
  (DijkstraEraTxBody era, DijkstraEraScript era) =>
  UTxO era -> TxBody l era -> AlonzoScriptsNeeded era
getDijkstraScriptsNeeded utxo txb =
  getConwayScriptsNeeded utxo txb
    <> guardingScriptsNeeded
  where
    guardingScriptsNeeded = AlonzoScriptsNeeded $
      catMaybes $
        zipAsIxItem (txb ^. guardsTxBodyL) $
          \(AsIxItem idx cred) -> (\sh -> (GuardingPurpose (AsIxItem idx sh), sh)) <$> credScriptHash cred
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L287-297)
```haskell
  {- scriptIntegrityHash txb = hashScriptIntegrity pp (languages txw) (txrdmrs txw) -}
  -- Per-level: script integrity is per-tx (depends on that tx's redeemers and language views)
  let scriptIntegrity = mkScriptIntegrity pp tx (plutusLanguagesUsedStAnnTx stAnnTx)
  runTest $ Alonzo.checkScriptIntegrityHash tx pp scriptIntegrity

  {- concatMapˡ (λ txSub → mapˢ proj₁ (TopLevelGuardsOf txSub)) (SubTransactionsOf txTop) ⊆ GuardsOf txTop -}
  let requiredGuardsBySubTxs =
        foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
      topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
      missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
  runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards
```
