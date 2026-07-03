Audit Report

## Title
Global Shared Daily Mint Rate Limit Can Be Exhausted by a Single Depositor, Temporarily Blocking All Deposits - (File: contracts/RSETH.sol)

## Summary
`RSETH.sol` enforces a single protocol-wide daily mint cap via `checkDailyMintLimit`. Because `currentPeriodMintedAmount` is a global accumulator with no per-user sub-limit, any depositor whose single transaction maps to an rsETH amount that fills the remaining daily budget will cause every subsequent `depositETH` or `depositAsset` call to revert with `DailyMintLimitExceeded` for up to 24 hours. The same structural pattern is independently present in the L2 pool contracts.

## Finding Description
`RSETH.mint()` applies `checkDailyMintLimit` before minting:

```solidity
// contracts/RSETH.sol L42-56
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
    }
    currentPeriodMintedAmount += amount;
    _;
}
```

`currentPeriodMintedAmount` is a single storage variable shared across all callers. [1](#0-0) 

`LRTDepositPool.depositETH()` and `depositAsset()` both call `_mintRsETH()`, which calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)`, applying the shared modifier. [2](#0-1) 

There is no per-user sub-limit, no maximum single-transaction cap, and no mechanism to reserve capacity for other users. A depositor sending ETH or LSTs whose rsETH equivalent equals or exceeds `maxMintAmountPerDay - currentPeriodMintedAmount` atomically sets `currentPeriodMintedAmount` to `maxMintAmountPerDay`, causing every subsequent deposit in the same 24-hour window to revert. [3](#0-2) 

The identical pattern exists in `RSETHPoolV3.limitDailyMint` with `dailyMintAmount` / `dailyMintLimit`. [4](#0-3) 

## Impact Explanation
All users are blocked from depositing ETH or LSTs into the L1 protocol for up to 24 hours after the limit is exhausted. The deposit path via `LRTDepositPool` is the only way for users to obtain rsETH from L1. This constitutes a **temporary freezing of the deposit functionality** — users cannot enter the protocol during the blackout window. Impact: **Medium. Temporary freezing of funds.**

## Likelihood Explanation
`maxMintAmountPerDay` is a finite value set by the LRT manager. Any depositor whose intended deposit maps to an rsETH amount that fills the remaining daily budget can trigger this, with or without malicious intent. Large institutional depositors or protocols routinely move hundreds or thousands of ETH in a single transaction. A deliberate attacker can also front-run the daily reset to immediately exhaust the new period's budget at zero cost beyond gas. The condition is realistically reachable without coordination.

## Recommendation
Replace the single global accumulator with a per-depositor sub-limit, or enforce a maximum single-transaction deposit cap so no single call can consume the entire daily budget. Alternatively, remove the global daily mint cap from `RSETH.mint()` and rely solely on the per-asset `depositLimitByAsset` ceiling in `LRTConfig`, which is a cumulative cap rather than a rolling time-window cap. [5](#0-4) 

## Proof of Concept
1. `maxMintAmountPerDay` is set to `X` rsETH (e.g., 1 000 rsETH).
2. Whale calls `LRTDepositPool.depositETH{value: V}(0, "")` where `V` is large enough that `getRsETHAmountToMint(ETH_TOKEN, V) >= X`.
3. `_mintRsETH(X)` → `RSETH.mint(whale, X)` → `checkDailyMintLimit(X)`: `currentPeriodMintedAmount` becomes `X == maxMintAmountPerDay`.
4. Any subsequent call to `depositETH` or `depositAsset` by any user within the same 24-hour window evaluates `currentPeriodMintedAmount + amount > maxMintAmountPerDay` as `true` for any non-zero `amount` and reverts with `DailyMintLimitExceeded`.
5. All users are locked out of deposits for up to 24 hours.

**Foundry fork test plan:**
```solidity
function testWhaleExhaultsDailyLimit() public fork {
    uint256 limit = rsETH.maxMintAmountPerDay();
    // deposit enough ETH to mint exactly `limit` rsETH
    uint256 ethNeeded = limit * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(ETH_TOKEN);
    vm.deal(whale, ethNeeded);
    vm.prank(whale);
    depositPool.depositETH{value: ethNeeded}(0, "");
    assertEq(rsETH.currentPeriodMintedAmount(), limit);

    // any subsequent depositor reverts
    vm.deal(alice, 1 ether);
    vm.prank(alice);
    vm.expectRevert(abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector, ...));
    depositPool.depositETH{value: 1 ether}(0, "");
}
``` [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/RSETH.sol (L19-25)
```text
    uint256 public maxMintAmountPerDay;

    /// @notice Amount minted in the current 24-hour period
    uint256 public currentPeriodMintedAmount;

    /// @notice Start time of the current 24-hour period
    uint256 public periodStartTime;
```

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L96-125)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```
