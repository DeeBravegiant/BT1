### Title
Stub `toPlutusScriptPurpose` Panics on Any PlutusV4 Script Validation in Dijkstra Era — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`)

---

### Summary

The `EraPlutusTxInfo 'PlutusV4 DijkstraEra` instance implements `toPlutusScriptPurpose` as a hard `error` stub. Because `PlutusV4` is simultaneously declared a fully supported language via `mkSupportedLanguage`, any transaction carrying a PlutusV4 script in the Dijkstra era will reach `toPlutusV4Args`, which calls `toPlutusScriptPurpose`, which throws an impure Haskell exception — bypassing the `Either`-based error handling and crashing the validating node.

---

### Finding Description

In `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`, the `EraPlutusContext DijkstraEra` instance explicitly advertises PlutusV4 as a supported language:

```haskell
mkSupportedLanguage = \case
  PlutusV1 -> Just $ SupportedLanguage SPlutusV1
  PlutusV2 -> Just $ SupportedLanguage SPlutusV2
  PlutusV3 -> Just $ SupportedLanguage SPlutusV3
  PlutusV4 -> Just $ SupportedLanguage SPlutusV4   -- fully supported
``` [1](#0-0) 

The `EraPlutusTxInfo 'PlutusV4 DijkstraEra` instance then wires `toPlutusArgs = toPlutusV4Args`: [2](#0-1) 

`toPlutusV4Args` unconditionally calls `toPlutusScriptPurpose`:

```haskell
toPlutusV4Args proxy pv txInfo plutusPurpose maybeSpendingData redeemerData = do
  scriptPurpose <- toPlutusScriptPurpose proxy pv plutusPurpose   -- always called
  ...
``` [3](#0-2) 

But the implementation of `toPlutusScriptPurpose` for this instance is a stub that throws an impure exception:

```haskell
instance EraPlutusTxInfo 'PlutusV4 DijkstraEra where
  ...
  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"
``` [4](#0-3) 

`toPlutusV4Args` returns `Either (ContextError era) (PlutusArgs 'PlutusV4)`. A pure `Left` would be handled gracefully by the ledger's validation pipeline. However, `error` in Haskell throws an `ErrorCall` impure exception that escapes the `Either` monad entirely, propagating up the call stack uncaught and crashing the node process.

---

### Impact Explanation

**High — Deterministic disagreement between honest nodes from script/witness validation.**

Every honest Dijkstra-era node that attempts to validate a transaction containing a PlutusV4 script will call `toPlutusScriptPurpose` and receive an unhandled `ErrorCall` exception. Because the exception is impure (not a `Left` in the `Either` pipeline), it is not caught by the ledger's normal predicate-failure machinery. The node process crashes or the exception propagates to the block-application layer in an undefined way. Nodes that crash mid-block application may diverge from nodes that handle the exception differently (e.g., via a top-level `catch` that treats it as a block rejection vs. a node restart), producing a deterministic disagreement in ledger state from script validation.

---

### Likelihood Explanation

**Medium.** The Dijkstra era is the current experimental era in this repository and is the intended successor to Conway. `mkSupportedLanguage` explicitly returns `Just (SupportedLanguage SPlutusV4)`, so the ledger will accept and attempt to execute PlutusV4 scripts without any prior guard rejecting them. Any unprivileged transaction author who submits a transaction spending a PlutusV4-locked output (or minting with a PlutusV4 policy) triggers the panic. No special privilege, key, or governance action is required beyond constructing a valid transaction body that references a PlutusV4 script.

---

### Recommendation

Replace the stub with a proper implementation of `toPlutusScriptPurpose` for `'PlutusV4 DijkstraEra`, mirroring the `Conway.transPlutusPurposeV3` pattern used for V3. Until a correct implementation is ready, remove `PlutusV4` from `mkSupportedLanguage` (return `Nothing`) so the ledger rejects PlutusV4 scripts with a clean predicate failure rather than an unhandled exception. This mirrors the fix applied in the referenced zksync-crypto PR: override the unimplemented method with a correct implementation rather than leaving the default stub reachable.

---

### Proof of Concept

1. Construct a Dijkstra-era transaction that spends a UTxO locked by a PlutusV4 script (or mints tokens under a PlutusV4 minting policy).
2. Submit the transaction to a node running the Dijkstra era.
3. The node calls `applyTx` → UTXOW rule → `evalPlutusScripts` → `toPlutusArgs` → `toPlutusV4Args` → `toPlutusScriptPurpose`.
4. `toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"` fires, throwing an `ErrorCall` impure exception.
5. The exception escapes the `Either`-based error handling and crashes or diverges the node.

The root cause is structurally identical to the external report: a typeclass/trait method is declared as supported but its implementation is an unimplemented stub that panics at runtime, reachable by any unprivileged transaction sender. [5](#0-4) [6](#0-5)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L228-232)
```haskell
  mkSupportedLanguage = \case
    PlutusV1 -> Just $ SupportedLanguage SPlutusV1
    PlutusV2 -> Just $ SupportedLanguage SPlutusV2
    PlutusV3 -> Just $ SupportedLanguage SPlutusV3
    PlutusV4 -> Just $ SupportedLanguage SPlutusV4
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L495-499)
```haskell
instance EraPlutusTxInfo 'PlutusV4 DijkstraEra where
  toPlutusTxCert _ _ = pure . transTxCert

  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L576-576)
```haskell
  toPlutusArgs = toPlutusV4Args
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L580-599)
```haskell
toPlutusV4Args ::
  EraPlutusTxInfo 'PlutusV4 era =>
  proxy 'PlutusV4 ->
  ProtVer ->
  PV3.TxInfo ->
  PlutusPurpose AsIxItem era ->
  Maybe (Data era) ->
  Data era ->
  Either (ContextError era) (PlutusArgs 'PlutusV4)
toPlutusV4Args proxy pv txInfo plutusPurpose maybeSpendingData redeemerData = do
  scriptPurpose <- toPlutusScriptPurpose proxy pv plutusPurpose
  let scriptInfo =
        Conway.scriptPurposeToScriptInfo scriptPurpose (transDatum <$> maybeSpendingData)
  pure $
    PlutusV4Args $
      PV3.ScriptContext
        { PV3.scriptContextTxInfo = txInfo
        , PV3.scriptContextRedeemer = Babbage.transRedeemer redeemerData
        , PV3.scriptContextScriptInfo = scriptInfo
        }
```
