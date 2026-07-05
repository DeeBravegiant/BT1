### Title
Deposit Accounting Inconsistency in Dijkstra LEDGER Rule: Sub-Transaction Credential Registration Causes Permanent ADA Deposit Freeze on Parent Deregistration — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs`)

---

### Summary

The Dijkstra era's `dijkstraLedgerTransition` rule processes sub-transactions (via `SUBLEDGERS`) before the parent transaction's `UTXOW` rule, but passes the **pre-sub-transaction** cert state to `UTXOW` for deposit/refund accounting. When a sub-transaction registers a stake credential (paying a deposit) and the parent transaction deregisters that same credential, `certsTotalRefundsTxBody` computes a zero refund because the credential is absent from the original cert state. The deposit is permanently locked in `utxosDeposited` with no mechanism for recovery.

---

### Finding Description

In `dijkstraLedgerTransition`, the execution order is:

1. **SUBLEDGERS** runs first, processing all sub-transactions and producing `utxoStateAfterSubLedgers` and `certStateAfterSubLedgers`.
2. **ENTITIES** runs on `certStateAfterSubLedgers`, processing the parent tx's certificates.
3. **GOV** runs.
4. **UTXOW** runs last.

The critical defect is at line 439, where UTXOW receives `lsCertState ledgerState` — the cert state **before any sub-transaction ran** — as its environment:

```haskell
trans @(EraRule "UTXOW" era) $
  TRC
    ( DijkstraUtxoEnv slot pp (lsCertState ledgerState) originalUtxo
    , utxoStateBeforeUtxow
    , stAnnTx
    )
``` [1](#0-0) 

This cert state is then consumed by `updateUTxOStateNoFees` in the UTXO rule to compute deposit accounting:

```haskell
totalRefunds = certsTotalRefundsTxBody pp certState txBody
totalDeposits = certsTotalDepositsTxBody pp certState txBody
depositChange = totalDeposits <-> totalRefunds
``` [2](#0-1) 

The resulting `depositChange` is applied to `utxosDeposited`:

```haskell
utxosDeposited = utxosDeposited <> depositChange
``` [3](#0-2) 

In Conway (no sub-transactions), passing the pre-CERTS cert state to UTXOW is intentional and correct — the deregistered credentials are still present in it, so their stored deposit amounts are visible for refund computation. The comment in Conway's Ledger.hs explains this design:

```
-- Pass to UTXOW the unmodified CertState in its Environment,
-- so it can process refunds of deposits for deregistering
-- stake credentials and DReps. The modified CertState
-- (certStateAfterCERTS) has these already removed from its
-- AccountState.
``` [4](#0-3) 

In Dijkstra, however, sub-transactions run **before** the parent's ENTITIES rule. The correct cert state to pass to UTXOW is `certStateAfterSubLedgers` (after sub-transactions, before parent's ENTITIES), not `lsCertState ledgerState` (before all sub-transactions). The current code skips over the sub-transaction cert state changes entirely.

The sub-transaction rule itself correctly passes the cert state at the start of each sub-transaction to `SUBUTXOW`:

```haskell
trans @(EraRule "SUBUTXOW" era) $
  TRC
    ( SubUtxoEnv slot pp certState originalUtxo topIsValid
``` [5](#0-4) 

Here `certState` is the state at the start of that specific sub-transaction — correct for sub-tx-level accounting. The parent-level rule fails to apply the analogous logic.

---

### Impact Explanation

**High — Permanent freezing of ADA deposits.**

When a sub-transaction registers a stake credential (paying a deposit into `utxosDeposited`) and the parent transaction deregisters that same credential:

- The parent's ENTITIES rule succeeds because the credential exists in `certStateAfterSubLedgers`.
- The parent's UTXOW uses `lsCertState ledgerState`, which does **not** contain the credential (it was registered by the sub-tx).
- `certsTotalRefundsTxBody` returns zero for that deregistration certificate.
- `utxosDeposited` is not decreased by the deposit amount.
- The credential is removed from `certStateAfterENTITIES` and can never be deregistered again.
- The deposit ADA is permanently locked in `utxosDeposited` with no recovery path short of a hard fork.

This violates the preservation-of-value invariant documented in the Shelley formal spec: the deposit pot must accurately reflect the sum of all active deposits. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The Dijkstra era is experimental and nested transactions are a new feature. Any user who can submit a valid Dijkstra transaction can trigger this path — no privileged access is required. The attacker must control both the sub-transaction (credential registration) and the parent transaction (credential deregistration) for the same credential, which is straightforward since both are authored by the same party. The deposit loss is borne by the transaction author, making this a self-harm scenario that permanently destroys ADA from the circulating supply.

---

### Recommendation

In `dijkstraLedgerTransition`, replace `lsCertState ledgerState` with `certStateAfterSubLedgers` when constructing the `DijkstraUtxoEnv` passed to UTXOW:

```haskell
-- Before (buggy):
( DijkstraUtxoEnv slot pp (lsCertState ledgerState) originalUtxo

-- After (correct):
( DijkstraUtxoEnv slot pp certStateAfterSubLedgers originalUtxo
```

This mirrors the Conway design intent: pass the cert state **before the current transaction's own ENTITIES rule ran** (so deregistered credentials are still present for refund lookup), but **after all prior state changes** (sub-transactions) have been applied. [7](#0-6) 

---

### Proof of Concept

Construct a Dijkstra-era transaction `T` as follows:

1. **Sub-transaction `S`**: Contains a `RegTxCert cred` certificate for a fresh stake credential `cred`. This causes `SUBENTITIES` to register `cred` in `certStateAfterSubLedgers` and adds `keyDeposit` to `utxosDeposited`.

2. **Parent transaction body**: Contains an `UnRegTxCert cred` certificate for the same `cred`, signed by the credential's key.

**Execution trace:**

- `SUBLEDGERS` processes `S`: `cred` is registered, `utxosDeposited += keyDeposit`, `certStateAfterSubLedgers` contains `cred`.
- Parent `ENTITIES` runs on `certStateAfterSubLedgers`: `UnRegTxCert cred` succeeds, `cred` removed from `certStateAfterENTITIES`.
- Parent `UTXOW` runs with `lsCertState ledgerState` (does not contain `cred`):
  - `certsTotalRefundsTxBody pp (lsCertState ledgerState) txBody` → `0` (credential absent).
  - `depositChange = 0 - 0 = 0`.
  - `utxosDeposited` unchanged (still inflated by `keyDeposit`).
- Final state: `cred` is deregistered, but `utxosDeposited` retains the deposit. The `keyDeposit` ADA is permanently frozen. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

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

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L399-407)
```haskell
        certStateAfterENTITIES <-
          trans @(EraRule "ENTITIES" era) $
            TRC
              ( EntitiesEnv
                  (stAnnTx ^. plutusLegacyModeStAnnTxG)
                  (Conway.CertsEnv tx pp curEpochNo committee committeeProposals)
              , certStateAfterSubLedgers
              , StrictSeq.fromStrict $ txBody ^. certsTxBodyL
              )
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/Ledger.hs (L435-443)
```haskell
  -- Call UTXOW with DijkstraUtxoEnv, passing the original UTxO and original certState
  utxoStateFinal <-
    trans @(EraRule "UTXOW" era) $
      TRC
        ( DijkstraUtxoEnv slot pp (lsCertState ledgerState) originalUtxo
        , utxoStateBeforeUtxow
        , stAnnTx
        )
  pure $ LedgerState utxoStateFinal certStateFinal
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

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ledger.hs (L431-436)
```haskell
          -- Pass to UTXOW the unmodified CertState in its Environment,
          -- so it can process refunds of deposits for deregistering
          -- stake credentials and DReps. The modified CertState
          -- (certStateAfterCERTS) has these already removed from its
          -- AccountState.
          ( Shelley.UtxoEnv @era slot pp certState
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Rules/SubLedger.hs (L287-293)
```haskell
  utxoStateAfterSubUtxow <-
    trans @(EraRule "SUBUTXOW" era) $
      TRC
        ( SubUtxoEnv slot pp certState originalUtxo topIsValid
        , utxoStateBeforeSubUtxow
        , stAnnTx
        )
```

**File:** eras/shelley/formal-spec/Properties.md (L10-22)
```markdown
# Preservation of Value

Recall that there are six pots of money in the Shelley ledger:

* Circulation (total value of the UTxO)
* Deposits
* Fees
* Rewards (total value of the reward accounts)
* Reserves
* Treasury

For each transition system, we will list what pots are in scope,
describe how value moves between the pots,
```
