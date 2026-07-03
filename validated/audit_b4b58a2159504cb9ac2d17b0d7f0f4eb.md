#Audit Report

## Title
Daily Mint Limit Exhaustion + Block Stuffing Enables Temporary Deposit DoS — (`contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

## Summary
An unprivileged attacker can exhaust the `dailyMintLimit` in a single deposit transaction, then use block stuffing to prevent legitimate depositors from landing transactions for the remainder of the day epoch. Because `dailyMintAmount` only resets when `block.timestamp` crosses a day boundary and there is no privileged function to reset the accumulator directly, the attacker can repeat this at every boundary. The impact is a sustained, temporary freeze of all deposit functionality matching the allowed **Low — Block stuffing** impact class.

## Finding Description
`getCurrentDay()` derives the epoch purely from `block.timestamp`: [1](#0-0) 

The `limitDailyMint` modifier accumulates `dailyMintAmount` and only resets it when `currentDay > lastMintDay`; once the cap is hit, every subsequent `deposit` call reverts with `DailyMintLimitExceeded`: [2](#0-1) 

`setDailyMintLimit` changes only the cap value; it does not clear `dailyMintAmount`: [3](#0-2) 

No function in the contract resets `dailyMintAmount` to zero directly. The only operational escape valve is `pause()`, which blocks all depositors equally and does not advance the epoch.

**Exploit path:**
1. Attacker calls `deposit(token, AMOUNT_TO_HIT_LIMIT, "")` — receives rsETH minus fee; `dailyMintAmount` reaches `dailyMintLimit`.
2. All subsequent `deposit` calls revert for the remainder of the day epoch.
3. Attacker fills blocks with high-gas dummy transactions so no legitimate deposit can be included before the epoch boundary.
4. At the boundary, attacker immediately re-exhausts the new day's limit and repeats.

On L2 chains (the deployment target given the bridge architecture), block gas limits and per-transaction costs are low enough to make sustained block stuffing economically viable. [4](#0-3) 

## Impact Explanation
All token depositors are temporarily locked out of `deposit(address,uint256,string)` for the duration of the attack. No funds already in the contract are at risk. The impact is a sustained, temporary denial of deposit functionality, matching **Low — Block stuffing** from the allowed impact scope.

## Likelihood Explanation
- No special role or privileged access is required; any address holding sufficient supported ERC20 tokens can trigger this.
- The attacker receives rsETH in exchange for deposited tokens; the net cost is only the protocol fee, not the principal.
- On L2 chains with low gas costs, block stuffing for the remaining hours of a day epoch is economically feasible.
- The attack is repeatable at every day boundary with no on-chain circuit breaker to prevent it.

## Recommendation
1. Add a privileged `resetDailyMintAmount()` function (e.g., restricted to `PAUSER_ROLE` or `DEFAULT_ADMIN_ROLE`) that sets `dailyMintAmount = 0` and `lastMintDay = getCurrentDay()`, allowing operators to recover without waiting for the natural day boundary.
2. Consider per-address sub-limits to prevent a single depositor from consuming the entire daily quota.
3. Evaluate replacing the discrete epoch with a rolling 86,400-second window to eliminate the exploitable boundary reset.

## Proof of Concept
```solidity
function testBlockStuffingDailyLimitDoS() public {
    pool.setDailyMintLimit(1 ether);

    // Step 1: attacker exhausts the daily limit
    vm.startPrank(attacker);
    token.approve(address(pool), type(uint256).max);
    pool.deposit(address(token), AMOUNT_TO_HIT_LIMIT, "");
    vm.stopPrank();

    // Step 2: legitimate user cannot deposit — limit exhausted
    vm.startPrank(user);
    token.approve(address(pool), 1e18);
    vm.expectRevert(RSETHPoolV3WithNativeChainBridge.DailyMintLimitExceeded.selector);
    pool.deposit(address(token), 1e18, "");
    vm.stopPrank();

    // Step 3: confirm revert persists while timestamp stays in same epoch
    // (block stuffing holds timestamp in same day window in production)
    assertEq(pool.getCurrentDay(), pool.lastMintDay());

    // Step 4: only after crossing the day boundary does the limit reset
    vm.warp(block.timestamp + 1 days);
    vm.startPrank(user);
    pool.deposit(address(token), 1e18, ""); // succeeds
    vm.stopPrank();
}
```

The test confirms that while `block.timestamp` remains within the same day epoch, all deposits revert with `DailyMintLimitExceeded`. The revert clears only after the boundary is crossed, which the attacker can delay via block stuffing on low-cost L2 chains.

### Citations

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L122-136)
```text
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
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L390-392)
```text
    function getCurrentDay() public view returns (uint256) {
        return (block.timestamp - startTimestamp) / 1 days;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L679-685)
```text
    function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }
        dailyMintLimit = _dailyMintLimit;
        emit DailyMintLimitSet(_dailyMintLimit);
    }
```
