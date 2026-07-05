### Title
Unbacked Direct Deposits Create ADA From Nothing in Dijkstra Era — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`, `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs`)

---

### Summary

The Dijkstra era introduces a new `directDeposits` transaction body field that adds coin directly to registered account balances. However, the amounts specified in `directDeposits` are absent from both sides of the preservation-of-value (`consumed == produced`) check. Any transaction sender can include an arbitrarily large `directDeposits` map in a Dijkstra transaction, causing the ledger to credit those amounts to account balances without any corresponding deduction from the UTxO. This creates ADA from nothing, violating the fundamental preservation-of-value invariant.

---

### Finding Description

**The `directDeposits` field and how it is applied**

The Dijkstra era adds a `directDeposits` field to the transaction body, defined as a `Map AccountAddress Coin`. [1](#0-0) 

In the `ENTITIES` transition rule, after certificate processing, the ledger applies these deposits directly to account balances: [2](#0-1) 

The `applyDirectDeposits` function unconditionally adds each amount to the matching account's balance: [3](#0-2) 

The only validation performed on `directDeposits` before applying it is a check that the target accounts are registered (`directDepositsMissingAccounts`) and a network-ID check in the UTXO rule: [4](#0-3) 

Neither check verifies that the deposited amounts are backed by UTxO inputs.

**The preservation-of-value check does not include `directDeposits`**

The Dijkstra UTXO rule enforces `consumed == produced` via: [5](#0-4) 

The `consumed` side for Dijkstra is `conwayConsumed`, which delegates to `getConsumedDijkstraValue`: [6](#0-5) 

`getConsumedDijkstraValue` calls `getConsumedMaryValue` for each transaction body, which only sums UTxO inputs, withdrawals, and refunds — no `directDeposits`: [7](#0-6) 

The `produced` side for Dijkstra is `getProducedDijkstraValue`, which calls `conwayProducedValue` (outputs + fees + deposits + treasury donation) and sub-transaction produced values — again, no `directDeposits`: [8](#0-7) 

Because `directDeposits` appears in neither `consumed` nor `produced`, the `consumed == produced` check passes regardless of the amounts placed in `directDeposits`. The ledger then applies those amounts to account balances in the ENTITIES rule, creating ADA that was never deducted from any UTxO entry.

**The same flaw exists in sub-transactions**

The `SUBENTITIES` rule applies the same pattern for sub-transactions: [9](#0-8) 

Sub-transaction produced values also omit `directDeposits`: [10](#0-9) 

---

### Impact Explanation

**Critical. Direct creation of ADA through an invalid ledger state transition.**

An attacker submits a valid Dijkstra transaction (spending at least one UTxO input, paying the minimum fee, satisfying all other checks) with a `directDeposits` map crediting an arbitrary amount — say 45 billion ADA — to their own registered account. The `consumed == produced` check passes because `directDeposits` is absent from both sides. The ENTITIES rule then calls `applyDirectDeposits`, adding the fabricated amount to the account balance. The attacker can immediately withdraw this balance via a subsequent withdrawal transaction, draining the stake pool or any other ADA source that backs withdrawals. Total ADA supply is inflated, breaking the fixed-supply invariant of the protocol.

---

### Likelihood Explanation

Any holder of a registered stake account on the Dijkstra era mainnet can exploit this. No privileged access, governance majority, or key compromise is required. The attacker only needs to:
1. Have a registered account address.
2. Construct a syntactically valid Dijkstra transaction with an arbitrary `directDeposits` entry.
3. Submit it to the network.

The Dijkstra era is the current development-head era in this repository. Once deployed, exploitation is trivially reachable by any unprivileged transaction sender.

---

### Recommendation

Include the total `directDeposits` amount on the **produced** side of the preservation-of-value equation. Specifically, `getProducedDijkstraValue` (and `dijkstraSubTxProducedValue` for sub-transactions) must add `inject (fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)` to the produced value, so that the coin credited to account balances must be explicitly funded by UTxO inputs. This mirrors how withdrawals are handled on the `consumed` side. [8](#0-7) [10](#0-9) 

---

### Proof of Concept

1. Register a stake credential and obtain a valid `AccountAddress` `addr`.
2. Construct a Dijkstra `TopTx` with:
   - One UTxO input worth, e.g., 2 ADA (to cover the fee).
   - One output returning change.
   - `directDeposits = DirectDeposits (Map.singleton addr (Coin 45_000_000_000_000_000))` (45 billion ADA).
   - A valid fee.
3. Submit the transaction. The `validateValueNotConservedUTxO` check passes because `directDeposits` is absent from both `consumed` and `produced`.
4. The ENTITIES rule calls `applyDirectDeposits`, crediting 45 billion ADA to `addr`.
5. Submit a withdrawal transaction claiming the full balance of `addr`. The withdrawal passes `withdrawalsThatExceedAccountBalance` because the balance is now 45 billion ADA.
6. The 45 billion ADA appears in UTxO outputs, having been created from nothing. [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Address.hs (L990-993)
```haskell
-- | Direct deposits to account addresses.
newtype DirectDeposits = DirectDeposits {unDirectDeposits :: Map AccountAddress Coin}
  deriving (Show, Eq, Generic)
  deriving newtype (NoThunks, NFData, EncCBOR, DecCBOR)
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L287-298)
```haskell
-- | Add each direct-deposit amount to the matching account balance.
--
-- /Note/ - There are no checks that direct deposits mention only registered accounts.
applyDirectDeposits ::
  EraAccounts era =>
  DirectDeposits ->
  Accounts era ->
  Accounts era
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L378-381)
```haskell
  runTest $ validateBatchWithdrawals accounts tx

  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L404-406)
```haskell

  {- direct deposit network IDs -}
  runTestOnSignal $ validateWrongNetworkInDirectDeposit netId txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L93-106)
```haskell
dijkstraProducedValue ::
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  MaryValue
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-130)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L252-261)
```haskell
dijkstraSubTxProducedValue ::
  (ConwayEraTxBody era, Value era ~ MaryValue) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody SubTx era ->
  Value era
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
```

**File:** eras/mary/impl/src/Cardano/Ledger/Mary/UTxO.hs (L77-87)
```haskell
getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  consumedValue <> MaryValue mempty mintedMultiAsset
  where
    mintedMultiAsset = filterMultiAsset (\_ _ -> (> 0)) $ txBody ^. mintTxBodyL
    {- balance (txins tx ◁ u) + wbalance (txwdrls tx) + keyRefunds pp tx -}
    consumedValue =
      sumUTxO (txInsFilter utxo (txBody ^. inputsTxBodyL))
        <> inject (refunds <> withdrawals)
    refunds = getTotalRefundsTxBody pp lookupStakingDeposit lookupDRepDeposit txBody
    withdrawals = fold . unWithdrawals $ txBody ^. withdrawalsTxBodyL

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```
