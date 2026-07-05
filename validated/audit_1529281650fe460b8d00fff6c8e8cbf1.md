### Title
Block-Level Execution Unit Budget Bypass via Sub-Transaction Script Execution in Dijkstra Era - (`eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs`)

### Summary

In the Dijkstra era, the block-level execution unit budget check (`maxBlockExUnits`) uses `totExUnits` to sum script execution costs across all transactions in a block. However, `totExUnits` only reads redeemers from the **top-level transaction's witness set** and completely ignores the redeemers of embedded sub-transactions. Since Dijkstra introduces nested transactions (`subTransactionsTxBodyL`), each with their own witness sets and redeemers, an attacker can craft top-level transactions whose sub-transactions collectively consume far more execution units than `maxBlockExUnits` permits, bypassing the intended block-level resource bound.

### Finding Description

The `totExUnits` function is defined in `eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs`:

```haskell
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

This only folds over the redeemers in the top-level transaction's witness set. [1](#0-0) 

The block-body transition rule `dijkstraBbodyTransition` calls:

```haskell
Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
```

which expands to `foldMap totExUnits txs` — summing only top-level transaction execution units across the block. [2](#0-1) 

The underlying `validateExUnits` function confirms this: [3](#0-2) 

In the Dijkstra era, a top-level transaction body carries an `OMap TxId (Tx SubTx era)` field (`dtbrSubTransactions`), and each sub-transaction has its own `TxWits` containing independent redeemers. [4](#0-3) [5](#0-4) 

The per-transaction UTXO check in `dijkstraUtxoTransition` also only validates the top-level transaction's execution units: [6](#0-5) 

Sub-transactions are processed through the `SUBLEDGERS` rule before the top-level UTXO check runs. [7](#0-6)  Even if each sub-transaction is individually bounded by `maxTxExUnits`, the block-level aggregation never includes their costs.

### Impact Explanation

The `maxBlockExUnits` protocol parameter exists to bound the total Plutus script execution work per block, ensuring block validation time is bounded and all honest nodes can keep up with the chain. [8](#0-7) 

Because sub-transaction execution units are invisible to the block-level check, a single block can contain `N` top-level transactions each embedding `M` sub-transactions with scripts budgeted at `maxTxExUnits` each. The actual execution work is `N × M × maxTxExUnits`, while the check observes only the top-level redeemers (potentially zero). This allows a block to exceed the intended computational bound by an arbitrary multiple, causing honest nodes to spend far more time validating a block than the protocol parameters intend.

This matches the **Medium** allowed impact: *"Attacker-controlled transactions... exceed intended validation limits."*

### Likelihood Explanation

Any unprivileged user can submit a top-level Dijkstra transaction containing sub-transactions with Plutus scripts. No special role, key, or governance majority is required. A block producer (even an honest one) including such transactions will inadvertently produce a block that bypasses `maxBlockExUnits`. The attack is fully attacker-controlled at the transaction-submission level and requires no coordination.

### Recommendation

Replace `totExUnits` with a batch-aware variant in the Dijkstra BBODY rule that also folds over sub-transaction redeemers:

```haskell
totBatchExUnits :: (EraTx era, AlonzoEraTxWits era, DijkstraEraTxBody era) => Tx TopTx era -> ExUnits
totBatchExUnits tx =
  totExUnits tx
    <> foldMap totExUnits (tx ^. bodyTxL . subTransactionsTxBodyL)
```

Use this function in `dijkstraBbodyTransition` instead of `Alonzo.validateExUnits`, and similarly update the per-transaction check in `dijkstraUtxoTransition` if the design intent is that the entire batch is bounded by `maxTxExUnits`.

### Proof of Concept

1. Craft a top-level Dijkstra transaction with zero top-level redeemers and `K` sub-transactions, each containing a Plutus script with `ExUnits` set to `maxTxExUnits`.
2. Fill a block with `B` such transactions (bounded only by `maxBlockBodySize`).
3. Submit the block. `dijkstraBbodyTransition` calls `Alonzo.validateExUnits txs (pp ^. ppMaxBlockExUnitsL)`, which computes `foldMap totExUnits txs = mempty` (no top-level redeemers), passing the check trivially.
4. The actual script execution work is `B × K × maxTxExUnits`, which can be orders of magnitude above `maxBlockExUnits`, causing all validating nodes to spend unbounded time on script evaluation for this block.

### Citations

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Tx.hs (L390-394)
```haskell
totExUnits ::
  (EraTx era, AlonzoEraTxWits era) =>
  Tx l era ->
  ExUnits
totExUnits tx = foldMap snd $ tx ^. witsTxL . rdmrsTxWitsL . unRedeemersL
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Bbody.hs (L361-361)
```haskell
  Alonzo.validateExUnits @era txs $ pp ^. ppMaxBlockExUnitsL
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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxBody.hs (L163-188)
```haskell
data DijkstraTxBodyRaw l era where
  DijkstraTxBodyRaw ::
    { dtbrSpendInputs :: !(Set TxIn)
    , dtbrCollateralInputs :: !(Set TxIn)
    , dtbrReferenceInputs :: !(Set TxIn)
    , dtbrOutputs :: !(StrictSeq (Sized (TxOut era)))
    , dtbrCollateralReturn :: !(StrictMaybe (Sized (TxOut era)))
    , dtbrTotalCollateral :: !(StrictMaybe Coin)
    , dtbrCerts :: !(OSet.OSet (TxCert era))
    , dtbrWithdrawals :: !Withdrawals
    , dtbrFee :: !Coin
    , dtbrVldt :: !ValidityInterval
    , dtbrGuards :: !(OSet (Credential Guard))
    , dtbrMint :: !MultiAsset
    , dtbrScriptIntegrityHash :: !(StrictMaybe ScriptIntegrityHash)
    , dtbrAuxDataHash :: !(StrictMaybe TxAuxDataHash)
    , dtbrNetworkId :: !(StrictMaybe Network)
    , dtbrVotingProcedures :: !(VotingProcedures era)
    , dtbrProposalProcedures :: !(OSet.OSet (ProposalProcedure era))
    , dtbrCurrentTreasuryValue :: !(StrictMaybe Coin)
    , dtbrTreasuryDonation :: !Coin
    , dtbrSubTransactions :: !(OMap TxId (Tx SubTx era))
    , dtbrDirectDeposits :: !DirectDeposits
    , dtbrAccountBalanceIntervals :: !(AccountBalanceIntervals era)
    } ->
    DijkstraTxBodyRaw TopTx era
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Tx.hs (L90-95)
```haskell
  DijkstraSubTx ::
    { dstBody :: !(TxBody SubTx era)
    , dstWits :: !(TxWits era)
    , dstAuxData :: !(StrictMaybe (TxAuxData era))
    } ->
    DijkstraTx SubTx era
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Utxo.hs (L414-415)
```haskell
  {- totExunits tx ≤ maxTxExUnits pp -}
  runTest $ Alonzo.validateExUnitsTooBigUTxO pp tx
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

**File:** eras/alonzo/formal-spec/protocol-parameters.tex (L99-102)
```tex
\textbf{Limiting Script Execution Costs.}
The $\var{maxTxExUnits}$ and $\var{maxBlockExUnits}$ protocol parameters are
used to limit the total per-transaction and per-block resource use. These only apply to phase-2 scripts.
The parameters are used to ensure that the time and memory that are required to verify a block are bounded.
```
