Audit Report

## Title
`FeeReceiver` MEV Rewards Excluded from TVL Before `updateRSETHPrice()`, Causing Understated rsETH Price — (File: contracts/LRTOracle.sol, contracts/FeeReceiver.sol, contracts/LRTDepositPool.sol)

## Summary

`LRTOracle.updateRSETHPrice()` is a public, permissionless function that computes the rsETH/ETH exchange rate without first flushing accumulated MEV rewards from `FeeReceiver` into the deposit pool. Because `getETHDistributionData()` explicitly excludes `FeeReceiver.balance`, any ETH sitting there at the time of a price update is invisible to the TVL snapshot. An attacker can call `updateRSETHPrice()` while `FeeReceiver` holds a large balance, then immediately deposit at the artificially low price to mint excess rsETH, permanently diluting existing holders' yield.

## Finding Description

**Root cause — `FeeReceiver.balance` is never included in TVL until `sendFunds()` is called:**

`FeeReceiver` accumulates ETH via its `receive()` fallback: [1](#0-0) 

The only path for those funds to enter the TVL calculation is `sendFunds()`, which has no access control and is never called atomically with any price update: [2](#0-1) 

**`updateRSETHPrice()` is public and calls `_updateRsETHPrice()` directly, with no flush step:** [3](#0-2) 

`_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which delegates to `ILRTDepositPool.getTotalAssetDeposits(ETH)`, which in turn calls `getETHDistributionData()`: [4](#0-3) 

**`getETHDistributionData()` explicitly documents the exclusion of `FeeReceiver`:** [5](#0-4) 

The six components summed are: deposit pool balance, NDC balances, EigenLayer shares, queued withdrawals, unstaking vault balance, and converter ETH — `FeeReceiver.balance` is absent from all of them: [6](#0-5) 

**The mint calculation uses the stored `rsETHPrice`:** [7](#0-6) 

A lower `rsETHPrice` (because FeeReceiver rewards are excluded) directly inflates `rsethAmountToMint` for every depositor.

**Exploit flow:**
1. MEV rewards accumulate: `FeeReceiver.balance = X ETH`.
2. Attacker calls `LRTOracle.updateRSETHPrice()`. `_getTotalEthInProtocol()` sums all protocol ETH **excluding** `X ETH`. The stored `rsETHPrice` is set to a value lower than the true price.
3. Attacker calls `LRTDepositPool.depositETH{value: D}()`. Mint amount = `D / rsETHPrice` — inflated because `rsETHPrice` is understated.
4. Anyone calls `FeeReceiver.sendFunds()`, moving `X ETH` into the deposit pool.
5. `updateRSETHPrice()` is called again; price rises to reflect the full TVL. The attacker's excess rsETH now represents real value extracted from existing holders.

**Existing guards are insufficient:**

The downside-protection mechanism in `_updateRsETHPrice()` can pause the protocol if the price drop exceeds `pricePercentageLimit`: [8](#0-7) 

However, this guard fails to prevent the attack when: (a) `pricePercentageLimit == 0` (no limit configured), or (b) the FeeReceiver balance is small enough relative to TVL that the price drop stays within the configured threshold. The attacker can simply wait for a moment when the accumulated balance is just below the threshold.

## Impact Explanation

**High — Theft of unclaimed yield.**

When `updateRSETHPrice()` is called while `FeeReceiver` holds unflused rewards, the rsETH price is understated. Every deposit made at the understated price mints excess rsETH. When `sendFunds()` is eventually called and the price is updated, the excess rsETH already issued to new depositors permanently dilutes the yield owed to existing holders. The protocol fee calculation (`rewardAmount = totalETHInProtocol - previousTVL`) is also understated for the same reason, reducing the fee taken on behalf of the treasury. [9](#0-8) 

## Likelihood Explanation

`FeeReceiver` receives ETH continuously from MEV and execution-layer rewards. `updateRSETHPrice()` is a public, permissionless function callable by any EOA or contract. Any depositor can observe `FeeReceiver.balance` on-chain, call `updateRSETHPrice()` at a moment when the balance is large (but within the price-drop threshold), and immediately deposit to capture the inflated rsETH mint. No privileged access is required. The window exists between every MEV reward receipt and the next `sendFunds()` call, and the attack is repeatable.

## Recommendation

Call `FeeReceiver.sendFunds()` (or an equivalent internal flush) at the start of `_updateRsETHPrice()` before computing `_getTotalEthInProtocol()`, so that all accrued MEV rewards are included in the TVL used for the exchange rate:

```solidity
function _updateRsETHPrice() internal {
    // Flush pending MEV rewards into the deposit pool first
    address feeReceiver = lrtConfig.getContract(LRTConstants.REWARD_RECEIVER);
    IFeeReceiver(feeReceiver).sendFunds();

    address rsETHTokenAddress = lrtConfig.rsETH();
    // ... rest of function unchanged
}
```

This ensures the TVL snapshot always includes all accrued rewards and eliminates the exploitable gap between reward accumulation and price computation.

## Proof of Concept

**Minimal call sequence (no fork required):**

1. Deploy protocol with `rsethSupply = 10_000e18`, true TVL = `10_500 ETH` (500 ETH sitting in `FeeReceiver`, 10_000 ETH elsewhere). Correct price = `1.05e18`.
2. Call `LRTOracle.updateRSETHPrice()` — `_getTotalEthInProtocol()` returns `10_000 ETH` (FeeReceiver excluded). Stored `rsETHPrice` = `1.00e18`.
3. Call `LRTDepositPool.depositETH{value: 100 ether}(0, "")` — mints `100e18 * 1e18 / 1.00e18 = 100 rsETH` instead of the correct `≈95.24 rsETH`. Attacker receives `≈4.76` excess rsETH.
4. Call `FeeReceiver.sendFunds()` — 500 ETH moves to deposit pool.
5. Call `LRTOracle.updateRSETHPrice()` — price rises to reflect full TVL. Attacker's excess rsETH now represents real value extracted from existing holders.

**Foundry invariant test plan:** Assert that for any sequence of `{sendFunds, updateRSETHPrice, depositETH}` calls, the rsETH minted per unit of ETH deposited never exceeds `1e18 / truePrice` (where `truePrice` includes `FeeReceiver.balance`). The invariant will break whenever `updateRSETHPrice` is called before `sendFunds` while `FeeReceiver.balance > 0`.

### Citations

**File:** contracts/FeeReceiver.sol (L49-50)
```text
    /// @dev fallback to receive funds
    receive() external payable { }
```

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L331-348)
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
```

**File:** contracts/LRTDepositPool.sol (L464-466)
```text
    /// @dev provides ETH amount distribution data among depositPool, NDCs and eigenLayer
    /// @dev rewards are not accounted here
    /// it will automatically be accounted once it is moved from feeReceiver/rewardReceiver to depositPool
```

**File:** contracts/LRTDepositPool.sol (L480-499)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
