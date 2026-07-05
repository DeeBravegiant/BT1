### Title
`totExUnits` Omits Subtransaction ExUnits, Bypassing `maxTxExUnits`/`maxBlockExUnits` and Underpaying Script Fees in Dijkstra Era — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs`)

---

### Summary

The Dijkstra era introduces nested ("sub") transactions embedded inside a top-level transaction body. The `totExUnits` function, which is reused unchanged from Alonzo, only sums ExUnits from the **top-level transaction's** redeemer witnesses. It is blind to the redeemers of every subtransaction. As a result, the `maxTxExUnits` per-transaction limit, the `maxBlockExUnits` per-block limit, and the minimum-fee script-execution charge are all computed against a fraction of the actual script work performed, allowing an unprivileged transaction author to execute arbitrarily more Plutus computation than the protocol intends to permit and to pay proportionally less in fees.

---

### Finding Description

**Root cause — `totExUnits` is top-level-only**

`totExUnits` is defined in `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

It reads only `tx ^. witsTxL` — the witnesses of the single `Tx l era` value passed to it. [1](#0-0) 

**Dijkstra subtransactions carry independent witnesses with their own redeemers**

A Dijkstra top-level transaction body contains an `OMap TxId (Tx SubTx era)` field `dtbrSubTransactions`. Each subtransaction is a full `Tx SubTx era` with its own `witsTxL . rdmrsTxWitsL` redeemer map and its own per-script `ExUnits` budget. [2](#0-1) 

**Per-transaction ExUnits check ignores subtransaction redeemers**

`dijkstraUtxoTransition` enforces the `maxTxExUnits` limit by calling the unmodified Alonzo helper:

```haskell
{- totExunits tx ≤ maxTxExUnits pp -}
runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

`Alonzo.validateExUnitsTooBigUTxO` calls `totExUnits tx`, which only sees the top-level transaction's redeemers. Subtransaction redeemers are never summed. [3](#0-2) 

**Per-block ExUnits check has the same gap**

`dijkstraBbodyTransition` enforces `maxBlockExUnits` with:

```haskell
Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
```

`Alonzo.validateExUnits` computes `foldMap totExUnits txs` — again, only top-level redeemers across all transactions in the block. [4](#0-3) 

**Minimum-fee script charge is also undercounted**

`dijkstraUtxoTransition` validates the fee with:

```haskell
{- minfee pp txTop utxo₀ ≤ txfee txb -}
runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

The `minfee` calculation (inherited from Alonzo/Conway) adds `txscriptfee prices (totExUnits tx)` — again only the top-level redeemers. Subtransaction script fees are not charged. [5](#0-4) 

**Exploit path**

An attacker constructs a Dijkstra top-level transaction whose own redeemer map is empty (or just below `maxTxExUnits`) and embeds N subtransactions, each carrying redeemers that budget up to `maxTxExUnits` ExUnits of Plutus execution. The UTXO rule's `validateExUnitsTooBigUTxO` passes because it only sees the top-level redeemers. The BBODY rule's `validateExUnits` passes for the same reason. The fee check passes because `minfee` charges only for the top-level redeemers. The subtransaction scripts are nonetheless executed by the SubLedger/SubUtxow pipeline, consuming N × (up to `maxTxExUnits`) actual CPU/memory resources while the attacker pays fees for ≈ 0 ExUnits.

---

### Impact Explanation

**Fee underpayment (Medium — fees modified outside design parameters):** The attacker pays script-execution fees proportional only to the top-level transaction's ExUnits. Subtransaction Plutus execution is free. This directly reduces the fee revenue that should flow to the fee pot, modifying fees outside the protocol's design parameters.

**Resource-limit bypass (Medium — transactions exceed intended validation limits):** Both `maxTxExUnits` and `maxBlockExUnits` are bypassed. A single batch transaction can cause nodes to execute an unbounded multiple of the intended per-transaction and per-block Plutus budget, degrading block-validation performance and potentially causing deterministic disagreement if nodes with different resource limits diverge on whether a block is valid.

---

### Likelihood Explanation

The Dijkstra era is the current development era. Any transaction author can craft a top-level transaction with subtransactions; no privileged access is required. The exploit requires only knowledge of the Dijkstra transaction format and the ability to submit a transaction to the network. Likelihood is high once the era is live.

---

### Recommendation

1. **Extend `totExUnits` for Dijkstra** — introduce a Dijkstra-specific override that sums ExUnits across the top-level transaction **and** all subtransactions:

   ```haskell
   dijkstraTotExUnits :: (AlonzoEraTxWits era, DijkstraEraTxBody era)
                      => Tx TopTx era -> ExUnits
   dijkstraTotExUnits tx =
     totExUnits tx
       <> foldMap totExUnits (OMap.elems $ tx ^. bodyTxL . subTransactionsTxBodyL)
   ```

2. **Use the extended function in `dijkstraUtxoTransition`** for the `maxTxExUnits` check and in `dijkstraBbodyTransition` for the `maxBlockExUnits` check.

3. **Use the extended function in the Dijkstra `minfee` calculation** so that script fees correctly reflect the total Plutus work performed by the entire batch.

---

### Proof of Concept

```
Given: maxTxExUnits = ExUnits { mem = 14_000_000, steps = 10_000_000_000 }

Attacker constructs:
  topTx:
    body.subTransactions = [subTx1, subTx2, ..., subTxN]   -- N subtransactions
    wits.redeemers = {}                                      -- 0 ExUnits at top level

  subTxI (for i = 1..N):
    body.spendInputs = { some UTxO locked by alwaysSucceeds }
    wits.redeemers = { spend[0] -> (data, ExUnits 14_000_000 10_000_000_000) }

Validation path:
  dijkstraUtxoTransition:
    totExUnits topTx = ExUnits 0 0          -- only top-level redeemers
    ExUnits 0 0 ≤ maxTxExUnits              -- CHECK PASSES

  dijkstraBbodyTransition:
    foldMap totExUnits [topTx] = ExUnits 0 0
    ExUnits 0 0 ≤ maxBlockExUnits           -- CHECK PASSES

  minfee:
    txscriptfee prices (ExUnits 0 0) = 0    -- no script fee charged

  SubLedger/SubUtxow executes subTx1..subTxN:
    actual Plutus work = N × ExUnits 14_000_000 10_000_000_000
    -- all executed, attacker pays 0 in script fees
```

With N = 10, the attacker executes 10× `maxTxExUnits` of Plutus computation while paying fees for 0 ExUnits of script work.

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L184-184)
```haskell
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L372-373)
```haskell
  {- minfee pp txTop utxo₀ ≤ txfee txb -}
  runTest $ Shelley.validateFeeTooSmallUTxO pp tx originalUtxo
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L361-361)
```haskell
  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
```
