### Title
Sub-Transaction Plutus ExUnits Budget Not Validated Against `maxTxExUnits` or `maxBlockExUnits` - (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs`)

---

### Summary

The Dijkstra era introduces sub-transactions (`sub_transactions` field in a top-level `TxBody`). The top-level UTXO rule enforces `maxTxExUnits` only against the top-level transaction's redeemers. The `SUBUTXO` rule — which validates each sub-transaction — contains no ExUnits limit check at all. An unprivileged transaction author can embed sub-transactions carrying arbitrarily large Plutus ExUnits budgets, bypassing both the per-transaction and per-block execution-unit caps.

---

### Finding Description

In `dijkstraUtxoTransition`, the top-level UTXO rule enforces the ExUnits cap:

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

`Alonzo.validateExUnitsTooBigUTxO` calls `totExUnits tx`, which sums ExUnits from `tx ^. witsTxL . rdmrsTxWitsL` — the **top-level** transaction's witness set only. Sub-transactions carry their own independent witness sets with their own redeemers and ExUnits budgets; these are never aggregated into `totExUnits`. [1](#0-0) 

The `SUBUTXO` transition rule (`dijkstraSubUtxoTransition`) processes each sub-transaction through validity-interval, input, output, and network-ID checks, but contains **no** `validateExUnitsTooBigUTxO` call: [2](#0-1) 

The omission is confirmed structurally: the failure-injection mapping for `SUBUTXO` explicitly marks `ExUnitsTooBigUTxO` as impossible:

```haskell
ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
``` [3](#0-2) 

The `SUBLEDGERS` rule folds over all sub-transactions, dispatching each to `SUBLEDGER` → `SUBUTXOW` → `SUBUTXO`, with no aggregated ExUnits check at any level: [4](#0-3) 

The BBODY-level block ExUnits check (`validateExUnits`) also uses `foldMap totExUnits txs` over the sequence of top-level transactions, so it likewise misses sub-transaction ExUnits: [5](#0-4) 

By contrast, the collateral check was correctly extended to aggregate across sub-transactions:

```haskell
hasAnyRedeemers t =
  hasRedeemers t || any hasRedeemers (t ^. bodyTxL . subTransactionsTxBodyL)
``` [6](#0-5) 

This asymmetry — collateral aggregated, ExUnits not — confirms the gap is unintentional.

---

### Impact Explanation

An attacker submits a top-level transaction with zero or minimal ExUnits in its own redeemers (passing `maxTxExUnits`), and embeds N sub-transactions each declaring ExUnits budgets up to `maxTxExUnits`. The total ExUnits executed by validators for that single top-level transaction is N × `maxTxExUnits`, with no protocol-enforced ceiling. The same gap applies at the block level: `maxBlockExUnits` is also bypassed because the BBODY check only sums top-level `totExUnits`.

This matches the allowed impact: **Medium — attacker-controlled transactions exceed intended validation limits** (`maxTxExUnits`, `maxBlockExUnits`).

---

### Likelihood Explanation

Any unprivileged user who can submit a Dijkstra-era transaction can exploit this. No special role, key, or governance threshold is required. The CDDL schema permits an arbitrary number of sub-transactions, each with a full `transaction_witness_set` including redeemers and ExUnits fields. [7](#0-6) 

---

### Recommendation

1. In `dijkstraSubUtxoTransition`, add `runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx` (or a sub-transaction-specific equivalent) so each sub-transaction's ExUnits are individually bounded.
2. In `dijkstraUtxoTransition`, replace the current `totExUnits tx` call with a batch-aware aggregation that sums ExUnits across the top-level transaction **and all its sub-transactions**, then validates the total against `maxTxExUnits`.
3. Ensure the BBODY `validateExUnits` aggregation also accounts for sub-transaction ExUnits when computing the per-block total.

---

### Proof of Concept

```
Top-level tx:
  redeemers: []          -- totExUnits = 0, passes maxTxExUnits check
  sub_transactions: [
    subTx_1: redeemers: [(script_1, ExUnits { mem = maxMem, steps = maxSteps })]
    subTx_2: redeemers: [(script_2, ExUnits { mem = maxMem, steps = maxSteps })]
    ...
    subTx_N: redeemers: [(script_N, ExUnits { mem = maxMem, steps = maxSteps })]
  ]
```

- `validateExUnitsTooBigUTxO pp tx` sees `totExUnits tx = 0` → passes.
- `SUBUTXO` for each `subTx_i` runs no ExUnits check → passes.
- Validators execute N × `maxTxExUnits` worth of Plutus computation for a single accepted transaction, with no ledger-level rejection.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L300-302)
```haskell
    hasAnyRedeemers t =
      hasRedeemers t || any hasRedeemers (t ^. bodyTxL . subTransactionsTxBodyL)
    hasRedeemers = not . null . (^. witsTxL . rdmrsTxWitsL . unRedeemersL)
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L231-278)
```haskell
dijkstraSubUtxoTransition = do
  TRC (SubUtxoEnv slot pp certState originalUtxo (IsValid isValid), utxoState, stAnnTx) <-
    judgmentContext
  let tx = stAnnTx ^. txStAnnTxG

  let txBody = tx ^. bodyTxL

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubUtxo.hs (L340-340)
```haskell
  ExUnitsTooBigUTxO _ -> error "Impossible: `ExUnitsTooBigUTxO` for SUBUTXO"
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedgers.hs (L128-135)
```haskell
dijkstraSubLedgersTransition = do
  TRC (env, ledgerState, subTxs) <- judgmentContext
  foldM
    ( \ls subTx ->
        trans @(EraRule "SUBLEDGER" era) $ TRC (env, ls, subTx)
    )
    ledgerState
    subTxs
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Rules/Bbody.hs (L158-167)
```haskell
validateExUnits txs ppMax =
  let txTotal = foldMap totExUnits txs
   in pointWiseExUnits (<=) txTotal ppMax
        ?! injectFailure
          ( TooManyExUnits $
              Mismatch
                { mismatchSupplied = txTotal
                , mismatchExpected = ppMax
                }
          )
```

**File:** eras/dijkstra/impl/cddl/data/dijkstra.cddl (L783-808)
```text
sub_transactions = nonempty_oset<sub_transaction>

sub_transaction =
  [sub_transaction_body, transaction_witness_set, auxiliary_data/ nil]

sub_transaction_body =
  {   0  : set<transaction_input>
  ,   1  : [* transaction_output]
  , ? 3  : slot
  , ? 4  : certificates
  , ? 5  : withdrawals
  , ? 7  : auxiliary_data_hash
  , ? 8  : slot
  , ? 9  : mint
  , ? 11 : script_data_hash
  , ? 14 : guards
  , ? 15 : network_id
  , ? 18 : nonempty_set<transaction_input>
  , ? 19 : voting_procedures
  , ? 20 : proposal_procedures
  , ? 21 : coin
  , ? 22 : positive_coin
  , ? 24 : required_top_level_guards
  , ? 25 : direct_deposits
  , ? 26 : account_balance_intervals
  }
```
