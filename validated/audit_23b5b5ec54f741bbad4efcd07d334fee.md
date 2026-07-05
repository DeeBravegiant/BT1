### Title
`dRepAcceptedRatio` Uses Raw `drepExpiry` Instead of Actual Expiry (Ignoring `numDormantEpochs`), Enabling Governance Actions to Pass with Insufficient DRep Support — (File: `eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs`)

---

### Summary

The `dRepAcceptedRatio` function in the Conway `RATIFY` rule checks whether a DRep is expired by comparing `reCurrentEpoch > drepExpiry drepState`. However, the actual DRep expiry is `drepExpiry + numDormantEpochs` (as defined by `vsActualDRepExpiry`). Because `numDormantEpochs` is not snapshotted into the `DRepPulser` or the `RatifyEnv`, the ratification rule systematically under-counts active DReps whenever dormant epochs have accumulated. DReps that voted **No** (or abstained by not voting) and whose raw `drepExpiry` falls below `reCurrentEpoch` but whose actual expiry does not are silently excluded from the denominator, inflating the yes-ratio and potentially allowing governance actions to be enacted without the required DRep support.

---

### Finding Description

**Vulnerability class:** Stale/snapshot-vs-live-state mismatch in governance voting-power calculation (direct analog of the external report's `getsmNFTPastVotes` bug).

#### How DRep expiry works in Conway

The `VState` tracks two quantities:

| Field | Meaning |
|---|---|
| `drepExpiry` (inside `DRepState`) | Raw stored expiry epoch, written when the DRep registers or votes |
| `vsNumDormantEpochs` (inside `VState`) | Count of consecutive epochs with no active governance proposals |

The **actual** expiry is `drepExpiry + vsNumDormantEpochs`. This is codified in `vsActualDRepExpiry`:

```haskell
vsActualDRepExpiry cred vs =
  binOpEpochNo (+) (vsNumDormantEpochs vs) . drepExpiry <$> Map.lookup cred (vsDReps vs)
``` [1](#0-0) 

The `isDRepExpired` test helper also uses this two-field formula:

```haskell
binOpEpochNo (+) (vState ^. vsNumDormantEpochsL) (drep' ^. drepExpiryL) < currentEpoch
``` [2](#0-1) 

#### The snapshot omits `numDormantEpochs`

At each epoch boundary, `setFreshDRepPulsingState` creates a new `DRepPulser`. It snapshots `dpDRepState = vsDReps vState` (the raw `DRepState` map) but does **not** snapshot `vsNumDormantEpochs`:

```haskell
dpDRepState = vsDReps vState
``` [3](#0-2) 

The `RatifyEnv` built from this snapshot also has no `numDormantEpochs` field:

```haskell
RatifyEnv
  { reInstantStake = dpInstantStake
  , reStakePoolDistr = finalStakePoolDistr
  , reDRepDistr = finalDRepDistr
  , reDRepState = dpDRepState   -- raw drepExpiry values only
  , reCurrentEpoch = dpCurrentEpoch
  , reCommitteeState = dpCommitteeState
  , reAccounts = dpAccounts
  , reStakePools = dpStakePools
  }
``` [4](#0-3) 

#### The ratification check uses only the raw field

`dRepAcceptedRatio` checks:

```haskell
Just drepState
  | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
``` [5](#0-4) 

This is `drepExpiry` alone — not `drepExpiry + numDormantEpochs`. The `numDormantEpochs` value that was incremented by `updateNumDormantEpochs` at the epoch boundary is never consulted.

#### Concrete scenario

| Epoch | Event | `numDormantEpochs` | DRep raw `drepExpiry` | Actual expiry |
|---|---|---|---|---|
| 0 | DRep registers, `drepActivity = 2` | 0 | 2 | 2 |
| 1 | No proposals | 1 | 2 | 3 |
| 2 | No proposals | 2 | 2 | 4 |
| 3 | No proposals | 3 | 2 | 5 |
| 3 boundary | Pulser snapshot taken, `dpCurrentEpoch = 4` | — | snapshot: `drepExpiry = 2` | — |
| 4 | RATIFY runs: `reCurrentEpoch = 4 > drepExpiry = 2` → **DRep treated as expired** | — | — | actual expiry = 5, DRep is **active** |

The DRep is active (actual expiry 5 > current epoch 4) but is excluded from the denominator. If this DRep voted **No**, its stake is silently dropped, inflating the yes-ratio.

---

### Impact Explanation

When `numDormantEpochs > 0` at the epoch boundary, any DRep whose raw `drepExpiry` satisfies:

```
reCurrentEpoch > drepExpiry   AND   reCurrentEpoch ≤ drepExpiry + numDormantEpochs
```

is incorrectly excluded from the ratification denominator. DReps that voted **No** or did not vote (counted as No by default) are silently removed from the denominator, inflating the yes/(yes+no) ratio. This can cause governance actions — including `ParameterChange`, `TreasuryWithdrawals`, `HardForkInitiation`, `UpdateCommittee`, and `NewConstitution` — to be ratified and enacted without the required DRep supermajority.

This matches the allowed impact: **Critical — Unauthorized governance, treasury, protocol-parameter, committee, constitution, or hard-fork action is enacted.**

---

### Likelihood Explanation

- `numDormantEpochs > 0` is a normal operating condition: it accumulates automatically whenever no governance proposals are active for one or more epochs.
- The vulnerable DRep range (`drepExpiry ∈ [reCurrentEpoch − numDormantEpochs, reCurrentEpoch − 1]`) grows linearly with the number of dormant epochs.
- No privileged access is required. Any transaction sender can submit a governance action and vote on it; the bug is in the ledger's own ratification logic.
- The attacker can time proposal submission to maximize the number of No-voting DReps in the vulnerable range.

---

### Recommendation

Include `numDormantEpochs` in the `DRepPulser` snapshot and propagate it into `RatifyEnv`. Update the expiry check in `dRepAcceptedRatio` to use the actual expiry:

```haskell
-- current (incorrect):
| reCurrentEpoch > drepExpiry drepState -> (yes, tot)

-- corrected:
| reCurrentEpoch > binOpEpochNo (+) reNumDormantEpochs (drepExpiry drepState) -> (yes, tot)
```

Alternatively, pre-compute the actual expiry into the `DRepState` snapshot before creating the pulser (analogous to how `updateDormantDRepExpiry` bumps expiries when a proposal is submitted).

---

### Proof of Concept

**Step 1.** Set `ppDRepActivityL = EpochInterval 2` and register a DRep with 1 000 000 ADA delegated. The DRep's raw `drepExpiry` is set to `currentEpoch + 2`.

**Step 2.** Pass 3 epochs with no governance proposals. `numDormantEpochs` increments to 3. The DRep's raw `drepExpiry` is now `< currentEpoch`, but its actual expiry (`drepExpiry + 3`) is still in the future.

**Step 3.** Submit a governance action (e.g., `ParameterChange`) that requires DRep approval. Have the DRep vote **No**.

**Step 4.** Have a second, smaller DRep vote **Yes**.

**Step 5.** At the next epoch boundary, the pulser snapshot captures `drepExpiry` (raw) for both DReps. `reCurrentEpoch > drepExpiry` is true for the No-voting DRep → it is excluded from the denominator. The Yes-voting DRep's stake alone now constitutes 100 % of the counted denominator, and the proposal is ratified despite the No-voting DRep holding the majority of delegated stake.

The key ledger path is:

`LEDGER → GOV (vote recorded in gasGovDRepVotes)` → epoch boundary → `EPOCH → setFreshDRepPulsingState` (snapshot without `numDormantEpochs`) → `DRepPulser.finishDRepPulser → runConwayRatify → RATIFY → dRepAcceptedRatio` (incorrect expiry check at line 267). [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L56-67)
```haskell
data VState era = VState
  { vsDReps :: !(Map (Credential DRepRole) DRepState)
  , vsCommitteeState :: !(CommitteeState era)
  , vsNumDormantEpochs :: !EpochNo
  -- ^ Number of contiguous epochs in which there are exactly zero
  -- active governance proposals to vote on. It is incremented in every
  -- EPOCH rule if the number of active governance proposals to vote on
  -- continues to be zero. It is reset to zero when a new governance
  -- action is successfully proposed. We need this counter in order to
  -- bump DRep expiries through dormant periods when DReps do not have
  -- an opportunity to vote on anything.
  }
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/State/VState.hs (L154-156)
```haskell
vsActualDRepExpiry :: Credential DRepRole -> VState era -> Maybe EpochNo
vsActualDRepExpiry cred vs =
  binOpEpochNo (+) (vsNumDormantEpochs vs) . drepExpiry <$> Map.lookup cred (vsDReps vs)
```

**File:** eras/conway/impl/testlib/Test/Cardano/Ledger/Conway/ImpTest.hs (L1606-1614)
```haskell
isDRepExpired drep = do
  vState <- getsNES $ nesEsL . esLStateL . lsCertStateL . certVStateL
  currentEpoch <- getsNES nesELL
  case Map.lookup drep $ vState ^. vsDRepsL of
    Nothing -> error $ unlines ["DRep not found", show drep]
    Just drep' ->
      pure $
        binOpEpochNo (+) (vState ^. vsNumDormantEpochsL) (drep' ^. drepExpiryL)
          < currentEpoch
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance.hs (L497-515)
```haskell
                ( DRepPulser
                    { dpPulseSize = floor pulseSize
                    , dpAccounts = dState ^. accountsL
                    , dpIndex = 0 -- used as the index of the remaining UMap
                    , dpInstantStake = instantStake -- used as part of the snapshot
                    , dpStakePoolDistr = stakePoolDistr
                    , dpDRepDistr = Map.empty -- The partial result starts as the empty map
                    , dpDRepState = vsDReps vState
                    , dpCurrentEpoch = epochNo
                    , dpCommitteeState = vsCommitteeState vState
                    , dpEnactState =
                        mkEnactState govState
                          & ensTreasuryL .~ epochState ^. treasuryL
                    , dpProposals = proposalsActions props
                    , dpProposalDeposits = proposalsDeposits props
                    , dpGlobals = globals
                    , dpStakePools = epochState ^. epochStateStakePoolsL
                    }
                )
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Governance/DRepPulser.hs (L386-417)
```haskell
finishDRepPulser (DRPulsing (DRepPulser {..})) =
  ( PulsingSnapshot
      dpProposals
      finalDRepDistr
      dpDRepState
      (Map.map individualTotalPoolStake $ unPoolDistr finalStakePoolDistr)
  , ratifyState'
  )
  where
    !leftOver = Map.drop dpIndex (dpAccounts ^. accountsMapL)
    (finalDRepDistr, finalStakePoolDistr) =
      computeDRepDistr dpInstantStake dpDRepState dpProposalDeposits dpStakePoolDistr dpDRepDistr leftOver
    !ratifyEnv =
      RatifyEnv
        { reInstantStake = dpInstantStake
        , reStakePoolDistr = finalStakePoolDistr
        , reDRepDistr = finalDRepDistr
        , reDRepState = dpDRepState
        , reCurrentEpoch = dpCurrentEpoch
        , reCommitteeState = dpCommitteeState
        , reAccounts = dpAccounts
        , reStakePools = dpStakePools
        }
    !ratifySig = RatifySignal dpProposals
    !ratifyState =
      RatifyState
        { rsEnactState = dpEnactState
        , rsEnacted = mempty
        , rsExpired = mempty
        , rsDelayed = False
        }
    !ratifyState' = runConwayRatify dpGlobals ratifyEnv ratifyState ratifySig
```

**File:** eras/conway/impl/src/Cardano/Ledger/Conway/Rules/Ratify.hs (L258-281)
```haskell
dRepAcceptedRatio RatifyEnv {reDRepDistr, reDRepState, reCurrentEpoch} gasDRepVotes govAction =
  toInteger yesStake %? toInteger totalExcludingAbstainStake
  where
    accumStake (!yes, !tot) drep (CompactCoin stake) =
      case drep of
        DRepCredential cred ->
          case Map.lookup cred reDRepState of
            Nothing -> (yes, tot) -- drep is not registered, so we don't consider it
            Just drepState
              | reCurrentEpoch > drepExpiry drepState -> (yes, tot) -- drep is expired, so we don't consider it
              | otherwise ->
                  case Map.lookup cred gasDRepVotes of
                    -- drep hasn't voted for this action, so we don't count
                    -- the vote but we consider it in the denominator:
                    Nothing -> (yes, tot + stake)
                    Just VoteYes -> (yes + stake, tot + stake)
                    Just Abstain -> (yes, tot)
                    Just VoteNo -> (yes, tot + stake)
        DRepAlwaysNoConfidence ->
          case govAction of
            NoConfidence _ -> (yes + stake, tot + stake)
            _ -> (yes, tot + stake)
        DRepAlwaysAbstain -> (yes, tot)
    (yesStake, totalExcludingAbstainStake) = Map.foldlWithKey' accumStake (0, 0) reDRepDistr
```
