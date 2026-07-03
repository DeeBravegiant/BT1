### Title
Stale `AGETHRateReceiver` Rate Causes Structural Undercollateralization in `AGETHTokenWrapper`, Permanently Freezing Last Redeemers' Funds — (`contracts/agETH/AGETHTokenWrapper.sol`, `contracts/agETH/AGETHPoolV3.sol`, `contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` with no staleness check. `AGETHPoolV3.deposit()` uses this rate to compute how many agETH wrapper tokens to mint. When the rate is stale (lower than the true L1 rate), the pool over-mints wrapper tokens relative to the ETH deposited. The bridger, operating off-chain, can only deposit as much altAgETH into `AGETHTokenWrapper` as the ETH actually buys on L1 at the current correct rate. The resulting gap is a permanent, irrecoverable undercollateralization: the last redeemers cannot withdraw because the contract holds fewer altAgETH tokens than outstanding wrapper tokens.

---

### Finding Description

**Step 1 — No staleness guard in `getRate()`**

`CrossChainRateReceiver` stores `lastUpdated` but never enforces a freshness window. `getRate()` blindly returns `rate`: [1](#0-0) 

**Step 2 — `AGETHPoolV3.viewSwapAgETHAmountAndFee()` divides by the stale rate**

```
agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

If `agETHToETHrate` is stale-low (agETH has accrued yield since the last LZ message), the division yields a larger `agETHAmount` than the correct rate would produce. [2](#0-1) 

**Step 3 — Pool mints the inflated amount directly to the user** [3](#0-2) 

**Step 4 — Bridger can only back the correct amount**

The bridger takes the ETH from the pool to L1, buys agETH at the current (correct, higher) rate, bridges it back as altAgETH, and calls `depositBridgerAssets()`. The altAgETH deposited equals `ETH * 1e18 / correctRate`, which is strictly less than the `agETHAmount_stale` already minted. [4](#0-3) 

**Step 5 — Withdrawal is 1:1; last redeemers are frozen**

`_withdraw()` burns wrapper tokens and transfers altAgETH 1:1. Once the altAgETH balance is exhausted, all subsequent `withdraw()` calls revert with an ERC20 transfer failure, permanently freezing the excess wrapper tokens. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

The undercollateralization is structural and irrecoverable without an admin intervention that is not provided by any existing contract function. Users who deposited during a stale-rate window hold wrapper tokens that can never be redeemed because the backing altAgETH does not exist in the contract. There is no mechanism to retroactively correct the minted supply.

---

### Likelihood Explanation

- LayerZero message delays and drops are a documented, real-world occurrence.
- `CrossChainRateReceiver` records `lastUpdated` but enforces no maximum staleness period anywhere in the production contracts.
- agETH's rate is monotonically non-decreasing (it accrues staking yield), so any stale rate is always lower than the current rate, always producing over-minting.
- The bridger's off-chain logic is rate-correct; the on-chain minting is rate-stale. The gap is deterministic.
- No admin pause, circuit breaker, or rate-deviation guard exists in `AGETHPoolV3`. [6](#0-5) 

---

### Recommendation

1. **Enforce a staleness threshold in `getRate()`**: revert (or return a sentinel) if `block.timestamp - lastUpdated > MAX_STALENESS`.
2. **Add a staleness check in `AGETHPoolV3.viewSwapAgETHAmountAndFee()`** before using the oracle rate.
3. **Add a minimum-rate guard**: revert if the returned rate deviates more than a configured percentage from the previous rate.
4. **Pause deposits** automatically when the rate has not been updated within the staleness window.

---

### Proof of Concept

```solidity
// Fork test (local fork, pinned block where LZ message is delayed)
function testStaleRateUndercollateralization() external {
    // 1. Pin a stale rate (e.g., 1.05e18) in AGETHRateReceiver
    //    while the true L1 rate is 1.10e18
    vm.mockCall(
        address(agETHOracle),
        abi.encodeWithSelector(IOracle.getRate.selector),
        abi.encode(1.05e18) // stale, lower than true 1.10e18
    );

    // 2. User deposits 1 ETH
    uint256 deposit = 1 ether;
    vm.deal(user, deposit);
    vm.prank(user);
    pool.deposit{value: deposit}("ref");

    // agETHAmount_stale = 1e18 * 1e18 / 1.05e18 ≈ 0.952e18
    // agETHAmount_correct = 1e18 * 1e18 / 1.10e18 ≈ 0.909e18
    // excess ≈ 0.043e18 wrapper tokens minted with no backing

    // 3. Bridger deposits only the correct amount of altAgETH
    uint256 correctAltAgETH = 1e18 * 1e18 / 1.10e18; // what L1 actually yields
    vm.prank(bridger);
    wrapper.depositBridgerAssets(altAgETH, correctAltAgETH);

    // 4. Assert undercollateralization
    uint256 supply = wrapper.totalSupply();
    uint256 backing = IERC20(altAgETH).balanceOf(address(wrapper));
    assertGt(supply, backing); // wrapper is undercollateralized

    // 5. Last redeemer is frozen
    vm.prank(user);
    vm.expectRevert(); // ERC20 transfer fails — insufficient altAgETH
    wrapper.withdraw(altAgETH, supply); // tries to redeem full supply
}
``` [2](#0-1) [1](#0-0) [5](#0-4)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-14)
```text
    uint256 public rate;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L121-127)
```text
        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L111-119)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, _to, _amount);
    }
```

**File:** contracts/agETH/AGETHTokenWrapper.sol (L143-151)
```text
    function depositBridgerAssets(address _asset, uint256 _amount) external onlyRole(BRIDGER_ROLE) {
        if (maxAmountToDepositBridgerAsset(_asset) < _amount) {
            revert CannotDeposit();
        }

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        emit BridgerDeposited(_asset, _amount);
    }
```
