### Title
`DirectDeposits` ADA Not Included in `produced` Value Conservation Check, Enabling ADA Creation — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, a transaction body can carry a `directDepositsTxBodyL` field (`DirectDeposits`) that, when processed by the `ENTITIES` rule, adds ADA directly to registered account balances. However, the `dijkstraProducedValue` function — which defines the "produced" side of the mandatory value-conservation check — never includes the sum of those direct deposits. Because neither `consumed` nor `produced` accounts for direct deposits, the UTXO rule's `consumed == produced` check passes even when the transaction simultaneously places the same ADA in UTxO outputs **and** credits it to account balances, creating ADA out of thin air.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — a value flow that modifies ledger state (account balances) is absent from the `produced` calculation used in the preservation-of-value check.

**Analog to the external report:** The external report shows that `getReward()` mints AURA as a side-effect but the controller's `tokensIn` array never includes AURA, so the balance accounting is wrong. Here, `applyDirectDeposits` credits ADA to account balances as a side-effect of the ENTITIES rule, but `dijkstraProducedValue` never adds the direct-deposit total to `produced`, so the UTXO-level accounting is wrong.

**Step 1 — How `DirectDeposits` are applied.**

In `dijkstraEntitiesTransition`, after certificates are processed, the direct-deposit map is read from the transaction body and applied to account balances:

```haskell
let directDeposits = tx ^. bodyTxL . directDepositsTxBodyL
    accountsAfterCerts = certStateAfterCerts ^. certDStateL . accountsL
failOnJust (directDepositsMissingAccounts directDeposits accountsAfterCerts) $
  injectFailure . DirectDepositsToMissingAccounts
pure $ certStateAfterCerts & certDStateL . accountsL %~ applyDirectDeposits directDeposits
``` [1](#0-0) 

`applyDirectDeposits` unconditionally adds each deposit amount to the matching account's balance:

```haskell
applyDirectDeposits (DirectDeposits dd) =
  updateAccountBalances
    (\amount account -> addCompactCoin amount (account ^. balanceAccountStateL))
    dd
``` [2](#0-1) 

**Step 2 — The `produced` function omits direct deposits.**

`dijkstraProducedValue` delegates to `conwayProducedValue` for the top-level body and to `dijkstraSubTxProducedValue` for sub-transactions. Neither branch adds `directDepositsTxBodyL`:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody          -- outputs + fee + deposits + donation
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)             -- sub-tx produced values
``` [3](#0-2) 

`conwayProducedValue` adds treasury donation but not direct deposits:

```haskell
conwayProducedValue pp isStakePool txBody =
  getProducedMaryValue pp isStakePool txBody
    <+> inject (txBody ^. treasuryDonationTxBodyL)
``` [4](#0-3) 

`dijkstraSubTxProducedValue` also omits direct deposits:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject (getTotalDepositsTxBody pp isRegPoolId txBody <> txBody ^. treasuryDonationTxBodyL)
    <> burnedMultiAssets txBody
``` [5](#0-4) 

**Step 3 — The value-conservation check is therefore blind to direct deposits.**

The UTXO transition enforces:

```haskell
{- consumed pp utxo₀ txb = produced pp certState txb -}
runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
``` [6](#0-5) 

`consumed` (via `conwayConsumed` → `getConsumedMaryValue`) sums UTxO inputs + withdrawals + refunds + minted multi-assets — no direct deposits.
`produced` (via `getProducedDijkstraValue`) sums outputs + fee + cert deposits + treasury donation + sub-tx values — no direct deposits.

The check therefore passes regardless of the direct-deposit total.

**Step 4 — Concrete ADA-creation scenario.**

| Item | Amount |
|---|---|
| UTxO input (Alice) | 1 000 ADA |
| UTxO output (Alice) | 995 ADA |
| Fee | 5 ADA |
| `DirectDeposits` → Bob's account | 1 000 ADA |

- `consumed` = 1 000 ADA; `produced` = 995 + 5 = 1 000 ADA → **check passes**.
- ENTITIES rule: Bob's account balance += 1 000 ADA.
- Post-transaction ledger: Alice holds 995 ADA in UTxO; Bob holds 1 000 ADA in account balance.
- **Total ADA in system: 1 995 ADA (was 1 000 ADA). 1 000 ADA created.**

The same amplification applies to sub-transactions, which also carry `directDepositsTxBodyL` and whose produced value is computed by `dijkstraSubTxProducedValue` — equally missing the direct-deposit term.

---

### Impact Explanation

**Critical. Direct creation of ADA through an invalid ledger state transition.**

An unprivileged transaction author can craft a Dijkstra transaction that simultaneously:
1. Keeps the full input value in UTxO outputs (so the UTXO check passes), and
2. Credits an equal or arbitrary amount to registered account balances via `DirectDeposits`.

The net effect is unbounded ADA inflation. Because account balances contribute to staking power and can be withdrawn into the UTxO via `Withdrawals`, the created ADA is fully spendable. This violates the fundamental preservation-of-value invariant of the Cardano ledger.

---

### Likelihood Explanation

Any holder of a registered stake credential (or anyone who registers one) can exploit this immediately upon Dijkstra era activation. No privileged access, governance majority, or key compromise is required. The only prerequisite is a registered target account address, which the attacker can register themselves. The attack is deterministic and repeatable.

---

### Recommendation

Add the sum of all direct deposits to the `produced` value, mirroring how treasury donations are handled. In `dijkstraProducedValue`:

```haskell
dijkstraProducedValue pp isRegPoolId txBody =
  conwayProducedValue pp isRegPoolId txBody
    <> inject (fold . unDirectDeposits $ txBody ^. directDepositsTxBodyL)   -- ADD THIS
    <> foldMap'
      (getProducedValue pp isRegPoolId . view bodyTxL)
      (txBody ^. subTransactionsTxBodyL)
```

And in `dijkstraSubTxProducedValue`:

```haskell
dijkstraSubTxProducedValue pp isRegPoolId txBody =
  sumAllValue (txBody ^. outputsTxBodyL)
    <> inject ( getTotalDepositsTxBody pp isRegPoolId txBody
             <> txBody ^. treasuryDonationTxBodyL
             <> fold (unDirectDeposits $ txBody ^. directDepositsTxBodyL) )  -- ADD THIS
    <> burnedMultiAssets txBody
```

This forces the transaction author to fund direct deposits from UTxO inputs, exactly as treasury donations must be funded, restoring the preservation-of-value invariant.

---

### Proof of Concept

**Entry path (unprivileged):**

1. Register a stake credential for account `B` (standard `RegTxCert`).
2. Construct a Dijkstra `TopTx` with:
   - `inputs` = `{utxo_A}` (containing `N` ADA)
   - `outputs` = `[addr_A → (N − fee) ADA]`
   - `fee` = `fee` ADA
   - `directDeposits` = `{B → (N − fee) ADA}`
3. Submit the transaction.

**UTXO rule evaluation:**
- `consumed` = `N` (inputs)
- `produced` = `(N − fee)` (outputs) + `fee` = `N`
- `consumed == produced` → **passes** (direct deposits ignored)

**ENTITIES rule evaluation:**
- `applyDirectDeposits` → `balance(B) += (N − fee)`

**Post-state:**
- `addr_A` UTxO: `(N − fee)` ADA
- Account `B` balance: `(N − fee)` ADA
- **ADA created: `(N − fee)` ADA**

Repeat with arbitrarily large `N` to inflate total ADA supply without bound.

**Relevant files:**
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs` — `dijkstraProducedValue` (lines 93–106), `dijkstraSubTxProducedValue` (lines 252–261)
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Entities.hs` — `dijkstraEntitiesTransition` (lines 211–216)
- `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs` — `dijkstraUtxoTransition` value-conservation check (line 381)
- `libs/cardano-ledger-core/src/Cardano/Ledger/State/Account.hs` — `applyDirectDeposits` (lines 290–298)

### Citations

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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/UTxO.hs (L121-131)
```haskell
conwayProducedValue ::
  ( ConwayEraTxBody era
  , Value era ~ MaryValue
  ) =>
  PParams era ->
  (KeyHash StakePool -> Bool) ->
  TxBody TopTx era ->
  Value era
conwayProducedValue pp isStakePool txBody =
  getProducedMaryValue pp isStakePool txBody
    <+> inject (txBody ^. treasuryDonationTxBodyL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L380-381)
```haskell
  {- consumed pp utxo₀ txb = produced pp certState txb -}
  runTest $ Shelley.validateValueNotConservedUTxO pp originalUtxo certState txBody
```
