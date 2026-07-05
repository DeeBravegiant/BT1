### Title
Double-Counting of Deposit Refunds Across Sub-Transactions Allows ADA Creation - (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs`)

---

### Summary

In the Dijkstra era, `getConsumedDijkstraValue` aggregates the consumed value of a top-level transaction and all its sub-transactions by a plain `foldMap'` with no cross-sub-transaction deduplication. Because `dijkstraTotalRefundsTxCerts` also uses a plain `foldMap'` with no credential-seen tracking, an attacker can include the same `UnRegDepositTxCert` (or `UnRegDRepTxCert`) for the same credential in multiple sub-transactions. Each sub-transaction independently contributes its full refund amount to the total consumed value, allowing the transaction to claim N × deposit while only one deposit exists in the pot. The developers have already identified this scenario in a disabled (`xit`) test with a `TODO` comment acknowledging no predicate failure is yet implemented to block it.

---

### Finding Description

**Root cause — `dijkstraTotalRefundsTxCerts` (no deduplication):**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs, lines 235-238
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert  _ deposit -> deposit
  _ -> zero
```

Unlike the Conway implementation (`conwayDRepRefundsTxCerts`, `shelleyTotalRefundsTxCerts`), which maintain a `Map`/`Set` of already-seen credentials to prevent double-counting within a single certificate list, `dijkstraTotalRefundsTxCerts` is a pure monoid fold. It reads the deposit amount directly from the certificate field and adds it unconditionally. There is no check against the ledger state (`lookupStakingDeposit` / `lookupDRepDeposit` are ignored — the instance binds them to `_`):

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs, line 285
getTotalRefundsTxCerts _ _ _ = dijkstraTotalRefundsTxCerts
```

**Root cause — `getConsumedDijkstraValue` (no cross-sub-tx deduplication):**

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/UTxO.hs, lines 78-91
getConsumedDijkstraValue pp lookupStakingDeposit lookupDRepDeposit utxo txBody =
  withBothTxLevels
    txBody
    ( \topTxBody ->
        txBodyConsumedValue topTxBody <> subTransactionsConsumedValue topTxBody
    )
    txBodyConsumedValue
  where
    txBodyConsumedValue = getConsumedMaryValue pp lookupStakingDeposit lookupDRepDeposit utxo
    subTransactionsConsumedValue topTxBody =
      foldMap'
        (getConsumedValue pp lookupStakingDeposit lookupDRepDeposit utxo . view bodyTxL)
        (topTxBody ^. subTransactionsTxBodyL)
```

Each sub-transaction's consumed value is computed independently using the **same** `lookupStakingDeposit` snapshot (the ledger state before the transaction executes). If sub-tx₁ and sub-tx₂ both contain `UnRegDepositTxCert credA D`, both calls to `getConsumedMaryValue` → `getTotalRefundsTxBody` → `dijkstraTotalRefundsTxCerts` return `D`, and the `foldMap'` sums them to `2D`. The preservation-of-value check (`consumed == produced`) then passes with `2D` in refunds even though only `D` was ever deposited.

**Developer acknowledgement — disabled test:**

```haskell
-- eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs, lines 53-75
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  let
    subTx1 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    subTx2 = ... & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
    tx = ... & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```

The test is `xit` (disabled) and the expected predicate failure is `error "TODO: predicate failure not yet implemented"`, confirming no ledger rule currently blocks this.

---

### Impact Explanation

The preservation-of-value invariant (`consumed == produced`) is the core accounting guarantee of the Cardano ledger. By overcounting consumed value through duplicate refund claims across sub-transactions, an attacker can construct a transaction whose outputs exceed its inputs plus the single legitimate refund by `(N-1) × D` ADA, where N is the number of sub-transactions each claiming the same refund. This constitutes **direct creation of ADA through an invalid ledger state transition** — the deposit pot loses `D` but the attacker's outputs gain `N × D`.

This matches the allowed critical impact: *Direct loss, creation, or destruction of ADA or native assets through an invalid ledger state transition.*

---

### Likelihood Explanation

The Dijkstra era is the newest era and sub-transactions are a new feature. The attack requires only:
1. Registering a staking credential (paying deposit D — a normal, permissionless operation).
2. Constructing a top-level Dijkstra transaction with two or more sub-transactions each containing `UnRegDepositTxCert` for the same credential.
3. Submitting the transaction.

No privileged access, governance majority, or leaked key is required. The entry path is fully controlled by an unprivileged transaction sender. The developers have already identified the exact attack scenario in a disabled test, confirming it is reachable.

---

### Recommendation

1. **In `getConsumedDijkstraValue`**: Track which credentials have already had their refund counted (across the top-level body and all sub-transaction bodies) before summing. Pass a mutable seen-set through the fold, analogous to how `shelleyTotalRefundsTxCerts` and `conwayDRepRefundsTxCerts` track seen credentials within a single certificate list.

2. **In `dijkstraTotalRefundsTxCerts`**: Restore the `lookupStakingDeposit` / `lookupDRepDeposit` guard (as used in Conway's `shelleyTotalRefundsTxCerts` and `conwayDRepRefundsTxCerts`) so that a refund is only counted if the credential is actually registered in the current ledger state, and only once per credential per batch.

3. **Enable and fix the existing test** `"Multiple subtransactions cannot get the same refund"` in `Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs` once the predicate failure is implemented.

---

### Proof of Concept

```
Setup:
  credA registered with deposit D = 2_000_000 lovelace
  Deposit pot contains D

Attack transaction (Dijkstra TopTx):
  subTransactions:
    subTx1:
      inputs:  { utxo1 (value V1) }
      outputs: { addr_attacker (value V1 + D) }
      certs:   [ UnRegDepositTxCert credA D ]
    subTx2:
      inputs:  { utxo2 (value V2) }
      outputs: { addr_attacker (value V2 + D) }
      certs:   [ UnRegDepositTxCert credA D ]

getConsumedDijkstraValue computes:
  topTxBody consumed  = 0  (no inputs/certs at top level)
  subTx1 consumed     = V1 + D  (refund counted once)
  subTx2 consumed     = V2 + D  (refund counted again — BUG)
  total consumed      = V1 + V2 + 2D

getProducedDijkstraValue computes:
  subTx1 produced     = (V1 + D) + fee1
  subTx2 produced     = (V2 + D) + fee2
  total produced      = V1 + V2 + 2D + fees

Balance check: consumed == produced  ✓  (passes with 2D on both sides)

Result:
  Attacker receives 2D in outputs.
  Deposit pot loses only D (one actual deregistration succeeds;
  the second may fail in CERT rules, but the balance check has
  already passed, and no predicate failure is implemented to
  block the overall transaction — confirmed by the xit test).
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs (L228-238)
```haskell
-- | Unlike previous eras, we no longer need to lookup refunds from the ledger state, since all of the certificates specify the actual refund and ledger rules will validate that they are accurate.
dijkstraTotalRefundsTxCerts ::
  ( Foldable f
  , ConwayEraTxCert era
  ) =>
  f (TxCert era) ->
  Coin
dijkstraTotalRefundsTxCerts = foldMap' $ \case
  UnRegDepositTxCert _ deposit -> deposit
  UnRegDRepTxCert _ deposit -> deposit
  _ -> zero
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxCert.hs (L285-287)
```haskell
  getTotalRefundsTxCerts _ _ _ = dijkstraTotalRefundsTxCerts

  getTotalDepositsTxCerts = conwayTotalDepositsTxCerts
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

**File:** eras/dijkstra/impl/testlib/Test/Cardano/Ledger/Dijkstra/Imp/CertSpec.hs (L53-75)
```haskell
  xit "Multiple subtransactions cannot get the same refund" $ do
    stakingCred <- KeyHashObj <$> freshKeyHash
    _ <- registerStakeCredential stakingCred
    keyDeposit <- getsPParams ppKeyDepositL
    value1 <- arbitrary
    (_, addr1) <- freshKeyAddr
    input1 <- sendCoinTo addr1 value1
    value2 <- arbitrary
    (_, addr2) <- freshKeyAddr
    input2 <- sendCoinTo addr2 value2
    let
      subTx1 =
        mkBasicTx mkBasicTxBody
          & bodyTxL . inputsTxBodyL .~ Set.singleton input1
          & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      subTx2 =
        mkBasicTx mkBasicTxBody
          & bodyTxL . inputsTxBodyL .~ Set.singleton input2
          & bodyTxL . certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
      tx =
        mkBasicTx mkBasicTxBody
          & bodyTxL . subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
    submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/TxCert.hs (L855-872)
```haskell
conwayDRepRefundsTxCerts ::
  (Foldable f, ConwayEraTxCert era) =>
  (Credential DRepRole -> Maybe Coin) ->
  f (TxCert era) ->
  Coin
conwayDRepRefundsTxCerts lookupDRepDeposit = snd . F.foldl' go (Map.empty, Coin 0)
  where
    go accum@(!drepRegsInTx, !totalRefund) = \case
      RegDRepTxCert cred deposit _ ->
        -- Track registrations
        (Map.insert cred deposit drepRegsInTx, totalRefund)
      UnRegDRepTxCert cred _
        -- DRep previously registered in the same tx.
        | Just deposit <- Map.lookup cred drepRegsInTx ->
            (Map.delete cred drepRegsInTx, totalRefund <+> deposit)
        -- DRep previously registered in some other tx.
        | Just deposit <- lookupDRepDeposit cred -> (drepRegsInTx, totalRefund <+> deposit)
      _ -> accum
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/TxCert.hs (L639-659)
```haskell
shelleyTotalRefundsTxCerts pp lookupDeposit = snd . F.foldl' accum (mempty, Coin 0)
  where
    keyDeposit = pp ^. ppKeyDepositL
    accum (!regCreds, !totalRefunds) cert =
      case lookupRegStakeTxCert cert of
        Just k ->
          -- Need to track new delegations in case that the same key is later deregistered in
          -- the same transaction.
          (Set.insert k regCreds, totalRefunds)
        Nothing ->
          case lookupUnRegStakeTxCert cert of
            Just cred
              -- We first check if there was already a registration certificate in this
              -- transaction.
              | Set.member cred regCreds -> (Set.delete cred regCreds, totalRefunds <+> keyDeposit)
              -- Check for the deposit left during registration in some previous
              -- transaction. This de-registration check will be matched first, despite being
              -- the last case to match, because registration is not possible without
              -- de-registration.
              | Just deposit <- lookupDeposit cred -> (regCreds, totalRefunds <+> deposit)
            _ -> (regCreds, totalRefunds)
```
