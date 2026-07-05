Looking at the Dijkstra era's `DirectDeposits` feature and its interaction with the value preservation check.

The key files are:
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` — `getConsumedDijkstraValue` / `dijkstraProducedValue`
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs` — `dijkstraEntitiesTransition` / `applyDirectDeposits`
- `libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs` — `applyDirectDeposits`

---

### Title
Direct Deposit Amounts Excluded from Value Preservation Check Allows Unbounded ADA Creation - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

### Summary

The Dijkstra era introduces a `DirectDeposits` field in transaction bodies that credits ADA directly into staking account balances. The total amount of direct deposits is never included in the `produced` side of the ledger's value preservation equation (`consumed = produced`). An unprivileged transaction sender can therefore inflate any registered account balance by an arbitrary amount without funding it from UTxO inputs, creating ADA from nothing.

### Finding Description

**New feature — `DirectDeposits`**

`DirectDeposits` is a `Map AccountAddress Coin` carried in both top-level (`DijkstraTxBodyRaw`) and sub-transaction (`DijkstraSubTxBodyRaw`) bodies. [1](#0-0) [2](#0-1) 

In the `ENTITIES` transition rule, after verifying that every target account is registered (`directDepositsMissingAccounts`), the amounts are unconditionally added to account balances via `applyDirectDeposits`: [3](#0-2) 

`applyDirectDeposits` adds each amount to the matching account's `balanceAccountStateL` with no further checks: [4](#0-3) 

**Value preservation does not account for `DirectDeposits`**

The Dijkstra consumed-value function delegates entirely to `getConsumedMaryValue` (UTxO inputs + withdrawals + refunds + minted assets): [5](#0-4) 

The Dijkstra produced-value function delegates entirely to `conwayProducedValue` (UTxO outputs + fees + deposits + treasury donation + burned assets) plus sub-transaction produced values: [6](#0-5) 

The sub-transaction produced-value function also omits direct deposits: [7](#0-6) 

Neither `consumed` nor `produced` includes the sum of `DirectDeposits`. The preservation check `consumed = produced` therefore passes regardless of what amounts are placed in the `DirectDeposits` field, while account balances silently increase by those amounts.

**Contrast with withdrawals (the correct pattern)**

Withdrawals move ADA *out* of account balances into UTxO outputs. They are correctly included in `consumed` via `wbalance`, so the equation stays balanced. Direct deposits are the mirror operation — ADA should move *into* account balances *from* UTxO inputs — and must therefore appear in `produced`. They do not. [8](#0-7) 

### Impact Explanation

An attacker can create an arbitrary quantity of ADA from nothing:

1. Register a staking credential (standard, costs only the key deposit).
2. Submit a transaction whose `DirectDeposits` field credits, say, 10 billion ADA to that account.
3. The value preservation check passes — direct deposits appear in neither `consumed` nor `produced`.
4. `directDepositsMissingAccounts` passes — the account is registered.
5. The account balance is inflated by 10 billion ADA.
6. A subsequent transaction withdraws the full balance to a UTxO output.

This is a **direct creation of ADA through an invalid ledger state transition**, matching the Critical impact tier: *"Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition."*

### Likelihood Explanation

The attack requires only an unprivileged transaction sender and a registered staking credential — both trivially obtainable. No governance majority, privileged key, or consensus threshold is needed. The exploit is deterministic and repeatable. Likelihood is **high**.

### Recommendation

Include the sum of all `DirectDeposits` amounts in the `produced` side of the value preservation equation, mirroring how `totalDeposits` is handled for certificate deposits. Concretely:

1. Add a helper `directDepositsTotal :: DirectDeposits -> Coin` that sums the map values.
2. In `dijkstraProducedValue`, add `inject (directDepositsTotal (txBody ^. directDepositsTxBodyL))` to the result.
3. Apply the same fix to `dijkstraSubTxProducedValue` for sub-transaction bodies.
4. Add a property-based test asserting that total ADA in UTxO + account balances + fees + deposits is invariant across any Dijkstra transaction that includes `DirectDeposits`.

### Proof of Concept

```
-- Setup
cred  <- freshKeyHash                          -- attacker's staking credential
_     <- submitRegisterStakeCred cred          -- register account (costs keyDeposit)

-- Attack transaction
let directDeposits = DirectDeposits $
      Map.singleton
        (AccountAddress Mainnet (AccountId (KeyHashObj cred)))
        (Coin 10_000_000_000_000_000)          -- 10 billion ADA

tx <- mkBasicTx mkBasicTxBody
        & bodyTxL . directDepositsTxBodyL .~ directDeposits
        -- UTxO inputs cover only the tx fee; no extra ADA provided

submitTx_ tx
-- Passes: consumed = produced (direct deposits absent from both sides)
-- Passes: directDepositsMissingAccounts (account is registered)

-- Account balance is now 10 billion ADA
balance <- getBalance cred
-- balance == Coin 10_000_000_000_000_000

-- Drain in next transaction
submitWithdrawal cred balance   -- moves 10B ADA to UTxO output
```

The root cause is in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` at `dijkstraProducedValue` (line 102) and `dijkstraSubTxProducedValue` (line 258), where `DirectDeposits` is never added to the produced value. [9](#0-8) [10](#0-9)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L185-186)
```haskell
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L206-207)
```haskell
    , dstbrDirectDeposits :: !DirectDeposits
    , dstbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs (L211-216)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
    injectFailure . DirectDepositsToMissingAccounts

  pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs (L290-298)
```haskell
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L78-91)
```haskell
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels
    txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue :: forall m. TxBody m era -> Value era
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
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

**File:** eras/shelley-ma/formal-spec/utxo.tex (L49-57)
```tex
    & \fun{consumed} \in \PParams \to \UTxO \to \TxBody \to \hldiff{\ValMonoid} \\
    & \consumed{pp}{utxo}{txb} = \\
    & ~~\ubalance{(\txins{txb} \restrictdom \var{utxo})} ~+~ \hldiff{\fun{mint}~\var{txb}} \\
    &~~+~\hldiff{\fun{inject}}(\fun{wbalance}~(\fun{txwdrls}~{txb})~+~ \keyRefunds{pp}{txb})
    \nextdef
    & \fun{produced} \in \PParams \to \StakePools \to \TxBody \to \hldiff{\ValMonoid} \\
    & \fun{produced}~\var{pp}~\var{stpools}~\var{txb} = \\
    &~~\ubalance{(\fun{outs}~{txb})} \\
    &~~+ \hldiff{\fun{inject}}(\txfee{txb} + \totalDeposits{pp}{stpools}{(\txcerts{txb})})
```
