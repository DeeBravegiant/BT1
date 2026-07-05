### Title
Lack of Hot Credential Uniqueness Validation in `GOVCERT` Allows a Single Committee Vote to Count as Multiple Members' Votes - (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/GovCert.hs`)

---

### Summary

The `conwayGovCertTransition` rule in `GovCert.hs` processes `ConwayAuthCommitteeHotKey` certificates without validating that the supplied hot credential is not already authorized for a different cold credential. Because `committeeAcceptedRatio` in `Ratify.hs` iterates over cold credentials and resolves each one's vote independently via its hot credential, a single hot credential's `VoteYes` is counted once per cold credential that maps to it. A committee member who controls their own cold key can therefore authorize a hot credential already in use by another committee member, causing that hot credential's single on-chain vote to satisfy the committee threshold as if multiple independent members had voted.

---

### Finding Description

**Root cause — `GovCert.hs`, `checkAndOverwriteCommitteeMemberState`:**

```haskell
checkAndOverwriteCommitteeMemberState coldCred newMemberState = do
    let VState {vsCommitteeState = CommitteeState csCommitteeCreds} = certState ^. certVStateL
        coldCredResigned = ...
    failOnJust coldCredResigned $ injectFailure . ConwayCommitteeHasPreviouslyResigned
    ...
    isCurrentMember || isPotentialFutureMember ?! (injectFailure . ConwayCommitteeIsUnknown) coldCred
    pure $
      certState
        & certVStateL . vsCommitteeStateL . csCommitteeCredsL %~ Map.insert coldCred newMemberState
```

The function checks only two things: (1) the cold credential has not previously resigned, and (2) the cold credential is a known committee member. It performs **no check** that the hot credential inside `newMemberState` is not already mapped to a different cold credential. The `Map.insert` at line 208 unconditionally writes the new mapping.

The codebase itself acknowledges this design gap:

```haskell
-- | Extract all unique hot credential authorizations for the current committee.  Note
-- that there is no unique mapping from Hot to Cold credential, therefore we produce a
-- Set, instead of a Map.
authorizedHotCommitteeCredentials :: CommitteeState era -> Set.Set (Credential HotCommitteeRole)
```

**Vote-counting consequence — `Ratify.hs`, `committeeAcceptedRatio`:**

```haskell
accumVotes (!yes, !tot) member expiry
  | currentEpoch > expiry = (yes, tot)
  | otherwise =
      case Map.lookup member (csCommitteeCreds committeeState) of
        ...
        Just (CommitteeHotCredential hotKey) ->
          case Map.lookup hotKey votes of
            Nothing -> (yes, tot + 1)
            Just VoteYes -> (yes + 1, tot + 1)
(yesVotes, totalExcludingAbstain) = Map.foldlWithKey' accumVotes (0, 0) members
```

The accumulator folds over **cold credentials** (committee members). For each cold credential it resolves the hot credential and looks up that hot credential's vote. If two cold credentials share the same hot credential, the same `VoteYes` entry is counted twice — once per cold credential — inflating both `yes` and `tot`.

**Exploit path:**

1. Alice (cold key `A`) submits `AuthCommitteeHotKey A H` — Alice controls hot key `H`.
2. Bob (cold key `B`) observes on-chain that `H` is Alice's hot key.
3. Bob submits `AuthCommitteeHotKey B H` — valid, because Bob controls cold key `B` and the rule only requires `B`'s signature as witness.
4. `csCommitteeCreds` now maps both `A → CommitteeHotCredential H` and `B → CommitteeHotCredential H`.
5. Alice submits a single `VoteYes` with hot key `H` on a governance action.
6. `committeeAcceptedRatio` counts this as two yes votes (one for `A`, one for `B`), meeting a 2/3 threshold with only one actual independent vote.

This is confirmed as reachable by the existing test:

```haskell
it "Many CC Cold Credentials map to the same Hot Credential act as many votes" $ do
    ...
    case committeeMembers' of
      x : xs -> void $ registerCommitteeHotKeys (pure hotCred) $ x NE.:| xs
    passNEpochs 2
    getLastEnactedParameterChange `shouldReturn` SJust (GovPurposeId gaId)
```

---

### Impact Explanation

A committee member (Bob) who controls only their own cold key can unilaterally cause another committee member's (Alice's) single vote to satisfy the committee threshold as if multiple independent members had voted. Governance actions — including parameter changes, hard-fork initiations, treasury withdrawals, committee updates, and constitution changes — can be enacted without the required fraction of genuinely independent committee member votes. This constitutes **unauthorized enactment of governance actions** because the committee threshold, which is the protocol's mechanism for requiring multi-party consent, is bypassed through vote amplification rather than independent consent.

Impact category: **Critical — Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.**

---

### Likelihood Explanation

The attack requires only that the attacker be a committee member (hold a cold key in the current or proposed committee). No leaked keys, no privileged operator access, and no off-chain coordination with the victim are needed. The hot key authorization of any committee member is visible on-chain. The `AuthCommitteeHotKey` certificate is a standard, fee-paying transaction that any committee member can submit at any time. The `GOVCERT` rule accepts it without restriction. Likelihood is **Medium** — it requires committee membership, but committee members are the exact actors the governance system is designed to constrain.

---

### Recommendation

In `checkAndOverwriteCommitteeMemberState` (or at the point of `Map.insert`), add a validation that the hot credential in `newMemberState` does not already appear as the value for any other cold credential in `csCommitteeCreds`. Concretely, before inserting:

```haskell
let existingHotCreds = Map.elems csCommitteeCreds
    hotCredAlreadyUsed = CommitteeHotCredential hotCred `elem` existingHotCreds
hotCredAlreadyUsed ?! injectFailure (ConwayHotCredentialAlreadyUsed hotCred)
```

This enforces a bijective mapping between cold and hot credentials, matching the security model that each committee member casts exactly one independent vote.

---

### Proof of Concept

The existing test at `eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/RatifySpec.hs` lines 52–68 is a direct proof of concept: it registers all committee members with the same hot credential, casts a single vote, and asserts that the governance action is enacted — demonstrating that one vote satisfies the full committee threshold. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** libs/cardano-ledger-core/src/Cardano/Ledger/State/CertState.hs (L335-343)
```haskell
-- | Extract all unique hot credential authorizations for the current committee.  Note
-- that there is no unique mapping from Hot to Cold credential, therefore we produce a
-- Set, instead of a Map.
authorizedHotCommitteeCredentials :: CommitteeState era -> Set.Set (Credential HotCommitteeRole)
authorizedHotCommitteeCredentials CommitteeState {csCommitteeCreds} =
  let toHotCredSet acc = \case
        CommitteeHotCredential hotCred -> Set.insert hotCred acc
        CommitteeMemberResigned {} -> acc
   in F.foldl' toHotCredSet Set.empty csCommitteeCreds
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L143-163)
```haskell
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

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/Imp/RatifySpec.hs (L52-68)
```haskell
  it "Many CC Cold Credentials map to the same Hot Credential act as many votes" $ do
    hotCred NE.:| _ <- registerInitialCommittee
    (dRep, _, _) <- setupSingleDRep =<< uniformRM (10_000_000, 1_000_000_000)
    deposit <- uniformRM (1_000_000, 100_000_000_000)
    gaId <- submitParameterChange SNothing $ def & ppuDRepDepositL .~ SJust (Coin deposit)
    submitYesVote_ (CommitteeVoter hotCred) gaId
    whenPostBootstrap $ submitYesVote_ (DRepVoter dRep) gaId
    passNEpochs 2
    logAcceptedRatio gaId
    getLastEnactedParameterChange `shouldReturn` SNothing
    -- Make sure all committee members authorize the same hot credential that just voted:
    committeeMembers' <- Set.toList <$> getCommitteeMembers
    case committeeMembers' of
      x : xs -> void $ registerCommitteeHotKeys (pure hotCred) $ x NE.:| xs
      _ -> error "Expected an initial committee"
    passNEpochs 2
    getLastEnactedParameterChange `shouldReturn` SJust (GovPurposeId gaId)
```
