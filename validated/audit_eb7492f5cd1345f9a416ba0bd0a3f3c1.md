Audit Report

## Title
Publicly Callable `updateRSETHPrice()` Enables Yield Dilution via Deposit at Stale Price - (File: contracts/LRTOracle.sol)

## Summary
`LRTOracle.updateRSETHPrice()` carries no access control and is callable by any address. Because `rsETHPrice` is only refreshed when this function executes, an attacker can deposit ETH/LST at the stale (lower) price, immediately trigger the price update, and capture a portion of yield that should have accrued exclusively to pre-existing rsETH holders. Existing holders receive a proportionally smaller price increase, constituting a direct theft of unclaimed yield.

## Finding Description
`LRTOracle.updateRSETHPrice()` is unconditionally public: [1](#0-0) 

Inside `_updateRsETHPrice`, `previousTVL` is computed using the **stored** (stale) `rsETHPrice`: [2](#0-1) 

Deposits in `LRTDepositPool.getRsETHAmountToMint` also use the same stale stored value: [3](#0-2) 

**Root cause:** When an attacker deposits X ETH at the stale price, `rsethSupply` increases by `X / rsETHPrice` and `totalETHInProtocol` increases by X. Because the deposit is priced at exactly `rsETHPrice`, the `previousTVL` calculation (`rsethSupply × rsETHPrice`) absorbs the new deposit perfectly — the `rewardAmount` (`totalETHInProtocol - previousTVL`) is **unchanged** by the deposit. However, the fixed reward is now divided across a larger supply, diluting the per-token yield for pre-existing holders while the attacker's rsETH was minted at the cheaper stale price.

**Why the `pricePercentageLimit` guard is insufficient:** The guard at lines 252–266 only reverts for non-manager callers when the price increase exceeds the configured threshold. [4](#0-3) 

For yield accrual within the threshold, the attacker calls `updateRSETHPrice()` directly. For yield accrual above the threshold, the attacker front-runs the manager's `updateRSETHPriceAsManager()` call — the deposit step requires no privileged role and the front-run only needs mempool monitoring.

**Exploit path (single block, no flash loan):**
1. Observe `totalETHInProtocol > rsethSupply × rsETHPrice` (yield has accrued).
2. Call `LRTDepositPool.depositETH{value: X}(minRSETH, "")` — receive `X / rsETHPrice` rsETH at the stale price.
3. Call `LRTOracle.updateRSETHPrice()` (or front-run the manager's call) — price rises to `newRsETHPrice`.
4. Attacker's rsETH is now worth `(X / rsETHPrice) × newRsETHPrice > X` ETH.
5. Sell rsETH on secondary market immediately (no withdrawal delay required).

## Impact Explanation
**High — Theft of unclaimed yield.**

Existing rsETH holders receive a lower price increase than they are entitled to; the attacker captures the difference. Using the PoC numbers (1000 ETH TVL, 100 ETH yield, 10% protocol fee):

| Scenario | newRsETHPrice | Existing holders' gain per rsETH |
|---|---|---|
| No attacker | 1.09 ETH | +0.09 ETH |
| Attacker deposits 1000 ETH first | 1.045 ETH | +0.045 ETH |

Existing holders lose 45 ETH of yield; the attacker gains 45 ETH. The loss scales linearly with the attacker's deposit size relative to protocol TVL. This matches the allowed impact "High. Theft of unclaimed yield."

## Likelihood Explanation
**Medium.** The attack requires capital (flash loans are blocked by the 8-day withdrawal queue), but: no privileged role is needed; `updateRSETHPrice()` is unconditionally public; rsETH can be sold on secondary markets immediately, bypassing the withdrawal delay; and the attack is repeatable every time yield accrues. Front-running the manager's periodic keeper call requires only mempool monitoring.

## Recommendation
1. **Restrict `updateRSETHPrice()`** to a keeper/manager role (analogous to the existing `updateRSETHPriceAsManager()`) so that the timing of price updates cannot be controlled by an attacker.
2. **Alternatively**, snapshot the rsETH supply and TVL at deposit time and exclude newly deposited assets from the current reward window (one-epoch delay before new deposits participate in yield distribution).
3. **Alternatively**, use a time-weighted average price so that a single block's deposit cannot capture an entire accrued-yield window.

## Proof of Concept
```
Initial state:
  rsethSupply        = 1 000 rsETH
  rsETHPrice         = 1.000 ETH/rsETH  (stale)
  totalETHInProtocol = 1 100 ETH        (100 ETH yield accrued)
  protocolFeeBPS     = 1 000 (10%)

Step 1 – Attacker deposits 1 000 ETH at stale price:
  rsETH minted       = 1 000 / 1.000 = 1 000 rsETH
  new rsethSupply    = 2 000 rsETH
  new totalETH       = 2 100 ETH

Step 2 – Attacker calls updateRSETHPrice():
  previousTVL        = 2 000 × 1.000 = 2 000 ETH   ← stale price absorbs deposit
  rewardAmount       = 2 100 − 2 000 = 100 ETH      ← unchanged from pre-deposit
  protocolFee        = 100 × 10% = 10 ETH
  newRsETHPrice      = (2 100 − 10) / 2 000 = 1.045 ETH/rsETH

Attacker outcome:
  rsETH held         = 1 000
  ETH value          = 1 000 × 1.045 = 1 045 ETH
  Profit             = +45 ETH

Existing holders (1 000 rsETH):
  ETH value          = 1 000 × 1.045 = 1 045 ETH
  Expected (no attack) = 1 000 × 1.09 = 1 090 ETH
  Loss               = −45 ETH

Foundry fork test plan:
  1. Fork mainnet at a block where yield has accrued.
  2. Prank as attacker; call depositETH with value X.
  3. Record attacker rsETH balance and existing holder rsETH balance.
  4. Call updateRSETHPrice().
  5. Assert attacker's rsETH value > X (profit > 0).
  6. Assert existing holder's per-rsETH gain < expected gain without attacker deposit.
  7. Fuzz over X to show profit scales linearly with deposit size.
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L233-246)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
