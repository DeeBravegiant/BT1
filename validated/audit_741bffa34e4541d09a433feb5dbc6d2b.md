Audit Report

## Title
Unbacked wrsETH from Pool Minting Drains Wrapper's altRsETH Reserves, Causing Protocol Insolvency - (File: contracts/L2/RsETHTokenWrapper.sol)

## Summary

`RSETHPoolV2` and `RSETHPoolV3` call `wrsETH.mint(msg.sender, rsETHAmount)` directly when users deposit ETH or ERC-20 tokens, minting wrsETH without depositing any altRsETH collateral into `RsETHTokenWrapper`. Because `_withdraw()` imposes no solvency check and wrsETH is fungible, any pool depositor can immediately redeem their pool-minted wrsETH for altRsETH that was deposited by other users via `wrapper.deposit()`, leaving those users permanently unable to redeem their wrsETH.

## Finding Description

**Minting path — no collateral deposited into wrapper:**

`RSETHPoolV2.deposit()` mints wrsETH directly to the caller while ETH stays in the pool contract: [1](#0-0) 

`RSETHPoolV3.deposit()` does the same for both ETH and ERC-20 deposits: [2](#0-1) 

`RsETHTokenWrapper.mint()` has no collateral requirement — it simply calls `_mint`: [3](#0-2) 

**Withdrawal path — no solvency check:**

`_withdraw()` only verifies the asset is allowed, burns the caller's wrsETH, and transfers altRsETH out. There is no check that `balanceOf(altRsETH) >= totalSupply()` after the transfer: [4](#0-3) 

**Broken invariant:**

`maxAmountToDepositBridgerAsset()` reveals the design intent — the wrapper is supposed to remain fully collateralized (`balance >= totalSupply`). The bridger is expected to deposit altRsETH after pool mints to restore this invariant. However, there is no mechanism that delays or blocks `withdraw()` until the bridger has deposited: [5](#0-4) 

**Exploit flow:**

| Step | Action | Wrapper altRsETH balance | wrsETH totalSupply |
|------|--------|--------------------------|-------------------|
| 0 | User B calls `wrapper.deposit(altRsETH, 1000)` | 1000 | 1000 |
| 1 | User A calls `pool.deposit{value: X}()` → pool calls `wrsETH.mint(A, 500)` | 1000 | 1500 |
| 2 | User A calls `wrapper.withdraw(altRsETH, 500)` | 500 | 1000 |
| 3 | User B calls `wrapper.withdraw(altRsETH, 1000)` | **REVERTS** | — |

Because wrsETH is a fungible ERC-20, there is no on-chain mechanism to distinguish pool-minted wrsETH from wrapper-deposited wrsETH. User A's pool-minted wrsETH is indistinguishable from User B's wrapper-deposited wrsETH, so User A can freely redeem for User B's altRsETH. The bridger's subsequent `depositBridgerAssets()` call cannot restore User B's funds because User A already drained them.

## Impact Explanation

**Critical — Direct theft of user funds and protocol insolvency.**

User B deposits altRsETH into the wrapper and receives wrsETH. After User A executes the attack, User B holds wrsETH that can never be redeemed for altRsETH because the wrapper's balance has been drained. This is a permanent, irreversible loss of User B's deposited altRsETH — a direct theft of funds at rest. The wrapper enters an insolvent state where `totalSupply() > balanceOf(altRsETH)`, matching the "Protocol insolvency" and "Direct theft of any user funds" impact classes.

## Likelihood Explanation

**High.** The attack requires no special role, no governance capture, and no oracle manipulation. Any user who receives wrsETH via a standard pool deposit (a publicly accessible function) can immediately call `wrapper.withdraw()`. The attack is profitable whenever the wrapper holds any altRsETH balance from regular `deposit()` calls, which is the normal operating state of the system. The attack is atomic and repeatable.

## Recommendation

Enforce a collateralization invariant in `_withdraw()`. Before transferring altRsETH out, verify that the wrapper's remaining balance is sufficient to back all remaining wrsETH supply:

```solidity
function _withdraw(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    _burn(msg.sender, _amount);
    uint256 remainingBalance = ERC20Upgradeable(_asset).balanceOf(address(this)) - _amount;
    if (remainingBalance < totalSupply()) revert InsufficientCollateral();
    ERC20Upgradeable(_asset).safeTransfer(_to, _amount);
    emit Withdraw(_asset, msg.sender, _to, _amount);
}
```

Alternatively, track pool-minted supply separately from wrapper-deposited supply, or require the bridger to fully collateralize the wrapper before any withdrawals are permitted.

## Proof of Concept

Minimal Foundry test sequence:

1. Deploy `RsETHTokenWrapper`, `RSETHPoolV2`, and a mock altRsETH ERC-20.
2. Grant `MINTER_ROLE` on the wrapper to the pool.
3. User B: `altRsETH.approve(wrapper, 1000e18)` then `wrapper.deposit(altRsETH, 1000e18)` — wrapper holds 1000e18 altRsETH, totalSupply = 1000e18.
4. User A: `pool.deposit{value: X}()` — pool calls `wrsETH.mint(userA, 500e18)` — wrapper still holds 1000e18 altRsETH, totalSupply = 1500e18.
5. User A: `wrapper.withdraw(altRsETH, 500e18)` — wrapper now holds 500e18 altRsETH, totalSupply = 1000e18.
6. User B: `wrapper.withdraw(altRsETH, 1000e18)` — **reverts** (ERC20 transfer exceeds balance).
7. Assert: `altRsETH.balanceOf(address(wrapper)) < wrapper.totalSupply()` — wrapper is insolvent; User B's 500e18 altRsETH has been permanently stolen.

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L99-110)
```text
    function maxAmountToDepositBridgerAsset(address _asset) public view returns (uint256) {
        if (!allowedTokens[_asset]) return 0;

        // get totalSupply of wrsETH minted
        uint256 wrsETHSupply = totalSupply();
        // balance of _asset with the contract
        uint256 balanceOfAssetInWrapper = ERC20Upgradeable(_asset).balanceOf(address(this));

        if (balanceOfAssetInWrapper > wrsETHSupply) return 0;

        return wrsETHSupply - balanceOfAssetInWrapper;
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
