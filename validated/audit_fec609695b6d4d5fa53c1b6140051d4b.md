### Title
`eraMaxLanguage` Underdeclares Supported Plutus Version in `DijkstraEra`, Enabling Permanent Fund Freezing — (File: `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`)

---

### Summary

In `DijkstraEra`, the `AlonzoEraScript` instance declares `eraMaxLanguage = PlutusV3` while simultaneously defining a `DijkstraPlutusV4` constructor and handling `SPlutusV4` in `mkPlutusScript`. This is the direct Cardano analog of the ERC165 `supportsInterface()` omission: the era *implements* PlutusV4 script support (the constructor exists, `mkPlutusScript` returns `Just` for V4) but does not *declare* it through the canonical capability-discovery mechanism (`eraMaxLanguage`). As a result, `eraLanguages` and `supportedLanguages` exclude PlutusV4, making it impossible to execute PlutusV4 scripts. Any ADA or native assets locked at a PlutusV4 script address in DijkstraEra are permanently frozen.

---

### Finding Description

`AlonzoEraScript DijkstraEra` is defined in `eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs`:

```haskell
instance AlonzoEraScript DijkstraEra where
  data PlutusScript DijkstraEra
    = DijkstraPlutusV1 !(Plutus 'PlutusV1)
    | DijkstraPlutusV2 !(Plutus 'PlutusV2)
    | DijkstraPlutusV3 !(Plutus 'PlutusV3)
    | DijkstraPlutusV4 !(Plutus 'PlutusV4)   -- V4 constructor exists
    ...

  eraMaxLanguage = PlutusV3                   -- BUG: should be PlutusV4

  mkPlutusScript plutus =
    case plutusSLanguage plutus of
      SPlutusV1 -> pure $ DijkstraPlutusV1 plutus
      SPlutusV2 -> pure $ DijkstraPlutusV2 plutus
      SPlutusV3 -> pure $ DijkstraPlutusV3 plutus
      SPlutusV4 -> pure $ DijkstraPlutusV4 plutus  -- V4 handled → returns Just
``` [1](#0-0) 

`eraMaxLanguage` is the sole source of truth for which Plutus languages are active in an era. Two critical derived values depend on it:

**`eraLanguages`** (used in auxiliary-data validation and cost-model checks):
```haskell
eraLanguages :: forall era. AlonzoEraScript era => [Language]
eraLanguages = [minBound .. eraMaxLanguage @era]
``` [2](#0-1) 

**`supportedLanguages`** (used to build the set of languages for which cost models are required and script execution is permitted):
```haskell
supportedLanguages =
  let langs =
        [ errorFail (mkSupportedLanguageM lang)
        | lang <- [minBound .. eraMaxLanguage @era]
        ]
``` [3](#0-2) 

Because `eraMaxLanguage = PlutusV3`, both `eraLanguages` and `supportedLanguages` for `DijkstraEra` enumerate only `[PlutusV1, PlutusV2, PlutusV3]`. PlutusV4 is absent. Consequently:

1. No cost model for PlutusV4 is required or looked up in protocol parameters.
2. Any attempt to collect script inputs for a PlutusV4 script produces a `NoCostModel` `CollectError`.
3. The two-phase validation path rejects every transaction that attempts to execute a PlutusV4 script.

Meanwhile, `mkPlutusScript` returns `Just (DijkstraPlutusV4 plutus)` for any PlutusV4 binary, meaning:
- PlutusV4 script hashes are computable and valid payment credentials.
- Outputs can be sent to PlutusV4 script addresses without error.
- The scripts can be included in the witness set of a transaction.

The gap between "can be locked" and "can never be unlocked" is the vulnerability.

---

### Impact Explanation

**Impact: High — Permanent freezing of funds.**

Any ADA or native assets sent to a PlutusV4 script address in DijkstraEra are irrecoverable without a hard fork. The ledger will consistently reject every spending transaction because PlutusV4 is absent from `eraLanguages` and `supportedLanguages`. There is no in-protocol escape hatch. Recovery requires either a hard fork that corrects `eraMaxLanguage` to `PlutusV4`, or a special-case migration rule.

---

### Likelihood Explanation

**Likelihood: Medium.**

The inconsistency is a direct invitation to user error. `mkPlutusScript` is the canonical API for determining whether a script is valid for an era — it returns `Just` for PlutusV4, which any reasonable caller interprets as "this script is supported." A developer building on DijkstraEra who tests script creation (succeeds) and then locks funds (succeeds) will only discover the freeze when attempting to spend. The Dijkstra era is experimental but is present in the production codebase and could be activated on a live network.

---

### Recommendation

Set `eraMaxLanguage = PlutusV4` in the `AlonzoEraScript DijkstraEra` instance to align the declaration with the implementation:

```haskell
eraMaxLanguage = PlutusV4
```

Alternatively, if PlutusV4 execution is intentionally deferred, remove the `DijkstraPlutusV4` constructor and the `SPlutusV4` branch from `mkPlutusScript` so that `mkPlutusScript` returns `Nothing` for PlutusV4 scripts, preventing them from being stored or hashed in the era at all.

---

### Proof of Concept

1. In DijkstraEra, call `mkPlutusScript (someV4Plutus :: Plutus 'PlutusV4)`. It returns `Just (DijkstraPlutusV4 someV4Plutus)` — no error.
2. Compute the script hash via `hashScript`. The hash is a valid `ScriptHash`.
3. Construct a transaction output paying ADA to the PlutusV4 script address. Submit it. It is accepted — funds are now locked.
4. Construct a spending transaction that includes the PlutusV4 script in the witness set and a redeemer. Submit it.
5. The ledger evaluates `eraLanguages @DijkstraEra = [PlutusV1, PlutusV2, PlutusV3]`. PlutusV4 is absent. Cost-model lookup fails with `NoCostModel`. The transaction is rejected.
6. No valid spending transaction can ever be constructed. Funds are permanently frozen. [4](#0-3) [5](#0-4) [2](#0-1) [3](#0-2)

### Citations

**File:** eras/dijkstra/impl/src/Cardano/Ledger/Dijkstra/Scripts.hs (L436-453)
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

  mkPlutusScript plutus =
    case plutusSLanguage plutus of
      SPlutusV1 -> pure $ DijkstraPlutusV1 plutus
      SPlutusV2 -> pure $ DijkstraPlutusV2 plutus
      SPlutusV3 -> pure $ DijkstraPlutusV3 plutus
      SPlutusV4 -> pure $ DijkstraPlutusV4 plutus
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Scripts.hs (L749-750)
```haskell
eraLanguages :: forall era. AlonzoEraScript era => [Language]
eraLanguages = [minBound .. eraMaxLanguage @era]
```

**File:** eras/alonzo/impl/src/Cardano/Ledger/Alonzo/Plutus/Context.hs (L320-329)
```haskell
supportedLanguages ::
  forall era.
  (HasCallStack, EraPlutusContext era) =>
  NonEmpty (SupportedLanguage era)
supportedLanguages =
  let langs =
        [ errorFail (mkSupportedLanguageM lang)
        | lang <- [minBound .. eraMaxLanguage @era]
        ]
   in case nonEmpty langs of
```
