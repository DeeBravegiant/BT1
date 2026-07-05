### Title
`eraMaxLanguage` Set to `PlutusV3` Instead of `PlutusV4` in `DijkstraEra`, Causing PlutusV4 Scripts to Be Excluded from Execution — (`File: eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`)

---

### Summary

The Dijkstra era introduces `PlutusV4` as a new Plutus script language. The data type, serialization, `MemPack`, `TxInfo`, and `mkSupportedLanguage` all fully implement `PlutusV4`. However, the `AlonzoEraScript DijkstraEra` instance sets `eraMaxLanguage = PlutusV3` instead of `PlutusV4`. This is the exact analog of the EIP-2981 bug: the capability is implemented but not registered in the detection/dispatch mechanism (`eraMaxLanguage`), so the rest of the ledger does not recognize PlutusV4 as a supported language. Any UTxO locked by a PlutusV4 script becomes permanently unspendable.

---

### Finding Description

`eraMaxLanguage` is the single registration point that controls which Plutus language versions are active in a given era. It feeds `eraLanguages`, which in turn feeds `supportedLanguages` (used by `collectTwoPhaseScriptInputs`) and auxiliary-data validation:

```haskell
-- eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Scripts.hs
eraLanguages :: forall era. AlonzoEraScript era => [Language]
eraLanguages = [minBound .. eraMaxLanguage @era]
```

For `DijkstraEra`, `eraMaxLanguage` is set to `PlutusV3`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs
instance AlonzoEraScript DijkstraEra where
  data PlutusScript DijkstraEra
    = DijkstraPlutusV1 !(Plutus 'PlutusV1)
    | DijkstraPlutusV2 !(Plutus 'PlutusV2)
    | DijkstraPlutusV3 !(Plutus 'PlutusV3)
    | DijkstraPlutusV4 !(Plutus 'PlutusV4)   -- fully implemented
    ...
  eraMaxLanguage = PlutusV3                   -- BUG: should be PlutusV4
```

This means `eraLanguages @DijkstraEra = [PlutusV1, PlutusV2, PlutusV3]` — `PlutusV4` is absent.

Meanwhile, every other part of the Dijkstra implementation treats `PlutusV4` as fully supported:

- `mkSupportedLanguage` returns `Just $ SupportedLanguage SPlutusV4` for `PlutusV4`
- `TxInfoResult DijkstraEra` carries a `PlutusTxInfoResult 'PlutusV4 DijkstraEra` field
- `mkPlutusScript` accepts `SPlutusV4`
- The CDDL spec explicitly lists `plutus_v4_script` as script tag `4`
- The `HuddleSpec` lists language id `3` for PlutusV4 in `cost_models`

The inconsistency is that `eraMaxLanguage` — the single "interface registration" gate — is not updated, while all downstream implementations are.

---

### Impact Explanation

**High — Permanent freezing of funds where recovery requires a hard fork.**

`collectTwoPhaseScriptInputs` uses `supportedLanguages`, which is derived from `eraLanguages`. Because `PlutusV4` is absent from `eraLanguages`, no PlutusV4 script is ever collected for phase-2 execution. Any UTxO whose locking script is a `DijkstraPlutusV4` script cannot be spent:

- If a redeemer is supplied for the PlutusV4 script, the `hasExactSetOfRedeemers` check raises `ExtraRedeemers` and the transaction is rejected.
- If no redeemer is supplied, the `MissingRedeemers` check fires and the transaction is rejected.

In both cases the UTxO is permanently unspendable. The CDDL specification (`dijkstra.cddl`) explicitly advertises `plutus_v4_script` as a valid script type (tag `4`), so users and tooling will reasonably create PlutusV4-locked outputs. Recovery requires a hard fork to correct `eraMaxLanguage`.

A secondary impact is a **script integrity hash inconsistency**: `getLanguageView` is called for each language in `eraLanguages`. Because `PlutusV4` is absent, the PlutusV4 cost-model view is never included in the script integrity hash, even though the CDDL and `HuddleSpec` define a PlutusV4 cost-model entry (language id `3`). Implementations that independently reconstruct the hash using the CDDL spec will disagree with the ledger, constituting a deterministic disagreement between honest nodes.

---

### Likelihood Explanation

The Dijkstra era CDDL specification (`eras/dijkstra/impl/cddl/data/dijkstra.cddl`) and the `HuddleSpec` both publicly document PlutusV4 as a supported script type. Any wallet, dApp, or toolchain that reads the spec will generate PlutusV4 script addresses. The moment a user sends ADA to such an address, the funds are frozen. The entry path requires only a standard, unprivileged transaction submission.

---

### Recommendation

Change `eraMaxLanguage` in the `AlonzoEraScript DijkstraEra` instance from `PlutusV3` to `PlutusV4`:

```haskell
-- eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs
instance AlonzoEraScript DijkstraEra where
  ...
  eraMaxLanguage = PlutusV4   -- was PlutusV3
```

This single change propagates correctly through `eraLanguages`, `supportedLanguages`, `collectTwoPhaseScriptInputs`, auxiliary-data validation, and the script integrity hash computation, making the registration consistent with every other part of the Dijkstra implementation.

---

### Proof of Concept

**Root cause — the missing registration:** [1](#0-0) 

`eraMaxLanguage = PlutusV3` while `DijkstraPlutusV4` is a fully-defined constructor.

**The dispatch gate that uses `eraMaxLanguage`:** [2](#0-1) 

`eraLanguages = [minBound .. eraMaxLanguage @era]` — for DijkstraEra this yields `[PlutusV1, PlutusV2, PlutusV3]`, omitting `PlutusV4`.

**`mkSupportedLanguage` confirms PlutusV4 is otherwise fully supported:** [3](#0-2) 

**CDDL spec advertising PlutusV4 as script tag 4:** [4](#0-3) 

**HuddleSpec listing language id 3 for PlutusV4 in cost_models:** [5](#0-4) 

**Script integrity hash computation that excludes PlutusV4 cost-model view:** [6](#0-5)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L436-446)
```haskell
instance AlonzoEraScript DijkstraEra where
  data PlutusScript DijkstraEra
    = DijkstraPlutusV1 !(Plutus 'PlutusV1)
    | DijkstraPlutusV2 !(Plutus 'PlutusV2)
    | DijkstraPlutusV3 !(Plutus 'PlutusV3)
    | DijkstraPlutusV4 !(Plutus 'PlutusV4)
    deriving (Eq, Ord, Show, Generic)

  type PlutusPurpose f DijkstraEra = DijkstraPlutusPurpose f DijkstraEra

  eraMaxLanguage = PlutusV3
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Scripts.hs (L749-750)
```haskell
eraLanguages :: forall era. AlonzoEraScript era => [Language]
eraLanguages = [minBound .. eraMaxLanguage @era]
```

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/TxInfo.hs (L228-232)
```haskell
  mkSupportedLanguage = \case
    PlutusV1 -> Just $ SupportedLanguage SPlutusV1
    PlutusV2 -> Just $ SupportedLanguage SPlutusV2
    PlutusV3 -> Just $ SupportedLanguage SPlutusV3
    PlutusV4 -> Just $ SupportedLanguage SPlutusV4
```

**File:** eras/dijkstra/impl/cddl/data/dijkstra.cddl (L376-388)
```text
; Dijkstra supports five script types:
;   0: Native scripts with guard support (7 variants)
;   1: Plutus V1 scripts
;   2: Plutus V2 scripts
;   3: Plutus V3 scripts
;   4: Plutus V4 scripts (NEW)
script =
  [  0, native_script
  // 1, plutus_v1_script
  // 2, plutus_v2_script
  // 3, plutus_v3_script
  // 4, plutus_v4_script
  ]
```

**File:** eras/dijkstra/impl/cddl/lib/Cardano/Ledger/Dijkstra/HuddleSpec.hs (L1135-1143)
```haskell
instance HuddleRule "language" DijkstraEra where
  huddleRuleNamed pname _ =
    comment
      [str| 0: Plutus v1
          | 1: Plutus v2
          | 2: Plutus v3
          | 3: Plutus v4 (NEW)
          |]
      $ pname =.= (0 :: Integer) ... (3 :: Integer)
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/PParams.hs (L566-585)
```haskell
getLanguageView ::
  AlonzoEraPParams era =>
  PParams era ->
  Language ->
  LangDepView
getLanguageView pp lang =
  case lang of
    PlutusV1 ->
      LangDepView -- The silly double bagging is to keep compatibility with a past bug
        (serialize' version (serialize' version lang))
        (serialize' version costModelEncoding)
    PlutusV2 -> latestLangDepView
    PlutusV3 -> latestLangDepView
    PlutusV4 -> latestLangDepView
  where
    -- LangDepView for PlutusV1 differs from the rest
    latestLangDepView = LangDepView (serialize' version lang) costModelEncoding
    costModel = Map.lookup lang (costModelsValid $ pp ^. ppCostModelsL)
    costModelEncoding = serialize' version $ maybe encodeNull encodeCostModel costModel
    version = BT.pvMajor $ pp ^. ppProtocolVersionL
```
