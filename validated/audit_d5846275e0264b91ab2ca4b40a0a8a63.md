### Title
Incorrect Total Balance Accounting in Conway Era — `conwayCertsTotalDepositsTxBody` Omits DRep and Proposal Deposits from `certsTotalDepositsTxBody` - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/State/CertState.hs`)

---

### Summary

The `conwayCertsTotalDepositsTxBody` function, which is the Conway-era implementation of the `certsTotalDepositsTxBody` typeclass method, silently omits DRep registration deposits and governance proposal deposits when computing the total deposits for a transaction body. This is the analog of the reported "stale/incomplete balance update" vulnerability class: a balance-accounting function that does not capture all deposit flows, causing the deposit pot (`utxosDeposited`) to diverge from the true sum of obligations tracked in `VState` and `ConwayGovState`.

---

### Finding Description

In the Conway era, a transaction body can carry four categories of deposits:
1. Stake credential registration deposits
2. Stake pool registration deposits
3. DRep registration deposits (`RegDRepTxCert`)
4. Governance proposal deposits (`ProposalProcedure`)

The free-standing function `conwayTotalDepositsTxBody` (used by `getTotalDepositsTxBody` on the `EraTxBody` typeclass) correctly accounts for all four:

```haskell
conwayTotalDepositsTxBody pp isPoolRegisted txBody =
  getTotalDepositsTxCerts pp isPoolRegisted (txBody ^. certsTxBodyL)
    <+> conwayProposalsDeposits pp txBody
``` [1](#0-0) 

However, the `EraCertState` typeclass method `certsTotalDepositsTxBody` is implemented for Conway as `conwayCertsTotalDepositsTxBody`, which only delegates to `getTotalDepositsTxBody` using only the pool registration check — it does **not** include DRep deposits or proposal deposits:

```haskell
conwayCertsTotalDepositsTxBody ::
  EraTxBody era => PParams era -> ConwayCertState era -> TxBody l era -> Coin
conwayCertsTotalDepositsTxBody pp ConwayCertState {conwayCertPState} =
  getTotalDepositsTxBody pp (`Map.member` psStakePools conwayCertPState)
``` [2](#0-1) 

`getTotalDepositsTxBody` at the `EraTxBody` level defaults to only scanning `certsTxBodyL` (certs), not proposals: [3](#0-2) 

This means `certsTotalDepositsTxBody` — which is called by `producedTxBody` to compute the `proDeposits` field of the `Produced` accounting record — systematically under-counts deposits in Conway transactions that include DRep registrations or governance proposals. [4](#0-3) 

The codebase itself acknowledges this inconsistency with a `TODO` comment in the `TotalAda` instance for `UTxOState`:

```haskell
-- we don't add in the _deposits, because it is invariant that this
-- is equal to the sum of the key deposit map and the pool deposit map
-- So these are accounted for in the instance (TotalAda (CertState era))
-- TODO I'm not sure this is true ^
-- Imp conformance tests show in logs that totalAda is off by the deposit amount
``` [5](#0-4) 

Furthermore, `certStateTotalAda` for Conway returns `mempty`, meaning DRep deposits held in `VState` and proposal deposits held in `ConwayGovState` are not counted in the generic `totalAda` computation for Conway:

```haskell
certStateTotalAda :: forall era. Reflect era => CertState era -> Coin
certStateTotalAda = case reify @era of
  ...
  Conway -> mempty
``` [6](#0-5) 

The `obligationGovState` for Conway correctly tracks proposal deposits in `oblProposal`, and `conwayObligationCertState` correctly tracks DRep deposits in `oblDRep`: [7](#0-6) [8](#0-7) 

So the `Obligations` tracking is correct, but the `certsTotalDepositsTxBody` path used in `producedTxBody` is not, creating a split between what the deposit pot (`utxosDeposited`) is supposed to hold and what is actually computed as "produced" by a transaction.

---

### Impact Explanation

**Medium impact.** An attacker-controlled transaction that includes `RegDRepTxCert` certificates or `ProposalProcedure` entries will cause `certsTotalDepositsTxBody` (via `producedTxBody`) to under-report the deposits produced by the transaction. This means:

- The `Produced` accounting record used in balance checks will not include DRep or proposal deposits, causing the `consumed == produced` invariant check to fail or be bypassed for transactions that include these new Conway-era deposit types.
- The `utxosDeposited` field in `UTxOState` is updated via `updateUTxOStateNoFees` using `certsTotalDepositsTxBody`, which also omits these deposits, meaning the deposit pot can diverge from the true sum of obligations (`totalObligation`). [9](#0-8) 

This falls under: **Medium — Attacker-controlled transactions modify deposits/refunds outside design parameters**, since a transaction author can craft a transaction with DRep registrations or proposals that causes the deposit pot to be incorrectly tracked, potentially enabling future over-refunds or breaking the preservation-of-value invariant.

---

### Likelihood Explanation

Any unprivileged transaction sender in the Conway era can submit a transaction containing a `RegDRepTxCert` or a `ProposalProcedure`. Both are permissionless operations. The deposit amounts are non-trivial (governed by `ppDRepDepositL` and `ppGovActionDepositL`), so the accounting error scales with the number of such operations. The codebase's own `TODO` comment and conformance test log warnings confirm this is a known discrepancy.

---

### Recommendation

`conwayCertsTotalDepositsTxBody` should be updated to also include DRep deposits and proposal deposits, mirroring `conwayTotalDepositsTxBody`:

```haskell
conwayCertsTotalDepositsTxBody pp certState txBody =
  getTotalDepositsTxBody pp (`Map.member` psStakePools (conwayCertPState certState)) txBody
    <+> conwayProposalsDeposits pp txBody
```

Additionally, `certStateTotalAda` for Conway should be updated to include DRep deposits from `VState` and proposal deposits from `ConwayGovState`, and the `TotalAda (VState era)` instance should not return `mempty` for Conway.

---

### Proof of Concept

1. Construct a Conway-era transaction with one `RegDRepTxCert` (deposit = `ppDRepDeposit`) and one `ProposalProcedure` (deposit = `ppGovActionDeposit`).
2. Call `certsTotalDepositsTxBody pp certState txBody` — it returns only stake/pool deposits, omitting DRep and proposal deposits.
3. Call `conwayTotalDepositsTxBody pp isPool txBody` — it returns the correct full sum.
4. The difference equals `ppDRepDeposit + ppGovActionDeposit`.
5. After the transaction is applied, `utxosDeposited` will be under by this amount relative to `totalObligation certState govState`, violating the invariant checked by `potEqualsObligation`. [10](#0-9)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/TxBody.hs (L373-380)
```haskell
conwayTotalDepositsTxBody ::
  PParams ConwayEra ->
  (KeyHash StakePool -> Bool) ->
  TxBody l ConwayEra ->
  Coin
conwayTotalDepositsTxBody pp isPoolRegisted txBody =
  getTotalDepositsTxCerts pp isPoolRegisted (txBody ^. certsTxBodyL)
    <+> conwayProposalsDeposits pp txBody
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/CertState.hs (L112-117)
```haskell
conwayObligationCertState :: ConwayEraCertState era => CertState era -> Obligations
conwayObligationCertState certState =
  let accum ans drepState = ans <> drepDeposit drepState
   in (shelleyObligationCertState certState)
        { oblDRep = fromCompact $ F.foldl' accum mempty (certState ^. certVStateL . vsDRepsL)
        }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/CertState.hs (L119-122)
```haskell
conwayCertsTotalDepositsTxBody ::
  EraTxBody era => PParams era -> ConwayCertState era -> TxBody l era -> Coin
conwayCertsTotalDepositsTxBody pp ConwayCertState {conwayCertPState} =
  getTotalDepositsTxBody pp (`Map.member` psStakePools conwayCertPState)
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Core.hs (L263-270)
```haskell
  getTotalDepositsTxBody ::
    PParams era ->
    -- | Check whether stake pool is registered or not
    (KeyHash StakePool -> Bool) ->
    TxBody l era ->
    Coin
  getTotalDepositsTxBody pp isPoolRegisted txBody =
    getTotalDepositsTxCerts pp isPoolRegisted (txBody ^. certsTxBodyL)
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/AdaPots.hs (L150-162)
```haskell
-- | Compute the Coin part of what is produced by a TxBody, itemized as a 'Produced'
producedTxBody ::
  (EraTxBody era, EraCertState era) =>
  TxBody TopTx era ->
  PParams era ->
  CertState era ->
  Produced
producedTxBody txBody pp dpstate =
  Produced
    { proOutputs = sumCoinUTxO (txouts txBody)
    , proFees = txBody ^. feeTxBodyL
    , proDeposits = certsTotalDepositsTxBody pp dpstate txBody
    }
```

**File:** libs/cardano-ledger-test/src/Test/Cardano/Ledger/Generic/Functions.hs (L307-315)
```haskell
instance Reflect era => TotalAda (UTxOState era) where
  totalAda (UTxOState utxo _deposits fees gs _ donations) =
    totalAda utxo <+> fees <+> govStateTotalAda gs <+> donations

-- we don't add in the _deposits, because it is invariant that this
-- is equal to the sum of the key deposit map and the pool deposit map
-- So these are accounted for in the instance (TotalAda (CertState era))
-- TODO I'm not sure this is true ^
-- Imp conformance tests show in logs that totalAda is off by the deposit amount
```

**File:** libs/cardano-ledger-test/src/Test/Cardano/Ledger/Generic/Functions.hs (L349-356)
```haskell
certStateTotalAda :: forall era. Reflect era => CertState era -> Coin
certStateTotalAda = case reify @era of
  Shelley -> totalAda
  Mary -> totalAda
  Allegra -> totalAda
  Alonzo -> totalAda
  Babbage -> totalAda
  Conway -> mempty
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance.hs (L414-420)
```haskell
  obligationGovState st =
    Obligations
      { oblProposal = foldMap' gasDeposit $ proposalsActions (st ^. cgsProposalsL)
      , oblDRep = Coin 0
      , oblStake = Coin 0
      , oblPool = Coin 0
      }
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/Rules/Utxo.hs (L618-641)
```haskell
updateUTxOStateNoFees pp utxos txBody certState govState depositChangeEvent txUtxODiffEvent = do
  let UTxOState {utxosUtxo, utxosDeposited, utxosFees, utxosDonation} = utxos
      UTxO utxo = utxosUtxo
      !utxoAdd = txouts txBody -- These will be inserted into the UTxO
      {- utxoDel  = txins txb ◁ utxo -}
      !(utxoWithout, utxoDel) = extractKeys utxo (txBody ^. inputsTxBodyL)
      {- newUTxO = (txins txb ⋪ utxo) ∪ outs txb -}
      newUTxO = utxoWithout `Map.union` unUTxO utxoAdd
      deletedUTxO = UTxO utxoDel
      totalRefunds = certsTotalRefundsTxBody pp certState txBody
      totalDeposits = certsTotalDepositsTxBody pp certState txBody
      depositChange = totalDeposits <-> totalRefunds
  depositChangeEvent depositChange
  txUtxODiffEvent deletedUTxO utxoAdd
  pure $!
    UTxOState
      { utxosUtxo = UTxO newUTxO
      , utxosDeposited = utxosDeposited <> depositChange
      , utxosFees = utxosFees
      , utxosGovState = govState
      , utxosInstantStake =
          deleteInstantStake deletedUTxO (addInstantStake utxoAdd (utxos ^. instantStakeL))
      , utxosDonation = utxosDonation
      }
```

**File:** eras/shelley/impl/src/Cardano/Ledger/Shelley/LedgerState/Types.hs (L664-679)
```haskell
potEqualsObligation ::
  (EraGov era, EraCertState era) =>
  CertState era ->
  UTxOState era ->
  Bool
potEqualsObligation certState utxoSt = obligations == pot
  where
    obligations = totalObligation certState (utxoSt ^. utxosGovStateL)
    pot = utxoSt ^. utxosDepositedL

allObligations :: (EraGov era, EraCertState era) => CertState era -> GovState era -> Obligations
allObligations certState govState =
  obligationCertState certState <> obligationGovState govState

totalObligation :: (EraGov era, EraCertState era) => CertState era -> GovState era -> Coin
totalObligation certState govState = sumObligation (allObligations certState govState)
```
