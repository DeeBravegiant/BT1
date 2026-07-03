### Title
Unit Mismatch in `withinUnstakeLimits` Allows Operator to Over-Unstake stETH Relative to ETH Withdrawal Obligations - (`contracts/LRTConverter.sol`)

### Summary

The `withinUnstakeLimits` modifier in `LRTConverter.sol` compares `amountToUnstake` (denominated in **stETH**) directly against `availableActiveETHWithdrawals` (denominated in **ETH**) without any price conversion. When stETH/ETH deviates from 1.0, the guard permits the operator to queue more ETH-value of stETH into Lido's withdrawal queue than the protocol's actual ETH withdrawal obligations justify.

### Finding Description

`_getActiveETHUserWithdrawals()` reads `assetsCommitted[ETH_TOKEN]` from `LRTWithdrawalManager`, which is populated in `initiateWithdrawal` as `expectedAssetAmount` for the ETH asset — a value denominated in **ETH (wei)**. [1](#0-0) [2](#0-1) 

`whitelistedUnstakeAllowance` is set by `declareWithdrawalIntent(amount)`, where `amount` is documented as "Amount of **stETH** to declare for withdrawal." [3](#0-2) 

The guard at line 65 then adds these two heterogeneous quantities and compares against `amountToUnstake` (stETH):

```solidity
if (amountToUnstake > whitelistedUnstakeAllowance + availableActiveETHWithdrawals) {
    revert UnstakeLimitExceeded();
}
``` [4](#0-3) 

The addition `whitelistedUnstakeAllowance (stETH) + availableActiveETHWithdrawals (ETH)` is numerically meaningless when stETH/ETH ≠ 1.0. No oracle price is consulted anywhere in this modifier.

### Impact Explanation

When stETH/ETH > 1.0 (e.g., 1.05):

| Step | Value |
|---|---|
| `assetsCommitted[ETH_TOKEN]` | 100e18 ETH |
| `_getActiveETHUserWithdrawals()` | 100e18 (ETH units) |
| Operator calls `unstakeStEth(100e18)` | 100 stETH = 105 ETH in value |
| Guard check: `100e18 > 0 + 100e18` | **false → passes** |
| stETH queued to Lido | 105 ETH-equivalent |
| ETH committed to users | 100 ETH |
| Over-unstaking | 5 ETH-equivalent |

The excess stETH is locked in Lido's withdrawal queue for the duration of the unbonding period, temporarily reducing the LST balance available for other protocol operations. No funds are permanently lost (the ETH eventually returns), matching the **Low** scope: *contract fails to deliver promised returns, but doesn't lose value*.

### Likelihood Explanation

- stETH/ETH deviates from 1.0 routinely (even small deviations of 0.1–5% are common on-chain).
- The operator does not need to act maliciously; they naturally pass the ETH-denominated committed amount as the stETH amount to unstake, triggering the mismatch unintentionally.
- No special preconditions beyond normal protocol operation (users initiating ETH withdrawals, operator running the unstake routine).

### Recommendation

Convert `availableActiveETHWithdrawals` from ETH units to stETH units before the comparison, using the stETH/ETH oracle price:

```solidity
uint256 stEthPrice = lrtOracle.getAssetPrice(address(stETH)); // stETH per ETH, 1e18-scaled
uint256 activeETHWithdrawalsInStETH = (availableActiveETHWithdrawals * 1e18) / stEthPrice;

if (amountToUnstake > whitelistedUnstakeAllowance + activeETHWithdrawalsInStETH) {
    revert UnstakeLimitExceeded();
}
```

This ensures the budget comparison is always in homogeneous stETH units.

### Proof of Concept

```solidity
// Local fork test (no mainnet)
// Setup: stETH mock with price = 1.05e18 ETH per stETH
// assetsCommitted[ETH_TOKEN] = 100e18 (set via initiateWithdrawal for ETH asset)
// whitelistedUnstakeAllowance = 0

uint256 amountToUnstake = 100e18; // 100 stETH = 105 ETH in value

// withinUnstakeLimits check:
// availableActiveETHWithdrawals = 100e18 (ETH units)
// 100e18 > 0 + 100e18 → false → NO REVERT

// _unstakeStEth sends 100 stETH (worth 105 ETH) to Lido queue
// Invariant violated: 105 ETH-value queued > 100 ETH committed to users

assertEq(lrtConverter.unstakeStEth(amountToUnstake), /* succeeds */);
// Assert: ETH-value of stETH queued (105e18) > assetsCommitted[ETH_TOKEN] (100e18)
``` [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTConverter.sol (L58-77)
```text
    modifier withinUnstakeLimits(uint256 amountToUnstake) {
        if (amountToUnstake == 0) {
            revert InvalidAmount();
        }

        uint256 availableActiveETHWithdrawals = _getActiveETHUserWithdrawals();

        if (amountToUnstake > whitelistedUnstakeAllowance + availableActiveETHWithdrawals) {
            revert UnstakeLimitExceeded();
        }

        // Consume intended withdrawal limit
        if (whitelistedUnstakeAllowance > 0) {
            uint256 whitelistedAmountConsumed =
                amountToUnstake > whitelistedUnstakeAllowance ? whitelistedUnstakeAllowance : amountToUnstake;

            whitelistedUnstakeAllowance -= whitelistedAmountConsumed;
        }
        _;
    }
```

**File:** contracts/LRTConverter.sol (L170-177)
```text
    function unstakeStEth(uint256 amountToUnstake)
        external
        nonReentrant
        onlyLRTOperator
        withinUnstakeLimits(amountToUnstake)
    {
        _unstakeStEth(amountToUnstake);
    }
```

**File:** contracts/LRTConverter.sol (L214-226)
```text
    /// @notice Declare withdrawal intent (only whitelisted users)
    /// @param amount Amount of stETH to declare for withdrawal
    function declareWithdrawalIntent(uint256 amount) external nonReentrant onlyWhitelistedUser {
        if (amount == 0) {
            revert InvalidAmount();
        }
        uint256 maxWhitelistedAllowance = 1_000_000_000 ether;
        if (whitelistedUnstakeAllowance + amount > maxWhitelistedAllowance) {
            revert WhitelistedAllowanceExceeded();
        }

        whitelistedUnstakeAllowance = whitelistedUnstakeAllowance + amount;
        emit WithdrawalIntentDeclared(msg.sender, amount);
```

**File:** contracts/LRTConverter.sol (L266-270)
```text
    function _getActiveETHUserWithdrawals() internal view returns (uint256 activeETHWithdrawals) {
        ILRTWithdrawalManager lrtWithdrawalManager =
            ILRTWithdrawalManager(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));
        activeETHWithdrawals = lrtWithdrawalManager.assetsCommitted(LRTConstants.ETH_TOKEN);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L172-173)
```text
        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```
