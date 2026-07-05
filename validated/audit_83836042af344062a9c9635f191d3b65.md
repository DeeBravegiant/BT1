### Title
Plutus V4 Guard Scripts Receive Incomplete Script Context: Sub-Transaction Info Silently Omitted from `GuardingPurpose` Invocation - (File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs)

### Summary
In the Dijkstra era's `EraPlutusTxInfo 'PlutusV4 DijkstraEra` instance, when a Plutus V4 script is invoked with `GuardingPurpose`, the sub-transaction `TxInfo` list (`_subTxInfosForGuards`) is computed but then explicitly discarded — the guard script receives only the top-level `topTxInfo` with no sub-transaction details. This is the direct Cardano analog of the BunniToken `msg.sender` bug: a hook/callback receives the wrong/incomplete context, making any content-based authorization logic in the guard script permanently blind to the sub-transactions it is supposed to authorize.

### Finding Description
The Dijkstra era introduces nested transactions (`SubTx`) and a new `DijkstraGuarding` / `GuardingPurpose` script purpose. The design intent is that a Plutus V4 guard script executed under `GuardingPurpose` should receive the full details of every sub-transaction it is authorizing, so it can enforce invariants on sub-transaction content (minting, certificates, governance actions, withdrawals, etc.).

In `toPlutusTxInfo` for `PlutusV4 DijkstraEra` the relevant branch is:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs  lines 507-525
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
                  lti { ltiTx = subTx, ltiMemoizedSubTransactions = mempty }
              Just txInfoResults ->
                lookupTxInfoResult (plutusSLanguage proxy) txInfoResults
        left (SubTxContextError txId) $ mkTxInfo purpose
    -- TODO: Include _subTxInfosForGuards
    Right topTxInfo                          -- ← sub-tx info silently dropped
  _ -> Right topTxInfo
```

The `_subTxInfosForGuards` binding (the leading `_` suppresses the GHC unused-variable warning) is fully computed and then thrown away. The `-- TODO: Include _subTxInfosForGuards` comment is an explicit acknowledgement that the implementation is incomplete. The guard script's `PV3.ScriptContext` is built from `topTxInfo` alone, which is a `PV3.TxInfo` record that has no field for sub-transactions.

The `PV3.TxInfo` structure used for both PlutusV3 and PlutusV4 (`type PlutusTxInfo 'PlutusV4 = PV3.TxInfo`) contains only top-level transaction fields (`txInfoInputs`, `txInfoOutputs`, `txInfoMint`, `txInfoTxCerts`, `txInfoVotes`, `txInfoProposalProcedures`, etc.). Sub-transaction content — their inputs, outputs, minting, certificates, governance votes, withdrawals — is entirely absent from the context delivered to the guard script.

The UTXOW rule does enforce that every credential declared in a sub-transaction's `requiredTopLevelGuards` is present in the top-level `guards` set, and that the corresponding guard scripts pass phase-1/phase-2 validation. But phase-2 validation of a Plutus V4 guard script is performed against `topTxInfo`, which carries zero information about the sub-transactions being authorized. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
A Plutus V4 guard script invoked under `GuardingPurpose` is structurally blind to every sub-transaction it is supposed to authorize. Any content-based invariant the guard script tries to enforce — "sub-transactions may not mint policy X", "sub-transactions may not submit governance proposals", "sub-transactions may not withdraw from reward account Y" — is trivially bypassed because the script context contains none of that information.

Concrete attack surfaces:

- **Unauthorized native-asset minting**: A sub-transaction's `mint` field is invisible to the guard script. A guard script intended to cap or restrict minting in sub-transactions will always see an empty mint field and will pass unconditionally, allowing arbitrary token creation. This is a direct match for *Critical — direct creation of native assets through an invalid ledger state transition*.
- **Unauthorized governance actions**: Sub-transaction `votingProcedures` and `proposalProcedures` are absent from `topTxInfo`. A guard script meant to gate governance participation by sub-transactions cannot detect unauthorized votes or proposals. This matches *Critical — unauthorized governance/treasury/protocol-parameter action is enacted*.
- **Unauthorized withdrawals**: Sub-transaction `withdrawals` are not visible. A guard script protecting reward-account withdrawals in sub-transactions is ineffective. [4](#0-3) [5](#0-4) 

### Likelihood Explanation
The root cause is present in production source code with an explicit `-- TODO` marker. The `toPlutusScriptPurpose` method for PlutusV4 is currently a runtime `error` stub (`error "stub: PlutusV4 not yet implemented"`), which means PlutusV4 script execution will panic before reaching the guard-script evaluation path today. However, that stub is a separate, parallel incompleteness: once `toPlutusScriptPurpose` is implemented (the natural next step in completing PlutusV4 support), the `_subTxInfosForGuards` omission becomes immediately exploitable with no further changes required. Any integrator who deploys a Plutus V4 guard script relying on sub-transaction content for authorization will be silently unprotected. [6](#0-5) 

### Recommendation
The `_subTxInfosForGuards` list must be included in the `ScriptContext` delivered to guard scripts. Because `PV3.TxInfo` has no sub-transaction field, this requires one of:

1. Defining a new `PV4.TxInfo` type (distinct from `PV3.TxInfo`) that carries a `txInfoSubTransactions :: [PV4.TxInfo]` field, and updating `type PlutusTxInfo 'PlutusV4` accordingly.
2. Encoding the sub-transaction list into an existing extensible field (e.g., as a `Data` value in the redeemer or a dedicated `ScriptContext` extension) until a proper PlutusV4 API is stabilised.

The fix must be applied before `toPlutusScriptPurpose` is implemented, otherwise the vulnerability is live the moment PlutusV4 script execution becomes functional.

### Proof of Concept
1. Deploy a Plutus V4 script `guardScript` whose on-chain logic checks that no sub-transaction mints policy `P` (reads `txInfoMint` of each sub-transaction from the script context).
2. Register `guardScript`'s hash as a guard credential.
3. Construct a top-level transaction `topTx` with `guardsTxBodyL = [ScriptHashObj guardScriptHash]`.
4. Embed a sub-transaction `subTx` that mints 1,000,000 tokens of policy `P` and sets `requiredTopLevelGuards = {ScriptHashObj guardScriptHash => SNothing}`.
5. Submit `topTx` (once `toPlutusScriptPurpose` is implemented).
6. The ledger invokes `guardScript` with `GuardingPurpose`; the script context contains `topTxInfo` where `txInfoMint = mempty` (the top-level tx mints nothing). The guard script sees no minting, evaluates to `True`, and the transaction is accepted.
7. `subTx`'s 1,000,000 tokens of policy `P` are minted without the guard script's knowledge — a direct creation of native assets through an invalid ledger state transition. [7](#0-6) [8](#0-7)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L495-499)
```haskell
instance EraPlutusTxInfo 'PlutusV4 DijkstraEra where
  toPlutusTxCert _ _ = pure . transTxCert

  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L500-526)
```haskell
  toPlutusTxInfo proxy lti@LedgerTxInfo {ltiProtVer, ltiEpochInfo, ltiSystemStart, ltiUTxO, ltiTx} = do
    withBothTxLevels ltiTx mkTopTxInfo mkSubTxInfo
    where
      mkTopTxInfo tx = PlutusTxInfoResult $ do
        txInfo <- mkAnyLevelTxInfo tx
        let
          topTxInfo = txInfo {PV3.txInfoFee = transCoinToLovelace (tx ^. bodyTxL . feeTxBodyL)}
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L580-599)
```haskell
toPlutusV4Args ::
  EraPlutusTxInfo 'PlutusV4 era =>
  proxy 'PlutusV4 ->
  ProtVer ->
  PV3.TxInfo ->
  PlutusPurpose AsIxItem era ->
  Maybe (Data era) ->
  Data era ->
  Either (ContextError era) (PlutusArgs 'PlutusV4)
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxow.hs (L292-297)
```haskell
  {- concatMapˡ (λ txSub → mapˢ proj₁ (TopLevelGuardsOf txSub)) (SubTransactionsOf txTop) ⊆ GuardsOf txTop -}
  let requiredGuardsBySubTxs =
        foldMap (Map.keysSet . (^. bodyTxL . requiredTopLevelGuardsL)) subTxs
      topLevelGuards = OSet.toSet (txBody ^. guardsTxBodyL)
      missingGuards = requiredGuardsBySubTxs `Set.difference` topLevelGuards
  runTestOnSignal $ failureOnNonEmptySet missingGuards MissingRequiredGuards
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L189-208)
```haskell
  DijkstraSubTxBodyRaw ::
    { dstbrSpendInputs :: !(Set TxIn)
    , dstbrReferenceInputs :: !(Set TxIn)
    , dstbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dstbrCerts :: !(OSet.OSet (TxCert era))
    , dstbrWithdrawals :: !Withdrawals
    , dstbrVldt :: !ValidityInterval
    , dstbrGuards :: !(OSet (Credential Guard))
    , dstbrMint :: !MultiAsset
    , dstbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dstbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dstbrNetworkId :: !(StrictMaybe Network)
    , dstbrVotingProcedures :: !(VotingProcedures era)
    , dstbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dstbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dstbrTreasuryDonation :: !Coin
    , dstbrRequiredTopLevelGuards :: !(Map (Credential Guard) (StrictMaybe (Data era)))
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
```
