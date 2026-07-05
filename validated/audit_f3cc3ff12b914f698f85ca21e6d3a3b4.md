### Title
`DirectDeposits` Amounts Excluded from Preservation-of-Value Check Enables Unbounded ADA Creation — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

The Dijkstra era introduces a `DirectDeposits` field in the transaction body that credits ADA directly to registered staking accounts. However, neither the consumed-value nor the produced-value side of the preservation-of-value (`consumed == produced`) check accounts for these amounts. An unprivileged transaction sender can include `DirectDeposits` entries of arbitrary size and have them applied to account balances without providing the corresponding ADA from UTxO inputs, creating ADA from nothing.

---

### Finding Description

**`DirectDeposits` is a new Dijkstra-era transaction body field** (`dtbrDirectDeposits :: !DirectDeposits`) that maps `AccountAddress` to `Coin`. It is processed by the `ENTITIES` rule, which calls `applyDirectDeposits` to add each amount to the matching account balance. [1](#0-0) 

The only ledger-rule validation performed on `DirectDeposits` is `directDepositsMissingAccounts`, which rejects entries whose target account is not registered. No check is made on the deposit amounts. [1](#0-0) 

`applyDirectDeposits` then unconditionally adds each amount to the account balance via `addCompactCoin`, with no upper-bound guard: [2](#0-1) 

**The critical gap is in the preservation-of-value check.** The `EraUTxO DijkstraEra` instance wires up:

```
consumed = conwayConsumed          -- Conway function; unaware of DirectDeposits
getConsumedValue = getConsumedDijkstraValue
getProducedValue = getProducedDijkstraValue
```

`getConsumedDijkstraValue` delegates to `getConsumedMaryValue` for every tx level: [3](#0-2) 

`getConsumedMaryValue` sums UTxO inputs, withdrawals, and refunds — no `DirectDeposits` term.

`dijkstraProducedValue` (the top-level produced calculation) calls `conwayProducedValue` for the top-level body and `getProducedValue` for sub-transactions: [4](#0-3) 

`conwayProducedValue` is a Conway-era function that sums outputs, fees, deposits, and treasury donations. It has no knowledge of the Dijkstra-specific `DirectDeposits` field, so top-level direct deposits are absent from the produced side as well.

Because `DirectDeposits` appears on neither side of `consumed == produced`, the ledger accepts a transaction that credits arbitrary ADA to accounts while the UTxO balance is unaffected. ADA is created from nothing.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker registers one or more staking accounts, then submits a Dijkstra transaction with `DirectDeposits` entries crediting up to `maxBound :: Word64` lovelace (~1.8 × 10¹⁹, roughly 400× the total ADA supply) per entry. The `ENTITIES` rule accepts the transaction (accounts exist), the UTxO balance check passes (direct deposits are invisible to it), and the accounts receive the credited ADA. The attacker can then withdraw the newly created ADA via normal withdrawal transactions. [5](#0-4) 

---

### Likelihood Explanation

**High.** The attack requires only:
1. A registered staking account (standard operation).
2. A valid Dijkstra-era transaction with a `DirectDeposits` field (no privileged role, no key compromise, no governance majority).

The `Coin` CBOR decoder accepts any `Word64` value, so the deposit amount is only bounded by the 64-bit integer range, not by the actual ADA supply. [5](#0-4) 

---

### Recommendation

Include `DirectDeposits` amounts in the **produced** side of the preservation-of-value calculation in `dijkstraProducedValue` (and `dijkstraSubTxProducedValue` for sub-transactions), analogous to how `treasuryDonationTxBodyL` is included in `conwayProducedValue`. This ensures the ADA credited to accounts must be provided by the transaction's UTxO inputs, preserving the total ADA supply invariant. [4](#0-3) 

---

### Proof of Concept

```
1. Register staking credential C → account address A.
2. Craft a DijkstraEra transaction T:
     inputs  = { some UTxO covering only the fee }
     outputs = { change output }
     fee     = minimum fee
     directDeposits = { A → Coin 45_000_000_000_000_000 }   -- 45B ADA
3. Submit T.
   - ENTITIES rule: A is registered → passes directDepositsMissingAccounts.
   - UTXO rule: consumed (inputs + 0 withdrawals) == produced (outputs + fee)
               → passes, because DirectDeposits is absent from both sides.
   - applyDirectDeposits credits 45B ADA to account A.
4. Account A now holds 45B ADA that was never deducted from any UTxO entry.
5. Attacker withdraws via a standard withdrawal transaction.
```

Root cause chain:
- `dijkstraEntitiesTransition` → `directDepositsMissingAccounts` (existence only, no amount check) [1](#0-0) 
- `applyDirectDeposits` → `addCompactCoin` (no overflow guard, no limit) [2](#0-1) 
- `dijkstraProducedValue` → `conwayProducedValue` (no `DirectDeposits` term) [4](#0-3)

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs (L65-91)
```haskell
getConsumedDijkstraValue ::
  forall era l.
  ( DijkstraEraTxBody era
  , EraUTxO era
  , Value era ~ MaryValue
  , STxLevel l era ~ STxBothLevels l era
  ) =>
  PParams era ->
  (Credential Staking -> Maybe Coin) ->
  (Credential DRepRole -> Maybe Coin) ->
  UTxO era ->
  TxBody l era ->
  Value era
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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Coin.hs (L108-113)
```haskell
instance FromCBOR Coin where
  fromCBOR = Coin . toInteger <$> Plain.decodeWord64

instance DecCBOR Coin where
  decCBOR = fromPlainDecoder fromCBOR
  {-# INLINE decCBOR #-}
```
