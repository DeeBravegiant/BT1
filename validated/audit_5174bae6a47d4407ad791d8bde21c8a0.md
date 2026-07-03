### Title
Uninitialized `maxMintAmountPerDay` Causes `RSETH.mint()` to Always Revert, Blocking All L1 Deposits — (File: contracts/RSETH.sol)

---

### Summary

`RSETH.mint()` is gated by the `checkDailyMintLimit` modifier. When `maxMintAmountPerDay` is zero (its Solidity default, never set in `initialize()` or `reinitialize()`), every call to `mint()` with a non-zero amount reverts with `DailyMintLimitExceeded`. Because `LRTDepositPool.depositETH()` and `depositAsset()` both terminate in `RSETH.mint()`, all L1 deposits are permanently blocked until an admin separately calls `setMaxMintAmountPerDay`.

---

### Finding Description

`RSETH.initialize()` sets only `lrtConfig` and ERC20/Pausable state. `RSETH.reinitialize()` sets only `periodStartTime` and `custodyAddress`. Neither sets `maxMintAmountPerDay`, which therefore remains `0`. [1](#0-0) 

The `checkDailyMintLimit` modifier then executes:

```solidity
if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
    revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
}
``` [2](#0-1) 

With `maxMintAmountPerDay == 0`, the condition reduces to `amount > 0`, which is always true for any real deposit. The revert fires unconditionally.

`RSETH.mint()` applies this modifier: [3](#0-2) 

`LRTDepositPool._mintRsETH()` calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)`: [4](#0-3) 

Which is invoked by both public deposit entry points: [5](#0-4) 

The inconsistency is made explicit by `remainingDailyMintLimit()`, which correctly short-circuits on `maxMintAmountPerDay == 0` — but the modifier that actually guards minting does not: [6](#0-5) 

`setMaxMintAmountPerDay` is a separate, manually-triggered manager call with no enforcement that it is called before deposits open: [7](#0-6) 

---

### Impact Explanation

Every call to `LRTDepositPool.depositETH()` or `depositAsset()` reverts with `DailyMintLimitExceeded(amount, 0)` until `setMaxMintAmountPerDay` is explicitly called. This is a **temporary freezing of funds** (Medium): users cannot deposit assets into the protocol, and the L1 deposit pool is non-functional. The same revert also blocks `LRTOracle._updateRsETHPrice()` from minting protocol fees via `RSETH.mint()` when `protocolFeeInBPS > 0`, staling the rsETH price.

---

### Likelihood Explanation

The scenario is realistic at any contract upgrade where `reinitialize()` is called but `setMaxMintAmountPerDay` is omitted from the post-upgrade checklist. Because `maxMintAmountPerDay` is not part of either initializer, it silently defaults to zero. Any depositor immediately discovers the failure on the first transaction.

---

### Recommendation

Set a non-zero default for `maxMintAmountPerDay` inside `initialize()` (or `reinitialize()`), or add an early-return guard in `checkDailyMintLimit` that treats `maxMintAmountPerDay == 0` as "no limit" (consistent with how `remainingDailyMintLimit()` already behaves):

```solidity
modifier checkDailyMintLimit(uint256 amount) {
    if (maxMintAmountPerDay != 0) {
        if (block.timestamp >= periodStartTime + 1 days) { ... }
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(...);
        }
        currentPeriodMintedAmount += amount;
    }
    _;
}
```

---

### Proof of Concept

1. Deploy `RSETH` and `LRTDepositPool` without calling `setMaxMintAmountPerDay`.
2. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")`.
3. Execution reaches `RSETH.mint(user, rsethAmountToMint)`.
4. `checkDailyMintLimit` evaluates `rsethAmountToMint > 0` (since `maxMintAmountPerDay == 0`).
5. Transaction reverts: `DailyMintLimitExceeded(rsethAmountToMint, 0)`.
6. All deposits are blocked until an admin calls `setMaxMintAmountPerDay(nonZeroValue)`.

### Citations

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

**File:** contracts/RSETH.sol (L96-117)
```text
    function initialize(address admin, address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(admin);
        UtilLib.checkNonZeroAddress(lrtConfigAddr);

        __ERC20_init("rsETH", "rsETH");
        __Pausable_init();
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }

    /// @notice Initializes the contract with a period start time and the custody address
    /// @param _periodStartTime The period start time
    /// @param _custodyAddress The custody address for recovered funds
    function reinitialize(uint256 _periodStartTime, address _custodyAddress) external reinitializer(2) onlyLRTManager {
        if (_periodStartTime > block.timestamp || _periodStartTime <= block.timestamp - 1 days) {
            revert PeriodStartTimeShouldBeWithin24Hours();
        }
        periodStartTime = _periodStartTime;
        emit PeriodStartTimeSet(_periodStartTime);

        _setCustodyAddress(_custodyAddress);
    }
```

**File:** contracts/RSETH.sol (L125-128)
```text
    function setMaxMintAmountPerDay(uint256 _maxMintAmountPerDay) external onlyLRTManager {
        maxMintAmountPerDay = _maxMintAmountPerDay;
        emit MaxMintAmountPerDayUpdated(_maxMintAmountPerDay);
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

**File:** contracts/RSETH.sol (L265-272)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        if (maxMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyMintAmount = (block.timestamp >= periodStartTime + 1 days) ? 0 : currentPeriodMintedAmount;

        return maxMintAmountPerDay > effectiveDailyMintAmount ? maxMintAmountPerDay - effectiveDailyMintAmount : 0;
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

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
    }
```
