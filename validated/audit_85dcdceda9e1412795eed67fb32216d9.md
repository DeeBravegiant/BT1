Audit Report

## Title
`dailyMintAmount` Not Decremented on wrsETH Burn Enables Daily Limit Exhaustion - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3.sol)

## Summary
Both `RSETHPoolV3` and `RSETHPoolV3ExternalBridge` enforce a daily minting cap via the `limitDailyMint` modifier, which increments `dailyMintAmount` on every `deposit()` call. However, `RsETHTokenWrapper._withdraw()` burns wrsETH and returns altRsETH with no callback to the pool, leaving `dailyMintAmount` permanently elevated. An attacker with altRsETH can cycle deposits and withdrawals to exhaust the daily limit at a cost of only fees plus gas, blocking all other depositors for up to 24 hours.

## Finding Description
In `RSETHPoolV3ExternalBridge`, the `limitDailyMint` modifier increments `dailyMintAmount += rsETHAmount` before executing the function body, and the only reset is `dailyMintAmount = 0` when a new day begins. [1](#0-0) [2](#0-1) 

The pool mints wrsETH by calling `wrsETH.mint()` directly on the wrapper, which does not deposit any altRsETH into the wrapper's balance. [3](#0-2) [4](#0-3) 

`RsETHTokenWrapper._withdraw()` is a public, permissionless path that burns wrsETH and transfers altRsETH 1:1 with no notification to the pool. [5](#0-4) 

The exploit cycle:
1. Attacker calls `wrapper.deposit(altRsETH, N)` to seed the wrapper with altRsETH, receiving N wrsETH.
2. Attacker calls `pool.deposit{value: X}()` → receives Z wrsETH; `dailyMintAmount += Z`.
3. Attacker calls `wrapper.withdraw(altRsETH, Z)` → burns Z wrsETH, recovers Z altRsETH; `dailyMintAmount` unchanged.
4. Attacker calls `wrapper.deposit(altRsETH, Z)` to restore the wrapper's altRsETH balance.
5. Repeat steps 2–4 until `dailyMintAmount == dailyMintLimit`.

Each iteration costs only `feeBps` of the deposited ETH plus gas. The attacker's altRsETH seed is recovered at the end. No privileged role is required.

## Impact Explanation
Once `dailyMintAmount` reaches `dailyMintLimit`, every subsequent call to `pool.deposit()` reverts with `DailyMintLimitExceeded` until the next day's automatic reset. This constitutes **Temporary freezing of funds (Medium)**: legitimate depositors are completely locked out of the pool for up to 24 hours, matching the allowed impact scope. [6](#0-5) 

## Likelihood Explanation
The attack entry points (`pool.deposit()`, `wrapper.withdraw()`, `wrapper.deposit()`) are all public and require no special role. The attacker needs altRsETH (a bridged L2 asset, obtainable on secondary markets or by bridging rsETH from L1) and ETH to cover fees. The net capital loss per cycle is only `feeBps` of the deposited amount plus gas. The attack is repeatable every 24 hours and requires no victim interaction. Likelihood is **Medium**.

## Recommendation
Decrement `dailyMintAmount` when wrsETH is burned. Two approaches:

1. **Pool-side burn function**: Add a `burnAndRefund` function in the pool that decrements `dailyMintAmount` before calling `wrapper.withdraw()`, and restrict the wrapper's `withdraw()` to only be callable through the pool.
2. **Wrapper callback**: Register a pool address in the wrapper and call a `onBurn(uint256 amount)` hook in `_withdraw()` so the pool can decrement its counter. This requires the wrapper to track which pool minted the tokens.

The cleanest fix is to consolidate mint/burn accounting into a single contract that owns both the supply counter and the burn path.

## Proof of Concept
```solidity
// Foundry fork test on an L2 with RSETHPoolV3ExternalBridge + RsETHTokenWrapper

function testExhaustDailyLimit() public {
    uint256 limit = pool.dailyMintLimit();

    // Seed the wrapper with altRsETH so withdraw() has balance to return
    uint256 seed = limit; // 1:1 altRsETH to wrsETH
    altRsETH.approve(address(wrapper), seed);
    wrapper.deposit(address(altRsETH), seed); // wrapper has `seed` altRsETH

    // Cycle: deposit ETH into pool, withdraw altRsETH from wrapper, re-seed wrapper
    while (pool.dailyMintAmount() < limit) {
        uint256 ethChunk = /* ETH equivalent of one chunk of rsETH */;
        pool.deposit{value: ethChunk}("ref");          // dailyMintAmount += chunk
        uint256 bal = wrsETH.balanceOf(address(this)) - seed; // pool-minted wrsETH
        wrsETH.approve(address(wrapper), bal);
        wrapper.withdraw(address(altRsETH), bal);      // dailyMintAmount unchanged
        altRsETH.approve(address(wrapper), bal);
        wrapper.deposit(address(altRsETH), bal);       // restore wrapper balance
    }

    // Victim deposit reverts
    vm.prank(victim);
    vm.expectRevert(RSETHPoolV3ExternalBridge.DailyMintLimitExceeded.selector);
    pool.deposit{value: 1 ether}("ref");
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L152-158)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L381-383)
```text
        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3.sol (L118-125)
```text
        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
