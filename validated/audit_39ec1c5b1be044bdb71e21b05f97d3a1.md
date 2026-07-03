### Title
Publicly Callable `updateRSETHPrice()` Enables Yield Dilution via Deposit at Stale Price - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle.updateRSETHPrice()` carries no access control and is callable by any address. Because the stored `rsETHPrice` is only refreshed when this function executes, an attacker can deposit ETH/LST at the stale (lower) price, immediately trigger the price update, and capture yield that should have accrued exclusively to existing rsETH holders. This is the direct analog of the Bancor "add liquidity → trade → remove liquidity" fee-capture pattern: here the attacker "adds liquidity" (deposits at stale price), "triggers the distribution event" (`updateRSETHPrice`), and retains the diluted yield.

### Finding Description

`LRTOracle.updateRSETHPrice()` is unconditionally public:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [1](#0-0) 

Inside `_updateRsETHPrice`, the reward and new price are computed as:

```solidity
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
// ...
uint256 rewardAmount = totalETHInProtocol - previousTVL;
protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
``` [2](#0-1) 

`rsETHPrice` is the **stored** value from the last call. Between calls it is stale. Any user can deposit via `LRTDepositPool.depositETH()` or `depositAsset()` at this stale price:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

The attacker's deposit increases both `totalETHInProtocol` and `rsethSupply` by exactly offsetting amounts, so the **reward amount is unchanged** by the deposit. However, the new price is now shared across a larger supply, diluting the per-token yield for pre-existing holders while the attacker's rsETH was minted at the cheaper stale price.

**Attack steps (single block, no flash loan required):**
1. Observe that yield has accrued: `totalETHInProtocol > rsethSupply × rsETHPrice`.
2. Call `LRTDepositPool.depositETH{value: X}(minRSETH, "")` — receive `X / rsETHPrice` rsETH at the stale price.
3. Call `LRTOracle.updateRSETHPrice()` — price rises to `newRsETHPrice > rsETHPrice`.
4. Attacker's rsETH is now worth `(X / rsETHPrice) × newRsETHPrice > X` ETH.
5. Sell rsETH on secondary market or wait for withdrawal queue.

The attacker can also simply front-run the protocol's own periodic `updateRSETHPrice()` keeper call without needing to call it themselves.

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders receive a lower price increase than they are entitled to. The attacker captures the difference. Concretely:

| Scenario | newRsETHPrice | Existing holders' gain per rsETH |
|---|---|---|
| No attacker (1000 ETH TVL, 100 ETH yield, 10% fee) | 1.09 ETH | +0.09 ETH |
| Attacker deposits 1000 ETH first | 1.045 ETH | +0.045 ETH |

Existing holders lose 45 ETH of yield; the attacker gains 45 ETH. The loss scales linearly with the attacker's deposit size relative to protocol TVL.

### Likelihood Explanation

**Medium.** The attack requires capital (no flash loan is possible because the withdrawal queue enforces an 8-day delay), but:
- No privileged role is needed.
- `updateRSETHPrice()` is unconditionally public.
- The attacker can front-run the protocol's own keeper call, requiring only mempool monitoring.
- rsETH can be sold on secondary markets immediately, removing the withdrawal-delay constraint.
- The `pricePercentageLimit` guard only blocks calls when the price increase exceeds the configured threshold; normal yield accrual within the threshold is fully exploitable. [4](#0-3) 

### Recommendation

1. **Restrict `updateRSETHPrice()`** to a keeper/manager role so that the timing of price updates cannot be controlled by an attacker.
2. **Alternatively**, snapshot the rsETH supply and TVL at deposit time and exclude newly deposited assets from the current reward window (i.e., apply a one-epoch delay before new deposits participate in yield distribution).
3. **Alternatively**, use a time-weighted average price so that a single block's deposit cannot capture an entire accrued-yield window.

### Proof of Concept

```
Initial state:
  rsethSupply       = 1 000 rsETH
  rsETHPrice        = 1.000 ETH/rsETH  (stale)
  totalETHInProtocol = 1 100 ETH       (100 ETH yield accrued)
  protocolFeeBPS    = 1 000 (10 %)

Step 1 – Attacker deposits 1 000 ETH at stale price:
  rsETH minted      = 1 000 / 1.000 = 1 000 rsETH
  new rsethSupply   = 2 000 rsETH
  new totalETH      = 2 100 ETH

Step 2 – Attacker calls updateRSETHPrice():
  previousTVL       = 2 000 × 1.000 = 2 000 ETH
  reward            = 2 100 − 2 000 = 100 ETH   (unchanged from pre-deposit)
  protocolFee       = 100 × 10 % = 10 ETH
  newRsETHPrice     = (2 100 − 10) / 2 000 = 1.045 ETH/rsETH

Attacker outcome:
  rsETH held        = 1 000
  ETH value         = 1 000 × 1.045 = 1 045 ETH
  Profit            = +45 ETH

Existing holders (1 000 rsETH):
  ETH value         = 1 000 × 1.045 = 1 045 ETH
  Expected (no attack) = 1 000 × 1.09 = 1 090 ETH
  Loss              = −45 ETH

Root cause lines:
  LRTOracle.sol:87   — updateRSETHPrice() public, no access control
  LRTOracle.sol:234  — previousTVL uses stale rsETHPrice
  LRTDepositPool.sol:520 — mint uses stale rsETHPrice
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L228-250)
```text
        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

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
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-267)
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
        }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
