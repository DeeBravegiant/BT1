### Title
Direct Deposits Excluded from Preservation-of-Value Check Enables Unbounded ADA Creation — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `directDeposits` field in the transaction body that allows a transaction to credit ADA directly into registered account balances. The `ENTITIES` and `SUBENTITIES` rules faithfully apply these credits. However, `dijkstraProducedValue` — the function used in the UTxO preservation-of-value check — does not include the sum of `directDeposits` on the produced side. An unprivileged transaction sender can therefore construct a transaction that passes the balance check while simultaneously minting arbitrary ADA into account balances, violating the fundamental ADA-preservation invariant.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — invalid state transition due to missing return-value / missing accounting term (direct analog of the unchecked ERC-20 `transferFrom` return value).

**Root cause — `dijkstraProducedValue` omits direct deposits**

`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` defines the produced-value function for the Dijkstra era:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody          -- outputs + fees + cert-deposits
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)             -- sub-tx produced values
``` [1](#0-0) 

`conwayProducedValue` is a Conway-era function that predates `directDeposits`; it has no knowledge of the field. Neither the top-level call nor the sub-transaction fold references `directDepositsTxBodyL`. The `EraUTxO DijkstraEra` instance wires this function in as the canonical `getProducedValue`:

```haskell
instance EraUTxO DijkstraEra where
  consumed          = conwayConsumed
  getConsumedValue  = getConsumedDijkstraValue
  getProducedValue  = getProducedDijkstraValue
``` [2](#0-1) 

**The preservation-of-value check uses this incomplete function**

`dijkstraUtxoTransition` calls the standard Shelley helper:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [3](#0-2) 

`validateValueNotConservedUTxO` computes `consumed` and `produced` and requires equality. Because `produced` omits `directDeposits`, the check is:

```
inputs + withdrawals + refunds  =  outputs + fees + cert_deposits
```

instead of the correct:

```
inputs + withdrawals + refunds  =  outputs + fees + cert_deposits + direct_deposits
``` [4](#0-3) 

**The ENTITIES rule unconditionally applies direct deposits to account balances**

After the UTxO check passes, `dijkstraEntitiesTransition` checks only that target accounts exist, then credits them:

```haskell
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts

pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [5](#0-4) 

`applyDirectDeposits` adds each amount to the matching account balance with no further validation:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
``` [6](#0-5) 

The same pattern is present in `dijkstraSubEntitiesTransition` for sub-transactions: [7](#0-6) 

The only other check on `directDeposits` in the UTxO rule is a network-ID sanity check — it does not constrain amounts: [8](#0-7) 

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker can create an arbitrary amount of ADA in a single transaction. The total ADA supply in the ledger (UTxO + accounts + fees + deposits + treasury + reserves) increases by exactly `sum(directDeposits) - fees`, violating the global preservation-of-value invariant that every Cardano era is required to maintain. [9](#0-8) 

---

### Likelihood Explanation

**High.** The attack requires only the ability to submit a valid Dijkstra-era transaction. No privileged role, governance majority, or key compromise is needed. The attacker must hold enough ADA to pay transaction fees and must have (or register) at least one account address as the direct-deposit target. The exploit is deterministic and repeatable.

---

### Recommendation

Include the sum of all `directDeposits` in the `produced` value. `dijkstraProducedValue` should be extended as follows:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> inject (F.fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)
    <> foldMap'
         (\subTx -> getProducedValue pp isRegPoolId (subTx ^. bodyTxL)
                    <> inject (F.fold . unDirectDeposits $ subTx ^. bodyTxL . directDepositsTxBodyL))
         (txBody ^. subTransactionsTxBodyL)
```

This ensures that any ADA credited to account balances via `directDeposits` must be explicitly "paid for" by the UTxO inputs, restoring the preservation-of-value invariant.

---

### Proof of Concept

**Setup:** Attacker controls a UTxO entry worth `N` ADA and a registered account address `acct`.

**Transaction body:**
| Field | Value |
|---|---|
| `inputs` | UTxO entry worth `N` ADA |
| `outputs` | `N − fee` ADA back to attacker's payment address |
| `fee` | `fee` (minimum valid fee) |
| `directDeposits` | `{ acct → N − fee }` |

**Preservation-of-value check (as currently implemented):**
```
consumed = N
produced = (N − fee) + fee = N   ✓  (check passes)
```

**Actual ledger state change:**
```
UTxO:     −N + (N − fee) = −fee
Accounts: +(N − fee)
Fees:     +fee
Net:      −fee + (N − fee) + fee = +(N − fee)   ← ADA created
```

**Result:** The attacker started with `N` ADA in UTxO and ends with `(N − fee)` ADA in UTxO **plus** `(N − fee)` ADA in their account — a net gain of `N − 2·fee` ADA from nothing. By choosing `N` arbitrarily large, the attacker can inflate the ADA supply without bound.

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L124-131)
```haskell
instance EraUTxO DijkstraEra where
  type ScriptsNeeded DijkstraEra = AlonzoScriptsNeeded DijkstraEra

  consumed = conwayConsumed

  getConsumedValue = getConsumedDijkstraValue

  getProducedValue = getProducedDijkstraValue
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L304-316)
```haskell
validateWrongNetworkInDirectDeposit ::
  DijkstraEraTxBody era =>
  Network ->
  TxBody t era ->
  Test (DijkstraUtxoPredFailure era)
validateWrongNetworkInDirectDeposit netId txb =
  failureOnNonEmptySet depositsWrongNetwork (WrongNetworkInDirectDeposit netId)
  where
    depositsWrongNetwork =
      Map.keysSet $
        Map.filterWithKey
          (\a _ -> aaNetworkId a /= netId)
          (unDirectDeposits $ txb ^. directDepositsTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L506-518)
```haskell
validateValueNotConservedUTxO ::
  (EraUTxO era, EraCertState era) =>
  PParams era ->
  UTxO era ->
  CertState era ->
  TxBody TopTx era ->
  Test (ShelleyUtxoPredFailure era)
validateValueNotConservedUTxO pp utxo certState txBody =
  failureUnless (consumedValue == producedValue) $
    ValueNotConservedUTxO Mismatch {mismatchSupplied = consumedValue, mismatchExpected = producedValue}
  where
    consumedValue = consumed pp certState utxo txBody
    producedValue = produced pp certState txBody
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubEntities.hs (L182-187)
```haskell
  let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
      accountsAfterSubCerts = certStateAfterSubCerts ^. certDStateL . accountsL
  failOnJust (directDepositsMissingAccounts directDeposits accountsAfterSubCerts) $
    injectFailure . SubDirectDepositsToMissingAccounts

  pure $ certStateAfterSubCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
```

**File:** eras/shelley/formal-spec/utxo.tex (L4-12)
```tex
A key constraint that must always be satisfied as a result and precondition of
a valid ledger state transition is called the \textit{general accounting
property}, or the \textit{preservation of value} condition. Every piece of
software that is a part of the implementation of the
Cardano cryptocurrency must function in such a way as to not result in
a violation of this rule.
If this condition is not satisfied, it is an indicator of
incorrect accounting, potentially due to
malicious disruption or a bug.
```
