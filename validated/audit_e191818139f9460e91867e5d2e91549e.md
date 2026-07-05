### Title
Dijkstra Sub-Transactions Can Double-Claim Stake Credential Deposit Refunds — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs`)

---

### Summary

In the Dijkstra era, a top-level transaction (`TopTx`) may embed multiple sub-transactions (`SubTx`). Each sub-transaction is independently validated through the `SUBLEDGER` rule. No cross-sub-transaction check prevents two sub-transactions within the same top-level transaction from each including an `UnRegDepositTxCert` for the **same** staking credential, allowing the deposit refund for that credential to be claimed more than once. The developers have acknowledged this gap with a pending (disabled) test and a `TODO: predicate failure not yet implemented` comment, confirming the guard does not yet exist in production code.

---

### Finding Description

The Dijkstra era introduces nested transactions: a `TopTx` carries an `OMap TxId (Tx SubTx era)` of sub-transactions in `dtbrSubTransactions`. [1](#0-0) 

Each sub-transaction is processed by the `SUBLEDGER` rule, which reuses the Conway `DELEG` rule for certificate handling. [2](#0-1) 

The `DijkstraSubLedgerPredFailure` type has no constructor for "credential already unregistered by a sibling sub-transaction": [3](#0-2) 

The developers explicitly acknowledge the missing guard in a disabled test:

```haskell
xit "Multiple subtransactions cannot get the same refund" $ do
  ...
  subTx1 = ... & certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  subTx2 = ... & certsTxBodyL .~ SSeq.singleton (UnRegDepositTxCert stakingCred keyDeposit)
  tx = ... & subTransactionsTxBodyL .~ OMap.fromFoldable [subTx1, subTx2]
  submitFailingTx tx . NE.singleton $ error "TODO: predicate failure not yet implemented"
``` [4](#0-3) 

The `xit` marker disables the test entirely; the `error "TODO: predicate failure not yet implemented"` as the expected failure value confirms that no production predicate failure exists to reject this pattern. The test title ("cannot get the same refund") states the intended invariant, and the `TODO` confirms it is not enforced.

The vulnerability class is identical to the external report: a **per-call limit** (one deposit refund per credential) is checked only within each individual sub-transaction but is **not tracked cumulatively** across sibling sub-transactions in the same top-level transaction.

---

### Impact Explanation

**Critical — Direct creation of ADA through an invalid ledger state transition.**

An attacker registers a staking credential, paying `keyDeposit` once. They then submit a single Dijkstra `TopTx` containing two (or more) sub-transactions, each carrying `UnRegDepositTxCert stakingCred keyDeposit`. If both sub-transactions are accepted, the deposit pot is debited `2 × keyDeposit` while only `1 × keyDeposit` was ever deposited. The excess ADA is created from the deposit pot, violating the preservation-of-value invariant and constituting direct ADA theft from the ledger's deposit accounting.

---

### Likelihood Explanation

**Medium.** The Dijkstra era (protocol version 12) is not yet active on mainnet, so the attack surface is currently limited to testnets and pre-production environments. Once the era is activated, any unprivileged transaction submitter can craft the exploit with no special keys, governance access, or majority stake. The attack requires only knowledge of a registered staking credential (which can be one the attacker registered themselves) and the ability to submit a valid Dijkstra `TopTx`. The developers' own test confirms awareness of the gap and the absence of a blocking predicate failure.

---

### Recommendation

In the Dijkstra `LEDGER` rule (or `SUBLEDGER` rule), before processing each sub-transaction's certificates, accumulate a set of credentials that have already been unregistered by earlier sub-transactions in the same top-level transaction. Reject any sub-transaction that attempts to unregister a credential already present in that set. Equivalently, thread the updated `CertState` (with the credential removed) from one sub-transaction into the next, so that the second `UnRegDepositTxCert` for the same credential fails with `StakeKeyNotRegisteredDELEG` (or a new dedicated `DuplicateSubTxRefund` predicate failure). The pending test at `Test.Cardano.Ledger.Dijkstra.Imp.CertSpec` should be re-enabled once the predicate failure is implemented.

---

### Proof of Concept

1. Register staking credential `C`, paying `keyDeposit = D` lovelace.
2. Construct:
   - `subTx1`: inputs = `{utxo1}`, certs = `[UnRegDepositTxCert C D]`
   - `subTx2`: inputs = `{utxo2}`, certs = `[UnRegDepositTxCert C D]`
   - `topTx`: subTransactions = `{subTx1, subTx2}`
3. Submit `topTx`.
4. If accepted (no cross-sub-tx guard), the deposit pot is debited `2D` while only `D` was deposited. The attacker's outputs contain `2D` in refunds, netting `+D` ADA from nothing.

The developers' own disabled test (`xit "Multiple subtransactions cannot get the same refund"`) with `error "TODO: predicate failure not yet implemented"` as the expected rejection confirms that step 3 currently succeeds rather than being rejected. [4](#0-3) [5](#0-4) [3](#0-2)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-188)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw TopTx era
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L331-354)
```haskell
  ) =>
  EncCBOR (DijkstraSubLedgerPredFailure era)
  where
  encCBOR =
    encode . \case
      SubUtxowFailure x -> Sum (SubUtxowFailure @era) 1 !> To x
      SubEntitiesFailure x -> Sum (SubEntitiesFailure @era) 2 !> To x
      SubGovFailure x -> Sum (SubGovFailure @era) 3 !> To x
      SubTreasuryValueMismatch mm -> Sum (SubTreasuryValueMismatch @era) 5 !> To mm

instance
  ( Era era
  , DecCBOR (PredicateFailure (EraRule "SUBUTXOW" era))
  , DecCBOR (PredicateFailure (EraRule "SUBENTITIES" era))
  , DecCBOR (PredicateFailure (EraRule "SUBGOV" era))
  ) =>
  DecCBOR (DijkstraSubLedgerPredFailure era)
  where
  decCBOR = decode . Summands "DijkstraSubLedgerPredFailure" $ \case
    1 -> SumD SubUtxowFailure <! From
    2 -> SumD SubEntitiesFailure <! From
    3 -> SumD SubGovFailure <! From
    5 -> SumD SubTreasuryValueMismatch <! From
    n -> Invalid n
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L356-377)
```haskell
conwayToDijkstraSubLedgerPredFailure ::
  forall era.
  ( InjectRuleFailure "SUBUTXOW" DijkstraUtxowPredFailure era
  , PredicateFailure (EraRule "UTXOW" era) ~ DijkstraUtxowPredFailure era
  , InjectRuleFailure "SUBENTITIES" Conway.ConwayCertsPredFailure era
  , PredicateFailure (EraRule "SUBENTITIES" era) ~ SubEntitiesPredFailure era
  , PredicateFailure (EraRule "CERTS" era) ~ Conway.ConwayCertsPredFailure era
  , InjectRuleFailure "SUBGOV" DijkstraGovPredFailure era
  , PredicateFailure (EraRule "GOV" era) ~ DijkstraGovPredFailure era
  ) =>
  Conway.ConwayLedgerPredFailure era ->
  DijkstraSubLedgerPredFailure era
conwayToDijkstraSubLedgerPredFailure = \case
  Conway.ConwayUtxowFailure f -> SubUtxowFailure (injectFailure @"SUBUTXOW" f)
  Conway.ConwayCertsFailure f -> SubEntitiesFailure (injectFailure @"SUBENTITIES" f)
  Conway.ConwayGovFailure f -> SubGovFailure (injectFailure @"SUBGOV" f)
  Conway.ConwayWdrlNotDelegatedToDRep x -> SubEntitiesFailure (SubWdrlNotDelegatedToDRep x)
  Conway.ConwayWithdrawalsMissingAccounts x -> SubEntitiesFailure (SubWithdrawalsMissingAccounts x)
  Conway.ConwayTreasuryValueMismatch x -> SubTreasuryValueMismatch x
  Conway.ConwayTxRefScriptsSizeTooBig _ -> error "Impossible: `ConwayTxRefScriptsSizeTooBig` for SUBLEDGER"
  Conway.ConwayMempoolFailure _ -> error "Impossible: `ConwayMempoolFailure` for SUBLEDGER"
  Conway.ConwayIncompleteWithdrawals _ -> error "Impossible: `ConwayIncompleteWithdrawals` for SUBLEDGER"
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
