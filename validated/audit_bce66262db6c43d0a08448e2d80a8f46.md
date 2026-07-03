### Title
MEV/Execution-Layer Rewards Held in `FeeReceiver` Are Excluded from rsETH Price, Enabling Permissionless Yield Theft - (File: `contracts/FeeReceiver.sol`, `contracts/LRTOracle.sol`)

---

### Summary

MEV and execution-layer rewards accumulate in `FeeReceiver` but are never included in the rsETH price calculation until `FeeReceiver.sendFunds()` is explicitly called. Because both `sendFunds()` and `LRTOracle.updateRSETHPrice()` are permissionless, an attacker can atomically: (1) deposit at the stale (lower) rsETH price, (2) trigger `sendFunds()` to move the rewards into the deposit pool, and (3) trigger `updateRSETHPrice()` to capture the price increase — stealing a proportional share of the pending MEV yield from all existing rsETH holders.

---

### Finding Description

**Step 1 — Rewards are excluded from TVL.**

`LRTOracle._getTotalEthInProtocol()` computes the rsETH price by summing asset balances across the deposit pool, NDCs, and EigenLayer strategies: [1](#0-0) 

It calls `ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset)`, which for ETH delegates to `getETHDistributionData()`: [2](#0-1) 

The code comment on line 465–466 explicitly acknowledges this gap:

> `/// @dev rewards are not accounted here`
> `/// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool`

The `FeeReceiver` contract balance is never queried. Any ETH sitting there is invisible to the price oracle.

**Step 2 — `sendFunds()` is permissionless.**

`FeeReceiver.sendFunds()` has no access control: [3](#0-2) 

Any external caller can trigger the transfer of the full `FeeReceiver` balance into `LRTDepositPool` at any time.

**Step 3 — `updateRSETHPrice()` is permissionless.** [4](#0-3) 

Any external caller can trigger a price update after the rewards land in the deposit pool.

**Step 4 — Deposits use the stored (stale) price.**

`getRsETHAmountToMint()` uses `lrtOracle.rsETHPrice()`, which is the last stored value — not a freshly computed one: [5](#0-4) 

This means a depositor who acts before `sendFunds()` + `updateRSETHPrice()` are called receives rsETH priced as if the pending MEV rewards do not exist.

---

### Impact Explanation

**Impact: High — Theft of unclaimed yield.**

An attacker who deposits before the MEV rewards are flushed into the deposit pool receives rsETH at a price that does not reflect those rewards. After triggering `sendFunds()` and `updateRSETHPrice()`, the price rises and the attacker's rsETH is worth more than they paid. The difference is extracted from the yield that should have accrued pro-rata to all existing rsETH holders. The attacker can realize the gain by selling rsETH on a secondary market (e.g., a DEX) or by eventually withdrawing through the withdrawal manager.

The magnitude of the theft scales with the size of the accumulated MEV rewards in `FeeReceiver` relative to total TVL.

---

### Likelihood Explanation

**Likelihood: Medium.**

- MEV and execution-layer rewards accumulate in `FeeReceiver` continuously as validators produce blocks.
- The attack requires no special permissions; both `sendFunds()` and `updateRSETHPrice()` are callable by any EOA or contract.
- The attacker only needs to monitor the `FeeReceiver` balance on-chain and act when it is large enough to be profitable.
- The `pricePercentageLimit` guard in `_updateRsETHPrice()` may revert the price update if the reward-driven increase exceeds the configured threshold, but: (a) the limit may be set to 0 (disabled), (b) small or moderate reward accumulations will stay within the limit, and (c) the attacker can wait for multiple reward cycles to accumulate before striking. [6](#0-5) 

---

### Recommendation

Include the `FeeReceiver` balance in the ETH TVL calculation inside `getETHDistributionData()`, or alternatively, include it directly in `_getTotalEthInProtocol()`. This mirrors the fix applied in the referenced Mellow Protocol report (adding `tokensOwed` to `tvl`). Concretely:

```solidity
// In getETHDistributionData() or _getTotalEthInProtocol():
address rewardReceiver = lrtConfig.getContract(LRTConstants.LRT_REWARD_RECEIVER);
ethLyingInProtocol += rewardReceiver.balance;
```

This ensures that pending MEV rewards are always priced into rsETH, eliminating the window in which a depositor can acquire rsETH at a price that excludes accrued-but-undelivered yield.

---

### Proof of Concept

**Given:**
- Current rsETH price: `1.05 ETH` per rsETH (stored in `LRTOracle.rsETHPrice`)
- Total ETH in protocol (TVL): `10,000 ETH`
- Pending MEV rewards sitting in `FeeReceiver`: `100 ETH` (not in TVL)

**Attack steps:**

1. Attacker calls `LRTDepositPool.depositETH{value: 1050 ETH}(...)`. At the current price of `1.05 ETH/rsETH`, they receive `1000 rsETH`. The attacker now holds `~9.09%` of total rsETH supply.

2. Attacker calls `FeeReceiver.sendFunds()`. The `100 ETH` moves to `LRTDepositPool`. TVL is now `11,150 ETH` (10,000 + 1,050 deposited + 100 MEV).

3. Attacker calls `LRTOracle.updateRSETHPrice()`. The new price is computed as `11,150 ETH / (total rsETH supply)`. The price rises above `1.05 ETH/rsETH` because the 100 ETH MEV reward is now included.

4. The attacker's `1000 rsETH` is now worth approximately `1050 + (9.09% × 100) ≈ 1059 ETH` — a gain of ~`9 ETH` extracted from the MEV rewards that should have accrued to all pre-existing rsETH holders.

5. Attacker sells rsETH on a secondary market or initiates withdrawal to realize the gain.

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
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

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L464-500)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
    function getETHDistributionData()
        public
        view
        override
        returns (
            uint256 ethLyingInDepositPool,
            uint256 ethLyingInNDCs,
            uint256 ethStakedInEigenLayer,
            uint256 ethUnstakingFromEigenLayer,
            uint256 ethLyingInConverter,
            uint256 ethLyingInUnstakingVault
        )
    {
        ethLyingInDepositPool = address(this).balance;

        uint256 ndcsCount = nodeDelegatorQueue.length;

        for (uint256 i; i < ndcsCount;) {
            ethLyingInNDCs += nodeDelegatorQueue[i].balance;

            ethStakedInEigenLayer += INodeDelegator(nodeDelegatorQueue[i]).getEffectivePodShares();
            ethUnstakingFromEigenLayer += INodeDelegator(nodeDelegatorQueue[i])
                .getAssetUnstaking(LRTConstants.ETH_TOKEN);
            unchecked {
                ++i;
            }
        }

        address lrtUnstakingVault = lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT);
        ethLyingInUnstakingVault = lrtUnstakingVault.balance;

        address lrtConverter = lrtConfig.getContract(LRTConstants.LRT_CONVERTER);
        ethLyingInConverter = ILRTConverter(lrtConverter).ethValueInWithdrawal();
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
