### Title
Duplicate Hot Credential Registration Allows a Single Committee Member to Double-Count Votes in `committeeAcceptedRatio` — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

The `GOVCERT` transition rule in `GovCert.hs` does not enforce uniqueness of hot credentials across committee cold keys. A committee member who controls two cold keys can register the same hot credential for both. Because `committeeAcceptedRatio` in `Ratify.hs` iterates over cold keys and resolves each to its hot key independently, the same vote is counted once per cold key, not once per hot key. A single actor can therefore multiply their effective voting weight, potentially ratifying governance actions that should not pass.

---

### Finding Description

**Root cause — `GovCert.hs`, `checkAndOverwriteCommitteeMemberState` (lines 190–208)**

When a `ConwayAuthCommitteeHotKey coldCred hotCred` certificate is processed, the helper `checkAndOverwriteCommitteeMemberState` performs exactly two checks:

1. The cold key has not previously resigned (`ConwayCommitteeHasPreviouslyResigned`).
2. The cold key is a current or pending committee member (`ConwayCommitteeIsUnknown`). [1](#0-0) 

There is **no check** that `hotCred` is not already mapped to by a different cold key. The final operation is a plain `Map.insert coldCred newMemberState`, which silently allows two cold keys to share the same hot credential. [2](#0-1) 

**Vote-counting flaw — `Ratify.hs`, `committeeAcceptedRatio` (lines 143–163)**

`committeeAcceptedRatio` folds over `members :: Map (Credential ColdCommitteeRole) EpochNo`. For each cold key it resolves the associated hot key from `CommitteeState`, then looks up that hot key's vote: [3](#0-2) 

If `coldKey1` and `coldKey2` both map to `hotKey`, and `hotKey` voted `VoteYes`, the fold increments `(yes, tot)` **twice** — once for each cold key — even though only one vote was cast.

**Test evidence of the reachable state**

The property-based test generator `genNonResignedCommitteeState` explicitly produces `CommitteeState` values where two cold keys share the same hot credential (10 % of generated cases via `overwriteWithDuplicate`), confirming the ledger itself treats this as a valid state: [4](#0-3) 

---

### Impact Explanation

The committee vote is required for ratification of `NewConstitution`, `HardForkInitiation`, `ParameterChange`, and `TreasuryWithdrawals` governance actions. [5](#0-4) 

An attacker who inflates their effective vote count can push any of those action types past the committee threshold without the genuine agreement of enough independent committee members. This constitutes **unauthorized enactment of governance, treasury, protocol-parameter, or hard-fork actions** — matching the Critical impact tier.

---

### Likelihood Explanation

The attack requires the adversary to hold two committee cold keys simultaneously. This is achievable without any privileged access:

- A single entity can be elected to the committee with two distinct cold credentials via a legitimately-passed `UpdateCommittee` governance action.
- Once both cold keys are in the committee, the attacker submits a single transaction containing two `AuthCommitteeHotKey` certificates — one for each cold key — both pointing to the same hot credential. All ledger checks pass.
- The attacker then casts one vote with the shared hot key; `committeeAcceptedRatio` counts it twice.

No leaked keys, no malicious supermajority, and no privileged operator access are required beyond holding two committee seats.

---

### Recommendation

In `checkAndOverwriteCommitteeMemberState`, before inserting the new mapping, verify that the proposed hot credential is not already present as a value in `csCommitteeCreds`:

```haskell
-- Proposed addition inside checkAndOverwriteCommitteeMemberState,
-- before the Map.insert:
let hotCredAlreadyUsed = case newMemberState of
      CommitteeHotCredential hotCred ->
        any (== CommitteeHotCredential hotCred)
            (Map.elems csCommitteeCreds)
      _ -> False
when hotCredAlreadyUsed $
  failBecause (injectFailure $ ConwayCommitteeHotCredentialAlreadyUsed hotCred)
```

A corresponding `ConwayCommitteeHotCredentialAlreadyUsed` predicate failure should be added to `ConwayGovCertPredFailure`. [6](#0-5) 

---

### Proof of Concept

**Setup:**
- Committee: 3 members — `coldKey1` (attacker), `coldKey2` (attacker), `coldKey3` (honest).
- Committee threshold: 2/3 (≈ 0.667).
- `coldKey1` and `coldKey2` are both elected via a prior `UpdateCommittee` action.

**Attack transaction:**
```
certsTxBody = [
  AuthCommitteeHotKey coldKey1 sharedHotKey,
  AuthCommitteeHotKey coldKey2 sharedHotKey   -- same hot key, no ledger rejection
]
```

**After the transaction, `csCommitteeCreds` contains:**
```
{ coldKey1 -> CommitteeHotCredential sharedHotKey
, coldKey2 -> CommitteeHotCredential sharedHotKey
, coldKey3 -> CommitteeHotCredential honestHotKey  -- not yet voted
}
```

**Attacker votes:**
```
VotingProcedure sharedHotKey VoteYes govActionId
```

**`committeeAcceptedRatio` fold result:**
| Cold key | Hot key | Vote | yes | tot |
|---|---|---|---|---|
| coldKey1 | sharedHotKey | VoteYes | +1 | +1 |
| coldKey2 | sharedHotKey | VoteYes | +1 | +1 |
| coldKey3 | honestHotKey | Nothing (no vote) | 0 | +1 |

`yes = 2`, `tot = 3` → ratio = 2/3 ≥ threshold → **committee accepted**.

Without the duplicate registration, `yes = 1`, `tot = 3` → ratio = 1/3 < 2/3 → **committee rejected**.

The attacker ratifies a governance action (e.g., `TreasuryWithdrawals` draining the treasury, or `HardForkInitiation`) with only one actual key, bypassing the intended quorum requirement. [7](#0-6) [1](#0-0)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L108-118)
```haskell
data ConwayGovCertPredFailure era
  = ConwayDRepAlreadyRegistered (Credential DRepRole)
  | ConwayDRepNotRegistered (Credential DRepRole)
  | ConwayDRepIncorrectDeposit (Mismatch RelEQ Coin)
  | ConwayCommitteeHasPreviouslyResigned (Credential ColdCommitteeRole)
  | ConwayDRepIncorrectRefund (Mismatch RelEQ Coin)
  | -- | Predicate failure whenever an update to an unknown committee member is
    -- attempted. Current Constitutional Committee and all available proposals will be
    -- searched before reporting this predicate failure.
    ConwayCommitteeIsUnknown (Credential ColdCommitteeRole)
  deriving (Show, Eq, Generic)
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L190-208)
```haskell
      checkAndOverwriteCommitteeMemberState coldCred newMemberState = do
        let VState {vsCommitteeState = CommitteeState csCommitteeCreds} = certState ^. certVStateL
            coldCredResigned =
              Map.lookup coldCred csCommitteeCreds >>= \case
                CommitteeMemberResigned {} -> Just coldCred
                CommitteeHotCredential {} -> Nothing
        failOnJust coldCredResigned $ injectFailure . ConwayCommitteeHasPreviouslyResigned
        let isCurrentMember =
              strictMaybe False (Map.member coldCred . committeeMembers) cgceCurrentCommittee
            committeeUpdateContainsColdCred GovActionState {gasProposalProcedure} =
              case pProcGovAction gasProposalProcedure of
                UpdateCommittee _ _ newMembers _ -> Map.member coldCred newMembers
                _ -> False
            isPotentialFutureMember =
              any committeeUpdateContainsColdCred cgceCommitteeProposals
        isCurrentMember || isPotentialFutureMember ?! (injectFailure . ConwayCommitteeIsUnknown) coldCred
        pure $
          certState
            & certVStateL . vsCommitteeStateL . csCommitteeCredsL %~ Map.insert coldCred newMemberState
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs (L273-274)
```haskell
    ConwayAuthCommitteeHotKey coldCred hotCred ->
      checkAndOverwriteCommitteeMemberState coldCred $ CommitteeHotCredential hotCred
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L136-163)
```haskell
committeeAcceptedRatio ::
  forall era.
  Map (Credential ColdCommitteeRole) EpochNo ->
  Map (Credential HotCommitteeRole) Vote ->
  CommitteeState era ->
  EpochNo ->
  Rational
committeeAcceptedRatio members votes committeeState currentEpoch =
  yesVotes %? totalExcludingAbstain
  where
    accumVotes ::
      (Integer, Integer) ->
      Credential ColdCommitteeRole ->
      EpochNo ->
      (Integer, Integer)
    accumVotes (!yes, !tot) member expiry
      | currentEpoch > expiry = (yes, tot) -- member is expired, vote "abstain" (don't count it)
      | otherwise =
          case Map.lookup member (csCommitteeCreds committeeState) of
            Nothing -> (yes, tot) -- member is not registered, vote "abstain"
            Just (CommitteeMemberResigned _) -> (yes, tot) -- member has resigned, vote "abstain"
            Just (CommitteeHotCredential hotKey) ->
              case Map.lookup hotKey votes of
                Nothing -> (yes, tot + 1) -- member hasn't voted, vote "no"
                Just Abstain -> (yes, tot) -- member voted "abstain"
                Just VoteNo -> (yes, tot + 1) -- member voted "no"
                Just VoteYes -> (yes + 1, tot + 1) -- member voted "yes"
    (yesVotes, totalExcludingAbstain) = Map.foldlWithKey' accumVotes (0, 0) members
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/CommitteeRatifySpec.hs (L296-314)
```haskell
genNonResignedCommitteeState :: Set.Set (Credential ColdCommitteeRole) -> Gen (CommitteeState era)
genNonResignedCommitteeState coldCreds = do
  hotCredsMap <-
    sequence $
      Map.fromSet
        (const $ CommitteeHotCredential <$> arbitrary)
        coldCreds
  frequency
    [ (9, pure $ CommitteeState hotCredsMap)
    , (1, CommitteeState <$> overwriteWithDuplicate hotCredsMap)
    ]
  where
    overwriteWithDuplicate m
      | Map.size m < 2 = pure m
      | otherwise = do
          fromIx <- choose (0, Map.size m - 1)
          toIx <- choose (0, Map.size m - 1)
          let valueToDuplicate = snd $ Map.elemAt fromIx m
          pure $ Map.updateAt (\_ _ -> Just valueToDuplicate) toIx m
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/Internal.hs (L452-459)
```haskell
votingCommitteeThresholdInternal currentEpoch pp committee (CommitteeState hotKeys) = \case
  NoConfidence {} -> NoVotingAllowed
  UpdateCommittee {} -> NoVotingAllowed
  NewConstitution {} -> threshold
  HardForkInitiation {} -> threshold
  ParameterChange {} -> threshold
  TreasuryWithdrawals {} -> threshold
  InfoAction {} -> NoVotingThreshold
```
