Audit Report

## Title
`maxMintAmountPerDay` Uninitialized to Zero Blocks All rsETH Minting Until Manager Intervenes - (File: contracts/RSETH.sol)

## Summary
`RSETH.sol` declares `maxMintAmountPerDay` as a state variable that defaults to `0`. Neither `initialize()` nor `reinitialize()` assigns it a non-zero value. The `checkDailyMintLimit` modifier unconditionally reverts for any `amount > 0` when `maxMintAmountPerDay == 0`, making `mint()` — the sole path to issue rsETH — permanently non-functional until the manager explicitly calls `setMaxMintAmountPerDay` with a non-zero value.

## Finding Description
`maxMintAmountPerDay` is declared at [1](#0-0)  and defaults to `0`.

`initialize()` sets up ERC-20 and role configuration but never assigns `maxMintAmountPerDay`: [2](#0-1) 

`reinitialize()` sets `periodStartTime` and `custodyAddress` but also omits `maxMintAmountPerDay`: [3](#0-2) 

The `checkDailyMintLimit` modifier enforces: [4](#0-3) 

With `maxMintAmountPerDay == 0`, the condition `currentPeriodMintedAmount + amount > 0` evaluates to `amount > 0`, which is always `true` for any real deposit, causing an unconditional revert with `DailyMintLimitExceeded`.

`mint()` applies this modifier and is the only path to issue rsETH: [5](#0-4) 

The only remedy is `setMaxMintAmountPerDay`, which is never called during initialization: [6](#0-5) 

No existing guard prevents the contract from being used before this value is set.

## Impact Explanation
Every call to `RSETH.mint()` reverts from the moment of deployment until the manager sets a non-zero `maxMintAmountPerDay`. User deposit transactions revert atomically (no funds lost), but the protocol cannot deliver its core promised return — rsETH in exchange for deposited assets. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
The broken state exists from the first block after deployment or upgrade. Any unprivileged depositor who calls `LRTDepositPool.depositAsset()` (or equivalent) before the manager calls `setMaxMintAmountPerDay` will have their transaction revert. No attacker capability is required; the condition is triggered by normal user interaction. The exposure window is unbounded by the contract itself — there is no on-chain enforcement that `setMaxMintAmountPerDay` must be called before deposits are accepted.

## Recommendation
- **Short term:** Set `maxMintAmountPerDay` to a sensible non-zero value inside `initialize()` or `reinitialize()` so the contract is functional immediately upon deployment/upgrade.
- **Long term:** Add a `require(_maxMintAmountPerDay > 0, "zero limit")` guard inside `setMaxMintAmountPerDay` to prevent the manager from accidentally re-blocking all minting by resetting it to `0`.

## Proof of Concept
1. Deploy `RSETH` proxy and call `initialize(admin, lrtConfigAddr)`. `maxMintAmountPerDay` is `0`.
2. Optionally call `reinitialize(periodStartTime, custodyAddress)`. `maxMintAmountPerDay` remains `0`.
3. A user deposits into `LRTDepositPool`, which calls `RSETH.mint(user, 1 ether)`.
4. `checkDailyMintLimit(1 ether)` evaluates `0 + 1 ether > 0` → `true` → reverts with `DailyMintLimitExceeded(1 ether, 0)`.
5. The deposit reverts. No rsETH is minted. Protocol is non-functional until `setMaxMintAmountPerDay(nonZeroValue)` is called.

**Foundry test sketch:**
```solidity
function test_mintRevertsWhenMaxMintAmountPerDayIsZero() public {
    // maxMintAmountPerDay is 0 after initialize()
    assertEq(rsETH.maxMintAmountPerDay(), 0);

    vm.prank(minterRole);
    vm.expectRevert(
        abi.encodeWithSelector(RSETH.DailyMintLimitExceeded.selector, 1 ether, 0)
    );
    rsETH.mint(user, 1 ether);
}
```

### Citations

**File:** contracts/RSETH.sol (L19-19)
```text
    uint256 public maxMintAmountPerDay;
```

**File:** contracts/RSETH.sol (L50-52)
```text
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }
```

**File:** contracts/RSETH.sol (L96-104)
```text
    function initialize(address admin, address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(admin);
        UtilLib.checkNonZeroAddress(lrtConfigAddr);

        __ERC20_init("rsETH", "rsETH");
        __Pausable_init();
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/RSETH.sol (L109-117)
```text
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
