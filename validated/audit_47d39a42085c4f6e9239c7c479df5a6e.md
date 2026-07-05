### Title
`EraPlutusTxInfo 'PlutusV4 DijkstraEra`: `toPlutusScriptPurpose` is a runtime-error stub while PlutusV4 is declared as a supported language — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs`)

---

### Summary

The `EraPlutusTxInfo 'PlutusV4 DijkstraEra` instance declares PlutusV4 as a fully supported language via `mkSupportedLanguage`, but the required `toPlutusScriptPurpose` method is implemented as `error "stub: PlutusV4 not yet implemented"`. Because `toPlutusScriptPurpose` is called unconditionally inside `toPlutusV4Args` (which is `toPlutusArgs` for PlutusV4), any transaction that carries a PlutusV4 script will trigger an uncaught Haskell `ErrorCall` exception during script-context construction, before the script is ever evaluated. This is the direct analog of the reported pattern: a function is registered as reachable but its implementation is absent.

---

### Finding Description

**Root cause — stub method in a live instance:** [1](#0-0) 

```haskell
instance EraPlutusTxInfo 'PlutusV4 DijkstraEra where
  toPlutusTxCert _ _ = pure . transTxCert

  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"
```

**PlutusV4 is simultaneously declared as a supported language:** [2](#0-1) 

```haskell
  mkSupportedLanguage = \case
    PlutusV1 -> Just $ SupportedLanguage SPlutusV1
    PlutusV2 -> Just $ SupportedLanguage SPlutusV2
    PlutusV3 -> Just $ SupportedLanguage SPlutusV3
    PlutusV4 -> Just $ SupportedLanguage SPlutusV4   -- ← PlutusV4 accepted
```

**`toPlutusArgs` for PlutusV4 is wired to `toPlutusV4Args`, which calls `toPlutusScriptPurpose` unconditionally:** [3](#0-2) 

```haskell
  toPlutusArgs = toPlutusV4Args

toPlutusV4Args proxy pv txInfo plutusPurpose maybeSpendingData redeemerData = do
  scriptPurpose <- toPlutusScriptPurpose proxy pv plutusPurpose   -- ← error thrown here
  ...
```

**`toPlutusArgs` is called from `toPlutusWithContext`, the central script-context builder:** [4](#0-3) 

```haskell
toPlutusWithContext script scriptHash plutusPurpose lti@LedgerTxInfo {ltiTx} txInfoResult (redeemerData, exUnits) costModel = do
  ...
  plutusArgs <-
    toPlutusArgs slang (ltiProtVer lti) txInfo plutusPurpose maybeSpendingDatum redeemerData
```

**`mkPlutusWithContext` for DijkstraEra routes PlutusV4 scripts through `toPlutusWithContext`:** [5](#0-4) 

```haskell
  mkPlutusWithContext = \case
    DijkstraPlutusV1 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV2 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV3 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV4 p -> Alonzo.toPlutusWithContext $ Left p   -- ← reaches the stub
```

**The call chain is therefore:**

```
submit tx with PlutusV4 script
  → mkSupportedLanguage PlutusV4 = Just SupportedLanguage   (accepted)
  → mkPlutusWithContext (DijkstraPlutusV4 p)
  → Alonzo.toPlutusWithContext
  → toPlutusArgs SPlutusV4 ...          (= toPlutusV4Args)
  → toPlutusScriptPurpose proxy pv ...  (= error "stub: PlutusV4 not yet implemented")
  → uncaught ErrorCall exception
```

Critically, `error` in Haskell throws an `ErrorCall` synchronous exception that is **not** a `Left` value in the `Either (ContextError era)` monad. It bypasses the normal error-handling path entirely. The `runPlutusScript` / `evaluatePlutusWithContext` pipeline operates on an already-constructed `PlutusWithContext` value; the exception fires during the construction step, before that value is ever produced, so the Plutus evaluation exception handlers are never reached. [6](#0-5) 

---

### Impact Explanation

**Impact: High — Permanent freezing of funds locked in PlutusV4 scripts; potential deterministic ledger-rule disagreement between honest nodes.**

1. Any UTxO locked by a PlutusV4 script in DijkstraEra is permanently unspendable: every attempt to spend it triggers the stub and throws an uncaught exception during context construction.
2. Because the exception escapes the `Either` monad, different node implementations or runtime configurations may handle the `ErrorCall` differently (crash vs. treat as validation failure), producing a deterministic disagreement in ledger-rule evaluation between honest nodes.
3. Recovery requires patching `toPlutusScriptPurpose` and deploying a software update, which in practice requires a hard fork to unfreeze already-locked funds.

---

### Likelihood Explanation

**Likelihood: Medium.**

PlutusV4 is explicitly listed as a supported language in `mkSupportedLanguage` for DijkstraEra. Any script author or toolchain that inspects the supported-language set and writes a PlutusV4 script will encounter this failure. The attacker-controlled entry path is a standard transaction submission; no privileged access is required. The only mitigating factor is that DijkstraEra is a future era not yet deployed on mainnet, but the bug is present in the production codebase and will be live upon deployment.

---

### Recommendation

Replace the stub with a real implementation of `toPlutusScriptPurpose` for `'PlutusV4` in `DijkstraEra`, mirroring the `Conway.transPlutusPurposeV3` pattern used for PlutusV3. Until the implementation is ready, either:

1. Remove `PlutusV4 -> Just $ SupportedLanguage SPlutusV4` from `mkSupportedLanguage` so the language is not advertised as supported, **or**
2. Return a `Left (ContextError …)` value instead of calling `error`, so the failure is handled gracefully within the `Either` monad.

---

### Proof of Concept

1. Deploy DijkstraEra.
2. Lock a UTxO with a PlutusV4 script (any script; the failure occurs before evaluation).
3. Submit a transaction spending that UTxO with a valid redeemer.
4. The ledger calls `mkPlutusWithContext (DijkstraPlutusV4 p)` → `Alonzo.toPlutusWithContext` → `toPlutusArgs SPlutusV4` → `toPlutusV4Args` → `toPlutusScriptPurpose proxy pv plutusPurpose`.
5. `toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"` fires, throwing an uncaught `ErrorCall`.
6. The UTxO remains permanently locked; the node may crash or diverge depending on exception handling.

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L228-233)
```haskell
  mkSupportedLanguage = \case
    PlutusV1 -> Just $ SupportedLanguage SPlutusV1
    PlutusV2 -> Just $ SupportedLanguage SPlutusV2
    PlutusV3 -> Just $ SupportedLanguage SPlutusV3
    PlutusV4 -> Just $ SupportedLanguage SPlutusV4

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L246-251)
```haskell
  mkPlutusWithContext = \case
    DijkstraPlutusV1 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV2 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV3 p -> Alonzo.toPlutusWithContext $ Left p
    DijkstraPlutusV4 p -> Alonzo.toPlutusWithContext $ Left p

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L495-499)
```haskell
instance EraPlutusTxInfo 'PlutusV4 DijkstraEra where
  toPlutusTxCert _ _ = pure . transTxCert

  toPlutusScriptPurpose _ = error "stub: PlutusV4 not yet implemented"

```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L576-591)
```haskell
  toPlutusArgs = toPlutusV4Args

  toPlutusTxInInfo _ = transTxInInfoV3

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
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/TxInfo.hs (L111-119)
```haskell
toPlutusWithContext script scriptHash plutusPurpose lti@LedgerTxInfo {ltiTx} txInfoResult (redeemerData, exUnits) costModel = do
  let slang = isLanguage @l
      maybeSpendingDatum =
        getSpendingDatum (ltiUTxO lti) ltiTx (hoistPlutusPurpose toAsItem plutusPurpose)
  mkTxInfo <- unPlutusTxInfoResult $ lookupTxInfoResult slang txInfoResult
  txInfo <- mkTxInfo $ hoistPlutusPurpose toAsPurpose plutusPurpose
  plutusArgs <-
    toPlutusArgs slang (ltiProtVer lti) txInfo plutusPurpose maybeSpendingDatum redeemerData
  pure $
```

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/Plutus/Evaluate.hs (L385-395)
```haskell
runPlutusScript :: PlutusWithContext -> ScriptResult
runPlutusScript = snd . runPlutusScriptWithLogs

runPlutusScriptWithLogs ::
  PlutusWithContext ->
  ([Text], ScriptResult)
runPlutusScriptWithLogs pwc = toScriptResult <$> evaluatePlutusWithContext P.Quiet pwc
  where
    toScriptResult = \case
      Left evalError -> explainPlutusEvaluationError pwc evalError
      Right _ -> scriptPass pwc
```
