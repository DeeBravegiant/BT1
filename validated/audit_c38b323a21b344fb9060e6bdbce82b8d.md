### Title
Guard Script Receives No Sub-Transaction Context, Preventing Content-Based Authorization of Sub-Transactions - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`)

---

### Summary

In the Dijkstra era, the `GuardingPurpose` Plutus script execution path computes sub-transaction `TxInfo` objects (`_subTxInfosForGuards`) but then discards them with an explicit `-- TODO: Include _subTxInfosForGuards` comment, passing only the top-level transaction's `TxInfo` to the guard script. This is the direct analog of the external report's vulnerability: just as the `sendAndCall` recipient could not authenticate the origin sender because that information was never forwarded, a Dijkstra guard script cannot inspect or authenticate sub-transaction content because the sub-transaction `TxInfo` is never forwarded to it.

---

### Finding Description

The Dijkstra era introduces nested sub-transactions. A sub-transaction body may declare `requiredTopLevelGuards` — a map of credentials (key hashes or Plutus script hashes) that must appear in the enclosing top-level transaction's `guards` field. When a script hash appears in `guards`, the corresponding Plutus script is executed under `GuardingPurpose` to authorize the sub-transactions.

The `toPlutusTxInfo` implementation for `PlutusV4` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs` handles the `GuardingPurpose` case as follows:

```haskell
Right $ \case
  purpose@(GuardingPurpose AsPurpose) -> do
    _subTxInfosForGuards <-
      forM (OMap.elems (tx ^. bodyTxL . subTransactionsTxBodyL)) $ \subTx -> do
        ...
        left (SubTxContextError txId) $ mkTxInfo purpose
    -- TODO: Include _subTxInfosForGuards
    Right topTxInfo          -- ← sub-tx data is silently dropped
  _ -> Right topTxInfo
```

The sub-transaction `TxInfo` objects are computed into `_subTxInfosForGuards` and then immediately discarded. The guard script receives only `topTxInfo` — the top-level transaction summary — with no information about any sub-transaction's inputs, outputs, minted value, certificates, withdrawals, or governance actions.

The `DijkstraTxBodyRaw` for a sub-transaction carries all the fields a guard script would need to inspect:

```haskell
DijkstraSubTxBodyRaw ::
  { dstbrSpendInputs   :: !(Set TxIn)
  , dstbrOutputs       :: !(StrictSeq (Sized (TxOut era)))
  , dstbrMint          :: !MultiAsset
  , dstbrWithdrawals   :: !Withdrawals
  , dstbrCerts         :: !(OSet.OSet (TxCert era))
  , dstbrVotingProcedures :: !(VotingProcedures era)
  , dstbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
  ...
  }
```

None of this is reachable by the guard script.

---

### Impact Explanation

The guard mechanism's stated purpose is to let a Plutus script authorize sub-transactions. Without sub-transaction `TxInfo`, a guard script:

1. **Cannot restrict minting in sub-transactions.** A guard script designed to allow only sub-transactions that mint ≤ N of a specific policy will always pass, because it cannot observe `txInfoMint` of any sub-transaction. An attacker can embed a sub-transaction that mints arbitrary native assets.

2. **Cannot restrict value flows in sub-transactions.** A guard script designed to prevent sub-transactions from sending funds to unauthorized addresses cannot inspect sub-transaction outputs.

3. **Cannot restrict governance actions in sub-transactions.** A guard script designed to gate sub-transaction governance proposals or votes cannot observe them.

4. **Cannot authenticate the sub-transaction initiator.** A guard script that is supposed to verify the sub-transaction's signatories or required signer hashes cannot do so.

The impact maps to: **Medium — attacker-controlled sub-transactions exceed intended validation limits** (minting, withdrawals, governance actions outside design parameters). In the specific case where a guard script is the sole enforcement mechanism preventing unauthorized native-asset minting inside a sub-transaction, the impact escalates to **Critical — direct creation of native assets through an invalid ledger state transition**.

---

### Likelihood Explanation

The `-- TODO: Include _subTxInfosForGuards` comment confirms this is a known incomplete implementation shipped in production code. Any developer who writes a guard script expecting to inspect sub-transaction content will find their script ineffective. An attacker who reads the ledger source or the Dijkstra specification and discovers this gap can deliberately craft sub-transactions that perform actions the guard script was designed to block. The entry path requires only an unprivileged transaction sender: craft a top-level transaction with a guard script credential in `guards`, embed a sub-transaction with `requiredTopLevelGuards` pointing to that credential, and include sub-transaction actions that the guard script would reject if it could see them.

---

### Recommendation

The `_subTxInfosForGuards` list must be included in the `ScriptContext` delivered to the guard script. The `PV3.TxInfo` (or a Dijkstra-specific extension) passed to a `GuardingPurpose` script should carry the full list of sub-transaction `TxInfo` objects so the guard script can inspect and authenticate each sub-transaction's content before authorizing it. Until this is resolved, guard scripts provide no content-based security guarantees over sub-transactions.

---

### Proof of Concept

The incomplete implementation is directly visible at: [1](#0-0) 

The `_subTxInfosForGuards` binding is computed (lines 509–523) and then discarded at line 524–525 with the comment `-- TODO: Include _subTxInfosForGuards`, returning `topTxInfo` unchanged. The sub-transaction body fields that would be needed by a guard script (minting, outputs, withdrawals, governance) are defined in: [2](#0-1) 

The `requiredTopLevelGuards` mechanism that triggers guard script execution is defined in: [3](#0-2) 

The `GuardingPurpose` script purpose that routes to this incomplete path is defined in: [4](#0-3)

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L1316-1317)
```haskell
  requiredTopLevelGuardsL ::
    Lens' (TxBody SubTx era) (Map (Credential Guard) (StrictMaybe (Data era)))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L544-548)
```haskell
pattern GuardingPurpose ::
  DijkstraEraScript era => f Word32 ScriptHash -> PlutusPurpose f era
pattern GuardingPurpose c <- (toGuardingPurpose -> Just c)
  where
    GuardingPurpose c = mkGuardingPurpose c
```
