### Title
Missing Value Conservation Check in Dijkstra Sub-Transaction Validation Allows ADA Creation - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`)

---

### Summary

The Dijkstra era introduces nested transactions (`SubTx` embedded inside a top-level `TopTx`). The `SUBUTXO` rule that validates sub-transactions omits the `validateValueNotConservedUTxO` check that every normal transaction must pass. The top-level `UTXO` rule's conservation check only covers the top-level transaction body and is blind to sub-transaction inputs and outputs. An unprivileged transaction sender can craft a sub-transaction whose outputs exceed its inputs in value, creating ADA from nothing.

---

### Finding Description

**Vulnerability class:** Funds/accounting bug — missing value conservation invariant in sub-transaction validation.

The analog to the HoneyLocker report is exact: the HoneyLocker `migrate()` function checked the codehash (type identity) but not the critical state values (`unlocked`, `HONEY_QUEEN`, `referral`). Here, the Dijkstra `SUBUTXO` rule checks many structural properties of a sub-transaction (input existence, validity interval, output size, network IDs) but omits the one invariant that prevents ADA creation: value conservation.

**Root cause — `dijkstraSubUtxoTransition` in `SubUtxo.hs`:**

The transition rule runs the following checks: [1](#0-0) 

It validates validity interval, forecast, output size, non-empty inputs, input existence in UTxO, bootstrap attrs, output minimum value, and network IDs. It then calls `updateUTxOStateNoFees` to apply the UTxO delta: [2](#0-1) 

There is no call to `validateValueNotConservedUTxO`. The mapping function that converts top-level UTxO failures to sub-UTxO failures explicitly marks both `FeeTooSmallUTxO` and `ValueNotConservedUTxO` as impossible: [3](#0-2) 

**Root cause — `dijkstraUtxoTransition` in `Utxo.hs`:**

The top-level UTXO rule does check value conservation, but only for the top-level transaction body against `originalUtxo`: [4](#0-3) 

`validateValueNotConservedUTxO` computes `consumed` and `produced` using only `txBody ^. inputsTxBodyL` and `txBody ^. outputsTxBodyL` — the top-level transaction's own inputs and outputs: [5](#0-4) 

Sub-transactions are stored in `txBody ^. subTransactionsTxBodyL`, a completely separate field. They are processed first by `SUBLEDGERS` before the top-level conservation check runs: [6](#0-5) 

The top-level conservation check is therefore blind to any value imbalance introduced by sub-transactions.

**Sub-transaction body has no fee field:**

The `DijkstraSubTxBodyRaw` has no fee field. Required fields for `SSubTx` are only inputs and outputs (no fee at index 2): [7](#0-6) 

This is by design (sub-txs don't pay fees), but the absence of a fee field combined with the absence of a conservation check means there is no mechanism at all to enforce that sub-tx outputs ≤ sub-tx inputs.

---

### Impact Explanation

**Critical. Direct creation of ADA through an invalid ledger state transition.**

Concrete attack:
1. Attacker owns UTxO entry A worth 100 ADA.
2. Attacker submits a Dijkstra top-level transaction with:
   - Top-level inputs: some UTxO entry B (e.g., 10 ADA); top-level outputs: 9 ADA; fee: 1 ADA. (Top-level conservation check passes.)
   - Sub-transaction: inputs = {A (100 ADA)}, outputs = {C (200 ADA)}.
3. `SUBUTXO` validates: A exists in UTxO ✓, C is a valid output ✓. No conservation check.
4. `updateUTxOStateNoFees` removes A and inserts C into the UTxO.
5. After the transaction: the UTxO contains C worth 200 ADA where A worth 100 ADA existed before. 100 ADA has been created from nothing.
6. The top-level conservation check passes because it only sees B → 9 ADA + 1 ADA fee.

The attacker can repeat this to mint arbitrary amounts of ADA, directly violating the Preservation of Value theorem that the ledger is designed to uphold: [8](#0-7) 

---

### Likelihood Explanation

**Medium.** The Dijkstra era (protocol version 12) is not yet active on mainnet at the time of this analysis. Once activated, any unprivileged transaction sender can exploit this with a single crafted transaction. No privileged keys, governance majority, or special access is required. The attack is trivially constructable: the attacker simply sets sub-tx output values higher than sub-tx input values.

---

### Recommendation

Add a value conservation check inside `dijkstraSubUtxoTransition` in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`. Since sub-transactions have no fee field, the conservation equation is:

```
consumed(pp, certState, utxo, subTxBody) == produced_no_fee(pp, certState, subTxBody)
```

where `produced_no_fee` omits the fee term. A new predicate failure `SubValueNotConservedUTxO` should be added to `DijkstraSubUtxoPredFailure` and the check inserted after the input-existence checks, analogous to the check in `dijkstraUtxoTransition`:

```haskell
{- consumed pp utxo txb = produced_no_fee pp certState txb -}
runTest $ validateSubValueNotConservedUTxO pp originalUtxo certState txBody
```

The `dijkstraUtxoToDijkstraSubUtxoPredFailure` mapping should also be updated to handle `ValueNotConservedUTxO` properly rather than marking it as impossible.

---

### Proof of Concept

The following describes a minimal test case (analogous to the HoneyLocker PoC):

```haskell
-- In a Dijkstra ImpTest:
test_subTxValueCreation :: ImpTestM DijkstraEra ()
test_subTxValueCreation = do
  -- Fund an address with 100 ADA
  (_, addr) <- freshKeyAddr
  txIn <- sendCoinTo addr (Coin 100_000_000)

  -- Build a sub-transaction that spends 100 ADA but creates 200 ADA
  let subTx :: Tx SubTx DijkstraEra
      subTx = mkBasicTx mkBasicTxBody
                & bodyTxL . inputsTxBodyL  .~ Set.singleton txIn
                & bodyTxL . outputsTxBodyL .~ StrictSeq.singleton
                    (mkBasicTxOut addr (inject (Coin 200_000_000)))  -- 200 ADA output

  -- Submit top-level tx embedding the sub-tx
  -- (top-level tx has its own balanced inputs/outputs)
  tx <- mkTopLevelTxWithSubTx subTx
  submitTx_ tx  -- Should fail but currently succeeds

  -- Verify 200 ADA now exists at addr (was 100 ADA before)
  balance <- getBalance (addrToCredential addr)
  balance `shouldBe` Coin 200_000_000  -- ADA was created from nothing
```

The `SUBUTXO` rule will accept the sub-transaction because `validateBadInputsUTxO` passes (the input exists) and no conservation check is performed. The top-level transaction's conservation check passes independently. The net result is 100 ADA created from nothing.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L238-263)
```haskell
  runTest $ Allegra.validateOutsideValidityIntervalUTxO slot txBody

  sysSt <- liftSTS $ asks systemStart
  ei <- liftSTS $ asks epochInfo
  runTest $ Alonzo.validateOutsideForecast ei slot sysSt tx

  let allSizedOutputs = txBody ^. allSizedOutputsTxBodyF
  let allOutputs = fmap sizedValue allSizedOutputs
  runTest $ Alonzo.validateOutputTooBigUTxO pp allOutputs

  runTest $ Shelley.validateInputSetEmptyUTxO txBody

  let inputs = txBody ^. inputsTxBodyL
  let refInputs = txBody ^. referenceInputsTxBodyL
  runTest $ Shelley.validateBadInputsUTxO originalUtxo (inputs `Set.union` refInputs)
  runTest $ Shelley.validateBadInputsUTxO (utxosUtxo utxoState) inputs

  runTestOnSignal $ Shelley.validateOutputBootAddrAttrsTooBig allOutputs

  runTestOnSignal $ Babbage.validateOutputTooSmallUTxO pp allSizedOutputs

  netId <- liftSTS $ asks networkId
  runTestOnSignal $ Shelley.validateWrongNetwork netId allOutputs
  runTestOnSignal $ Shelley.validateWrongNetworkWithdrawal netId txBody
  runTestOnSignal $ validateWrongNetworkInDirectDeposit netId txBody
  runTestOnSignal $ Alonzo.validateWrongNetworkInTxBody netId txBody
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L265-278)
```haskell
  if isValid
    then do
      newState <-
        Shelley.updateUTxOStateNoFees
          pp
          utxoState
          txBody
          certState
          (utxosGovState utxoState)
          (tellEvent . TotalDeposits (hashAnnotated txBody))
          (\a b -> tellEvent $ TxUTxODiff a b)
      pure $ newState & utxosDonationL <>~ txBody ^. treasuryDonationTxBodyL
    else
      pure utxoState
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L332-333)
```haskell
  FeeTooSmallUTxO _ -> error "Impossible: `FeeTooSmallUTxO` for SUBUTXO"
  ValueNotConservedUTxO _ -> error "Impossible: `ValueNotConservedUTxO` for SUBUTXO"
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L369-383)
```haskell
  -- Process all subtransactions first
  LedgerState utxoStateAfterSubLedgers certStateAfterSubLedgers <-
    trans @(EraRule "SUBLEDGERS" era) $
      TRC
        ( SubLedgerEnv
            slot
            mbCurEpochNo
            txIx
            pp
            chainAccountState
            originalUtxo
            (tx ^. isValidTxL)
        , ledgerState
        , subStAnnTxs
        )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L452-455)
```haskell
      requiredFields :: STxBothLevels l era -> [(Word, String)]
      requiredFields = \case
        STopTx -> [(0, "inputs"), (1, "outputs"), (2, "fee")]
        SSubTx -> [(0, "inputs"), (1, "outputs")]
```

**File:** eras/shelley/formal-spec/hand_proofs.tex (L68-78)
```tex
\begin{theorem}[Preservation of Value]
  \label{thm:chain-pres-of-value}
  For all environments $e$, blocks $b$, and states $s$, $s'$, if
  \begin{equation*}
    e\vdash s\trans{\hyperref[fig:rules:chain]{chain}}{b}s'
  \end{equation*}
  then
  \begin{equation*}
    \Val(s) = \Val(s')
  \end{equation*}
\end{theorem}
```
