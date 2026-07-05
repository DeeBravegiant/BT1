### Title
Missing Enforcement of `AccountBalanceIntervals` Constraints in Dijkstra Era Ledger Rules - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

The Dijkstra era introduces `AccountBalanceIntervals` as a new transaction body field that allows specifying balance constraints (lower bound, upper bound, or both) for accounts. However, no ledger rule in the Dijkstra era validates these constraints against actual account balances. Any transaction including `AccountBalanceIntervals` is accepted by the ledger regardless of whether the specified balance conditions are met, rendering the feature entirely non-functional as a safety mechanism.

---

### Finding Description

**Vulnerability class:** Missing parameter validation / missing enforcement of required state check — the direct analog to the external report's `min_amount_out` not being validated against vault state.

**Root cause:**

`AccountBalanceInterval` and `AccountBalanceIntervals` are defined in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`:

```haskell
data AccountBalanceInterval era
  = AccountBalanceLowerBound !(Inclusive Coin)
  | AccountBalanceUpperBound !(Exclusive Coin)
  | AccountBalanceBothBounds !(Inclusive Coin) !(Exclusive Coin)
``` [1](#0-0) 

The field is included in the Dijkstra transaction body via `accountBalanceIntervalsTxBodyL` and is part of the CDDL wire format:

```
account_balance_intervals = {+ credential => account_balance_interval}
``` [2](#0-1) 

The `dijkstraUtxoTransition` function in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` performs many validation checks — `validateBatchWithdrawals`, `validateBatchCollateral`, `validateWrongNetworkInDirectDeposit`, fee checks, size checks, etc. — but **never validates `AccountBalanceIntervals`**: [3](#0-2) 

Similarly, `dijkstraEntitiesTransition` validates withdrawals and direct deposits but contains no check for `AccountBalanceIntervals`: [4](#0-3) 

And `dijkstraSubEntitiesTransition` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs` also performs no such check: [5](#0-4) 

The **only** production-code reference to `AccountBalanceIntervals` outside of `TxBody.hs` is in `TxInfo.hs`, where it is blocked from being passed to Plutus V1–V3 scripts (throwing `AccountBalanceIntervalsNotSupported`) — but this is a Plutus context-building guard, not a ledger-state validation: [6](#0-5) 

A `grep` search across all production source files for any function matching `validateAccountBalance`, `checkAccountBalance`, `AccountBalance.*valid`, or `validateBalance` returns **zero matches**.

---

### Impact Explanation

**Impact: Medium** — Attacker-controlled transactions exceed intended validation limits.

`AccountBalanceIntervals` is designed to allow a transaction author (or script) to assert that certain accounts hold balances within specified ranges at the time the transaction is processed. This is the Dijkstra-era analog of `min_amount_out`: a caller-supplied bound that the ledger is supposed to enforce against live state.

Because the ledger never checks these intervals, any transaction that includes `AccountBalanceIntervals` constraints is accepted unconditionally. A transaction that should be rejected — because an account's balance falls outside the specified interval — proceeds to state application. This means:

1. Any safety invariant a transaction author encodes via `AccountBalanceIntervals` (e.g., "only execute if account X has at least N ADA") is silently bypassed.
2. A malicious block producer can include transactions whose balance-interval preconditions are not satisfied, causing the ledger to accept state transitions that the transaction author intended to be conditional.
3. The `AccountBalanceBothBounds` variant additionally accepts logically impossible intervals (e.g., `lower ≥ upper`) at the decoder level with no rejection, since no downstream validation exists. [7](#0-6) 

---

### Likelihood Explanation

**Likelihood: High** — The attack path requires only submitting a valid Dijkstra transaction with a non-empty `accountBalanceIntervalsTxBodyL`. No special privilege, key, or governance majority is needed. The field is freely settable by any transaction author. The missing check is in the core UTXO/ENTITIES transition rules that every Dijkstra transaction passes through.

---

### Recommendation

Add a validation function — analogous to `validateBatchWithdrawals` — that iterates over `accountBalanceIntervalsTxBodyL` for both the top-level transaction and all sub-transactions, looks up each `AccountId`'s current balance from `certState ^. certDStateL . accountsL`, and rejects the transaction if any balance falls outside its specified interval. This check should be called in `dijkstraUtxoTransition` (using the original pre-batch `accounts`) and a corresponding predicate failure variant should be added to `DijkstraUtxoPredFailure`. [8](#0-7) 

---

### Proof of Concept

```haskell
-- Account `cred` has balance 0 ADA.
-- Transaction specifies a lower-bound interval of 1,000,000 ADA for that account.
let tx =
      mkBasicTx $
        mkBasicTxBody
          & accountBalanceIntervalsTxBodyL
              .~ AccountBalanceIntervals
                   (Map.singleton
                     (AccountId cred)
                     (AccountBalanceLowerBound (Inclusive (Coin 1_000_000))))

-- Expected: transaction rejected with an AccountBalanceIntervalViolation failure.
-- Actual:   transaction accepted; the interval constraint is never checked.
submitTx_ tx
```

The `dijkstraUtxoTransition` rule processes the transaction, runs all its checks (fee, size, collateral, batch withdrawals, network IDs, etc.), and returns successfully without ever reading `accountBalanceIntervalsTxBodyL` for validation purposes. [3](#0-2) [9](#0-8)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L631-635)
```haskell
data AccountBalanceInterval era
  = AccountBalanceLowerBound !(Inclusive Coin)
  | AccountBalanceUpperBound !(Exclusive Coin)
  | AccountBalanceBothBounds !(Inclusive Coin) !(Exclusive Coin)
  deriving (Generic, Show, Eq, Ord, NoThunks, NFData)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L643-652)
```haskell
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
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L654-657)
```haskell
newtype AccountBalanceIntervals era
  = AccountBalanceIntervals
  {unAccountBalanceIntervals :: Map.Map AccountId (AccountBalanceInterval era)}
  deriving (Generic)
```

**File:** eras/dijkstra/impl/cddl/data/dijkstra.cddl (L814-819)
```text
account_balance_intervals = {+ credential => account_balance_interval}

account_balance_interval =
  [  inclusive_lower_bound : coin, exclusive_upper_bound : coin/ nil
  // inclusive_lower_bound : coin/ nil, exclusive_upper_bound : coin
  ]
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L249-280)
```haskell
-- | For each account, the total withdrawals across the entire batch should not exceed the original account balance.
-- Unregistered accounts are treated as having 0 balance.
validateBatchWithdrawals ::
  ( EraTx era
  , EraAccounts era
  , DijkstraEraTxBody era
  ) =>
  Accounts era ->
  Tx TopTx era ->
  Test (DijkstraUtxoPredFailure era)
validateBatchWithdrawals accounts tx =
  let allWithdrawals =
        Map.unionsWith (<>) $
          unWithdrawals (tx ^. bodyTxL . withdrawalsTxBodyL)
            : [ unWithdrawals $ subTx ^. bodyTxL . withdrawalsTxBodyL
              | subTx <- OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL
              ]
      badWithdrawals =
        Map.mapMaybeWithKey
          ( \acctAddr withdrawn ->
              let balance = getAccountBalance acctAddr
               in if withdrawn > balance
                    then Just Mismatch {mismatchSupplied = withdrawn, mismatchExpected = balance}
                    else Nothing
          )
          allWithdrawals
   in failureOnNonEmptyMap badWithdrawals WithdrawalsExceedAccountBalance
  where
    getAccountBalance (AccountAddress _ (AccountId cred)) =
      case lookupAccountState cred accounts of
        Nothing -> mempty -- unregistered account, 0 balance
        Just accountState -> fromCompact $ accountState ^. balanceAccountStateL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L343-427)
```haskell
dijkstraUtxoTransition = do
  TRC (DijkstraUtxoEnv slot pp certState originalUtxo, utxos, stAnnTx) <-
    judgmentContext
  let tx = stAnnTx ^. txStAnnTxG
  -- this is the original Accounts, before any transactions were applied
  let accounts = certState ^. certDStateL . accountsL

  let txBody = tx ^. bodyTxL

  {- inInterval (SlotOf Γ) (ValidIntervalOf txTop) -}
  runTest $ Allegra.validateOutsideValidityIntervalUTxO slot txBody

  sysSt <- liftSTS $ asks systemStart
  ei <- liftSTS $ asks epochInfo

  runTest $ Alonzo.validateOutsideForecast ei slot sysSt tx

  {- SpendInputs ≠ ∅ -}
  runTestOnSignal $ Shelley.validateInputSetEmptyUTxO txBody

  let allInputs = txBody ^. allInputsTxBodyF
      inputs = txBody ^. inputsTxBodyL

  {- SpendInputsOf txTop ∪ RefInputsOf txTop ∪ CollInputsOf txTop ⊆ dom(utxo₀) -}
  runTest $ Shelley.validateBadInputsUTxO originalUtxo allInputs

  {- SpendInputsOf txTop ⊆ dom(utxo_s) — prevents double-spend with subtxs -}
  runTest $ Shelley.validateBadInputsUTxO (utxosUtxo utxos) inputs

  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo

  {- (RedeemersOf txTop ≠ ∅ ⊎ Any (λ txSub → RedeemersOf txSub ≠ ∅) subtxs) → collateralCheck -}
  validate $ validateBatchCollateral pp tx originalUtxo

  runTest $ validateBatchWithdrawals accounts tx

  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody

  {- ∀ txout ∈ allOuts txb, getValue txout ≥ inject (serSize txout * coinsPerUTxOByte pp) -}
  let allSizedOutputs = txBody ^. allSizedOutputsTxBodyF
  runTest $ Babbage.validateOutputTooSmallUTxO pp allSizedOutputs

  let allOutputs = fmap sizedValue allSizedOutputs
  {- ∀ txout ∈ allOuts txb, serSize (getValue txout) ≤ maxValSize pp -}
  runTest $ Alonzo.validateOutputTooBigUTxO pp allOutputs

  {- ∀ ( _ ↦ (a,_)) ∈ allOuts txb, a ∈ Addrbootstrap → bootstrapAttrsSize a ≤ 64 -}
  runTestOnSignal $ Shelley.validateOutputBootAddrAttrsTooBig allOutputs

  netId <- liftSTS $ asks networkId

  {- ∀(_ → (a, _)) ∈ allOuts txb, netId a = NetworkId -}
  runTestOnSignal $ Shelley.validateWrongNetwork netId allOutputs

  {- ∀(a → ) ∈ txwdrls txb, netId a = NetworkId -}
  runTestOnSignal $ Shelley.validateWrongNetworkWithdrawal netId txBody

  {- (txnetworkid txb = NetworkId) ∨ (txnetworkid txb = ◇) -}
  runTestOnSignal $ Alonzo.validateWrongNetworkInTxBody netId txBody

  {- direct deposit network IDs -}
  runTestOnSignal $ validateWrongNetworkInDirectDeposit netId txBody

  {- no Ptr in collateral return -}
  validateNoPtrInCollateralReturn txBody

  {- txsize tx ≤ maxTxSize pp -}
  runTestOnSignal $ Shelley.validateMaxTxSizeUTxO pp tx

  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx

  {- ‖collateral tx‖ ≤ maxCollInputs pp -}
  runTest $ Alonzo.validateTooManyCollateralInputs pp txBody

  () <- trans @(EraRule "UTXOS" era) $ TRC ((), (), stAnnTx)
  Babbage.updateUTxOStateByTxValidity
    pp
    certState
    (utxosGovState utxos)
    tx
    (Conway.updateTreasuryDonation tx utxos)

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L155-187)
```haskell
dijkstraSubEntitiesTransition = do
  TRC (subCertsEnv, certState, certificates) <- judgmentContext
  let tx = certsTx subCertsEnv
      pp = certsPParams subCertsEnv
      curEpoch = certsCurrentEpoch subCertsEnv
      withdrawals = tx ^. bodyTxL . withdrawalsTxBodyL
      accounts = certState ^. certDStateL . accountsL

  runTest $ Conway.validateWithdrawalsDelegated accounts tx

  network <- liftSTS $ asks networkId
  let (missingWithdrawals, exceededWithdrawals) =
        case withdrawalsThatExceedAccountBalance withdrawals network accounts of
          Nothing -> (Map.empty, Map.empty)
          Just (missing, exceeded) -> (unWithdrawals missing, exceeded)
  failOnNonEmptyMap missingWithdrawals $
    injectFailure . SubWithdrawalsMissingAccounts . Withdrawals . NEM.toMap
  failOnNonEmptyMap exceededWithdrawals $ injectFailure . SubWithdrawalAmountsExceedAccountBalances

  let certStateBeforeSubCerts =
        certState
          & Conway.updateDormantDRepExpiries tx curEpoch
          & Conway.updateVotingDRepExpiries tx curEpoch (pp ^. ppDRepActivityL)
          & certDStateL . accountsL %~ applyWithdrawals withdrawals
  certStateAfterSubCerts <-
    trans @(EraRule "SUBCERTS" era) $ TRC (subCertsEnv, certStateBeforeSubCerts, certificates)

  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L431-434)
```haskell
  unless (null $ unAccountBalanceIntervals accountBalanceIntervals) $
    Left $
      inject $
        AccountBalanceIntervalsNotSupported @era accountBalanceIntervals
```
