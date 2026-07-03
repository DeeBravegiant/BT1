Audit Report

## Title
Silent Zero-Mint on Small Deposits Permanently Locks User Funds - (File: contracts/LRTDepositPool.sol)

## Summary
When a deposit amount is too small for `getRsETHAmountToMint()` to produce a non-zero result due to integer division truncation, `_beforeDeposit()` returns `rsethAmountToMint = 0`. If the caller passes `minRSETHAmountExpected = 0`, no revert occurs, the user's ETH or LST is accepted by the contract, and `_mintRsETH(0)` silently mints nothing. The depositor's funds are permanently locked with no recovery path.

## Finding Description

`getRsETHAmountToMint()` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

When `amount * assetPrice < rsETHPrice`, Solidity truncates the result to `0`.

`_beforeDeposit()` has two guards. The first:

```solidity
if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
    revert InvalidAmountToDeposit();
}
``` [2](#0-1) 

`minAmountToDeposit` is never set in `initialize()`, so it defaults to `0`. Any non-zero `depositAmount` passes this check. [3](#0-2) 

The second guard:

```solidity
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
``` [4](#0-3) 

When `rsethAmountToMint = 0` and `minRSETHAmountExpected = 0`, the condition `0 < 0` is false — no revert.

For `depositETH`, ETH arrives via `msg.value` before any logic runs. For `depositAsset`, `safeTransferFrom` executes after `_beforeDeposit` but before `_mintRsETH`: [5](#0-4) 

`_mintRsETH(0)` then calls `RSETH.mint(msg.sender, 0)`: [6](#0-5) 

The `checkDailyMintLimit` modifier in `RSETH.mint` does not revert on `amount = 0` (the condition `currentPeriodMintedAmount + 0 > maxMintAmountPerDay` is not triggered under normal operation), and OpenZeppelin's `_mint(to, 0)` is a no-op: [7](#0-6) [8](#0-7) 

The user ends up with 0 rsETH and no mechanism to reclaim their deposited ETH or LST.

## Impact Explanation

The depositor's funds are permanently transferred into `LRTDepositPool` with zero rsETH minted in return. There is no user-accessible withdrawal function for a holder of 0 rsETH. The locked ETH/LST marginally inflates the rsETH price for existing holders. This constitutes **Critical: Permanent freezing of funds** (and equivalently, direct permanent loss of user funds).

## Likelihood Explanation

rsETH is designed to appreciate over time via staking rewards, so `rsETHPrice > 1e18` is an expected, normal protocol state — not an edge case. Once that threshold is crossed, any deposit of 1 wei of ETH triggers the bug. The vulnerable range widens as rsETH appreciates further. Any unprivileged caller — including naive integrators, bots, or users who omit the slippage parameter — can trigger this with no special preconditions. The condition is repeatable and permanent per affected deposit.

## Recommendation

Add an explicit zero-shares guard in `_beforeDeposit()` immediately after computing `rsethAmountToMint`:

```solidity
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint == 0) revert ZeroRsETHMinted();
```

Additionally, set a non-zero `minAmountToDeposit` in `initialize()` calibrated to always produce at least 1 rsETH at the current price, and keep it updated as rsETH appreciates.

## Proof of Concept

1. Protocol operates normally; rsETH appreciates to `rsETHPrice = 1.5e18` (1.5 ETH per rsETH).
2. User calls `LRTDepositPool.depositETH{value: 1}(0, "")`.
3. `_beforeDeposit(ETH_TOKEN, 1, 0)`:
   - `1 == 0` → false; `1 < 0` → false. Passes.
   - `getRsETHAmountToMint(ETH_TOKEN, 1)` → `(1 * 1e18) / 1.5e18` → `0`.
   - `0 < 0` → false. No revert.
4. `_mintRsETH(0)` → `RSETH.mint(msg.sender, 0)` → `checkDailyMintLimit(0)` passes → `_mint(msg.sender, 0)` → no-op.
5. User's 1 wei ETH is permanently locked in `LRTDepositPool`; user holds 0 rsETH.

**Foundry fuzz test sketch:**
```solidity
function testFuzz_zeroMintLocksFunds(uint256 depositAmount) public {
    vm.assume(depositAmount > 0 && depositAmount < rsETHPrice / assetPrice);
    uint256 balBefore = address(depositPool).balance;
    vm.deal(user, depositAmount);
    vm.prank(user);
    depositPool.depositETH{value: depositAmount}(0, "");
    assertEq(rsETH.balanceOf(user), 0);
    assertEq(address(depositPool).balance, balBefore + depositAmount);
}
```

### Citations

**File:** contracts/LRTDepositPool.sol (L45-52)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        __Pausable_init();
        __ReentrancyGuard_init();
        maxNodeDelegatorLimit = 10;
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTDepositPool.sol (L111-115)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L657-659)
```text
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
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

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
    }
```
