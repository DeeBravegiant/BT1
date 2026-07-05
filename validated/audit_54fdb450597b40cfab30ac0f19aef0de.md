### Title
Guard Script Context Missing Subtransaction TxInfos Allows Bypass of Subtransaction Validation - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs)

### Summary
In the Dijkstra era's `PlutusV4` guard script context construction, subtransaction `TxInfo`s (`_subTxInfosForGuards`) are computed but explicitly not included in the script context passed to guard scripts. A `-- TODO: Include _subTxInfosForGuards` comment acknowledges this omission. Guard scripts therefore receive only the top-level transaction's `TxInfo` and cannot inspect the content of the subtransactions they are supposed to authorize, allowing an attacker to embed malicious subtransactions that bypass guard-script validation.

### Finding Description

The `toPlutusTxInfo` instance for `'PlutusV4` in `DijkstraEra` builds the script context for `GuardingPurpose` scripts as follows:

```haskell
Right $ \case
  purpose@(GuardingPurpose AsPurpose) -> do
    _subTxInfosForGuards <-
      forM (OMap.elems (tx ^. bodyTxL . subTransactionsTxBodyL)) $ \subTx -> do
        let txId = txIdTx subTx
        mkTxInfo <- unPlutusTxInfoResult $ ...
        left (SubTxContextError txId) $ mkTxInfo purpose
    -- TODO: Include _subTxInfosForGuards
    Right topTxInfo          -- ← subtransaction infos silently discarded
  _ -> Right topTxInfo
``` [1](#0-0) 

The `_subTxInfosForGuards` list is fully computed (including recursive `toPlutusTxInfo` calls for every embedded subtransaction) but then thrown away. The guard script receives only `topTxInfo`, which is the `PV3.TxInfo` of the top-level transaction and contains none of the subtransaction inputs, outputs, minting, certificates, withdrawals, or any other per-subtransaction fields.

The `mkAnyLevelTxInfo` helper that builds `topTxInfo` populates only the top-level transaction's fields:

```haskell
Right $
  PV3.TxInfo
    { PV3.txInfoInputs = inputsInfo
    , PV3.txInfoOutputs = outputs
    ...
    }
``` [2](#0-1) 

Subtransactions are embedded in the top-level body via `subTransactionsTxBodyL` but are not translated into any field of `PV3.TxInfo`, so a guard script has no way to observe what the subtransactions actually do.

The `GuardingPurpose` (`DijkstraGuarding`) is the dedicated Plutus purpose for guard scripts:

```haskell
data DijkstraPlutusPurpose f era
  = ...
  | DijkstraGuarding !(f Word32 ScriptHash)
``` [3](#0-2) 

The UTXOW rule enforces that every guard credential required by a subtransaction is present in the top-level transaction's `guards` set and that the corresponding script validates:

```haskell
let requiredGuardsBySubTxs =
      foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
    topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
    missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards
``` [4](#0-3) 

The guard script is therefore the sole Plutus-level gate that can inspect and restrict what subtransactions do. Because the script context omits subtransaction `TxInfo`s, that gate is blind to subtransaction content.

**Current exploitability note**: `toPlutusScriptPurpose` for `PlutusV4` is currently a stub (`error "stub: PlutusV4 not yet implemented"`), which means execution panics before a guard script can run.

```haskell
toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"
``` [5](#0-4) 

The root cause (missing subtransaction infos in the context) is already present in the committed code and will be live the moment `toPlutusScriptPurpose` is implemented, unless the TODO is resolved first.

### Impact Explanation

Guard scripts are the primary Plutus-level authorization mechanism for subtransactions in the Dijkstra era. A guard script that attempts to enforce constraints on subtransaction content (e.g., "only allow subtransactions that pay to address X", "only allow subtransactions that mint policy Y", "only allow subtransactions that submit certificate Z") will silently receive a context that contains none of that information. The script therefore cannot distinguish a benign subtransaction from a malicious one.

An attacker who controls the transaction author role can:
1. Satisfy the guard script's key-hash or native-script requirements (or use a permissive guard script).
2. Embed subtransactions that spend UTxOs to attacker-controlled addresses, mint unauthorized tokens, or submit unauthorized certificates/governance actions.
3. The guard script passes because it sees only `topTxInfo` and cannot observe the subtransaction content.

This constitutes a direct loss or unauthorized creation/destruction of ADA or native assets through an invalid ledger state transition, and potentially unauthorized governance actions enacted via subtransactions — matching the Critical and Medium impact tiers.

### Likelihood Explanation

**Medium**. The missing inclusion is explicitly flagged with a `-- TODO` comment in committed production code, meaning it is a known incomplete implementation rather than an accidental omission. Once `toPlutusScriptPurpose` for `PlutusV4` is completed (a necessary step before the Dijkstra era can be deployed), the vulnerability becomes immediately reachable by any unprivileged transaction sender who can construct a top-level Dijkstra transaction with subtransactions. No privileged access, key compromise, or consensus majority is required.

### Recommendation

Extend the `PV3.TxInfo` (or introduce a Dijkstra-specific `TxInfo` extension) to carry a list of subtransaction `TxInfo`s, and populate it from `_subTxInfosForGuards` before returning the context to guard scripts:

```haskell
Right $ \case
  purpose@(GuardingPurpose AsPurpose) -> do
    subTxInfosForGuards <-
      forM (OMap.elems (tx ^. bodyTxL . subTransactionsTxBodyL)) $ \subTx -> do
        let txId = txIdTx subTx
        mkTxInfo <- unPlutusTxInfoResult $ ...
        left (SubTxContextError txId) $ mkTxInfo purpose
    -- Include subTxInfosForGuards in the context passed to the guard script
    Right topTxInfo { PV3.txInfoSubTransactions = subTxInfosForGuards }
  _ -> Right topTxInfo
```

If the upstream Plutus `TxInfo` type cannot be extended, a Dijkstra-specific wrapper type should be introduced so that guard scripts can observe the full subtransaction context before approving.

### Proof of Concept

1. Deploy a PlutusV4 guard script `G` that is intended to enforce "subtransactions may only pay to address `A`". Because `G` receives only `topTxInfo`, it has no field to inspect subtransaction outputs and must either always succeed or always fail regardless of subtransaction content.

2. Construct a top-level Dijkstra transaction `T` with:
   - `guards = [hash(G)]`
   - A subtransaction `S` whose `requiredTopLevelGuards = {hash(G)}` and whose outputs pay entirely to attacker address `B ≠ A`.

3. Submit `T`. The UTXOW rule confirms `hash(G) ∈ guards` and runs `G` with `topTxInfo`. Since `topTxInfo` contains no subtransaction output data, `G` cannot detect the payment to `B` and returns `True`.

4. `S` executes, transferring funds to `B`, bypassing the guard script's intended restriction. [1](#0-0) [4](#0-3)

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L116-124)
```haskell
data DijkstraPlutusPurpose f era
  = DijkstraSpending !(f Word32 TxIn)
  | DijkstraMinting !(f Word32 PolicyID)
  | DijkstraCertifying !(f Word32 (TxCert era))
  | DijkstraWithdrawing !(f Word32 AccountAddress)
  | DijkstraVoting !(f Word32 Voter)
  | DijkstraProposing !(f Word32 (ProposalProcedure era))
  | DijkstraGuarding !(f Word32 ScriptHash)
  deriving (Generic)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L292-297)
```haskell
  {- concatMapˡ (λ txSub → mapˢ proj₁ (TopLevelGuardsOf txSub)) (SubTransactionsOf txTop) ⊆ GuardsOf txTop -}
  let requiredGuardsBySubTxs =
        foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
      topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
      missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
  runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards
```
