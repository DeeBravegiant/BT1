### Title
Missing Ledger Enforcement of `AccountBalanceIntervals` Constraints in Dijkstra Era ENTITIES Rule — (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs`)

---

### Summary

The Dijkstra era introduces `AccountBalanceIntervals` as a new field in the transaction body, allowing transactions to declare per-account balance constraints (lower bound, upper bound, or both). The data type, serialization, lens accessors, and Plutus-context error guards are all fully implemented. However, the `ENTITIES` transition rule (`dijkstraEntitiesTransition`) never reads or validates this field against the actual ledger account balances. The constraints are silently accepted and ignored, making the feature a no-op from a ledger-enforcement perspective.

---

### Finding Description

**Data structure is fully defined.** `AccountBalanceInterval` and `AccountBalanceIntervals` are declared in `Scripts.hs`:

```haskell
data AccountBalanceInterval era
  = AccountBalanceLowerBound !(Inclusive Coin)
  | AccountBalanceUpperBound !(Exclusive Coin)
  | AccountBalanceBothBounds !(Inclusive Coin) !(Exclusive Coin)

newtype AccountBalanceIntervals era
  = AccountBalanceIntervals
  { unAccountBalanceIntervals :: Map.Map AccountId (AccountBalanceInterval era) }
``` [1](#0-0) 

**The field is present in both top-level and sub-transaction bodies.** Both `DijkstraTxBodyRaw TopTx` and `DijkstraTxBodyRaw SubTx` carry `dtbrAccountBalanceIntervals` / `dstbrAccountBalanceIntervals`: [2](#0-1) 

**Lens accessor is exposed on the typeclass.** `accountBalanceIntervalsTxBodyL` is a required method of `DijkstraEraTxBody`: [3](#0-2) 

**Plutus-context guards exist for V1–V3.** `guardDijkstraFeaturesForPlutusV1toV3` returns `AccountBalanceIntervalsNotSupported` if the field is non-empty when a V1–V3 script is being evaluated, confirming the feature is intended to be active for PlutusV4: [4](#0-3) 

**The ENTITIES transition rule never validates the field.** `dijkstraEntitiesTransition` processes withdrawals, dormant-DRep expiry, certificate transitions, and direct deposits — but never reads `accountBalanceIntervalsTxBodyL` or checks any account balance against the declared intervals: [5](#0-4) 

The CHANGELOG for the Dijkstra era records the addition of the data structures and lens but contains no corresponding entry for ledger-side interval enforcement: [6](#0-5) 

---

### Impact Explanation

**Medium — attacker-controlled transactions exceed intended validation limits.**

`AccountBalanceIntervals` is structurally analogous to `currentTreasuryValue` (a declared value the ledger is expected to verify) rather than to pure script-context metadata. Because the ledger never checks the declared intervals against actual account balances, any transaction may include arbitrary interval claims. A PlutusV4 script that reads `txInfoAccountBalanceIntervals` from the script context and relies on the ledger having pre-verified those bounds before script execution can be tricked: the attacker supplies intervals that appear to satisfy the script's logic while the underlying account balances do not. This allows attacker-controlled transactions to bypass intended balance-range constraints, falling squarely within the "exceed intended validation limits" medium-impact category.

---

### Likelihood Explanation

Any unprivileged transaction sender can craft a `DijkstraTxBody` with a non-empty `accountBalanceIntervals` map containing arbitrary bounds. No special role, key, or governance threshold is required. The omission is reachable on every Dijkstra-era transaction that carries the field.

---

### Recommendation

Add a validation step inside `dijkstraEntitiesTransition` (or a dedicated helper called from it) that, after all certificate and withdrawal processing, iterates over `tx ^. bodyTxL . accountBalanceIntervalsTxBodyL` and for each `(accountId, interval)` pair checks that the current balance of `accountId` in `accountsAfterCerts` satisfies the declared `AccountBalanceInterval`. Introduce a corresponding `PredicateFailure` constructor (e.g., `AccountBalanceOutOfInterval`) in `EntitiesPredFailure` and emit it on violation.

---

### Proof of Concept

1. Construct a Dijkstra-era top-level transaction with:
   ```
   accountBalanceIntervals = { accountX → AccountBalanceLowerBound (Inclusive 1_000_000_000) }
   ```
   where `accountX` currently holds 0 ADA.
2. Submit the transaction. `dijkstraEntitiesTransition` processes withdrawals, certs, and direct deposits without ever reading `accountBalanceIntervalsTxBodyL`.
3. The transaction is accepted by the ledger despite `accountX` violating the declared lower bound.
4. A PlutusV4 script guarding a UTxO that reads `txInfoAccountBalanceIntervals` from its `ScriptContext` and approves spending only when the ledger has confirmed `accountX ≥ 1 ADA` is bypassed, because the ledger never performed that check.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L631-657)
```haskell
data AccountBalanceInterval era
  = AccountBalanceLowerBound !(Inclusive Coin)
  | AccountBalanceUpperBound !(Exclusive Coin)
  | AccountBalanceBothBounds !(Inclusive Coin) !(Exclusive Coin)
  deriving (Generic, Show, Eq, Ord, NoThunks, NFData)

instance EncCBOR (AccountBalanceInterval era) where
  encCBOR = \case
    AccountBalanceLowerBound l -> encodeListLen 2 <> encCBOR l <> encodeNull
    AccountBalanceUpperBound u -> encodeListLen 2 <> encodeNull <> encCBOR u
    AccountBalanceBothBounds l u -> encodeListLen 2 <> encCBOR l <> encCBOR u

instance Typeable era => DecCBOR (AccountBalanceInterval era) where
  decCBOR = do
    enforceSize "AccountBalanceInterval" 2
    lower <- decodeNullMaybe decCBOR
    upper <- decodeNullMaybe decCBOR
    case (lower, upper) of
      (Just l, Just u) -> pure $ AccountBalanceBothBounds l u
      (Just l, Nothing) -> pure $ AccountBalanceLowerBound l
      (Nothing, Just u) -> pure $ AccountBalanceUpperBound u
      _ -> cborError $ DecoderErrorCustom "AccountBalanceInterval" "Both interval bounds cannot be nil."

newtype AccountBalanceIntervals era
  = AccountBalanceIntervals
  {unAccountBalanceIntervals :: Map.Map AccountId (AccountBalanceInterval era)}
  deriving (Generic)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L186-207)
```haskell
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw TopTx era
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
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L1321-1321)
```haskell
  accountBalanceIntervalsTxBodyL :: Lens' (TxBody l era) (AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L431-434)
```haskell
  unless (null $ unAccountBalanceIntervals accountBalanceIntervals) $
    Left $
      inject $
        AccountBalanceIntervalsNotSupported @era accountBalanceIntervals
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L191-216)
```haskell
dijkstraEntitiesTransition = do
  TRC (EntitiesEnv legacyMode certsEnv, certState, certificates) <- judgmentContext
  let Conway.CertsEnv tx pp curEpoch _committee _committeeProposals = certsEnv
      withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
      accounts = certState ^. certDStateL . accountsL

  runTest $ Conway.validateWithdrawalsDelegated accounts tx

  network <- liftSTS $ asks networkId

  validateWithdrawals legacyMode network withdrawals accounts

  let certStateBeforeCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
  certStateAfterCerts <-
    trans @(EraRule "CERTS" era) $ TRC (certsEnv, certStateBeforeCerts, certificates)

  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/CHANGELOG.md (L134-136)
```markdown
* Add `accountBalanceIntervalsTxBodyL` lens to `DijkstraEraTxBody` typeclass.
  - Add the corresponding field to both `TopTx` and `SubTx` levels of `TxBody`.
  - Add `AccountBalanceInterval` and `AccountBalanceIntervals` data types.
```
