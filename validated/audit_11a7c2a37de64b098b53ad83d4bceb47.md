### Title
L2 Pool Deposit Fee Bypassed via `RsETHTokenWrapper.deposit()` - (File: contracts/L2/RsETHTokenWrapper.sol)

### Summary
The L2 pool contracts charge a basis-point fee on every `deposit()` call before minting wrsETH. However, `RsETHTokenWrapper.deposit()` is a public, fee-free function that mints wrsETH 1:1 for any `allowedTokens` altRsETH. Any user who holds a bridged altRsETH token (rsETH bridged from L1 via a supported bridge) can call this function directly and receive wrsETH without paying the protocol fee, permanently depriving the protocol of fee revenue.

### Finding Description
Every L2 pool variant (`RSETHPoolV3`, `RSETHPoolV2`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) charges a fee when a user deposits ETH or LST:

```solidity
// RSETHPoolV3.sol L258-262
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

The fee is computed as `fee = amount * feeBps / 10_000`, so the user receives fewer wrsETH than the full ETH-equivalent value deposited.

`RsETHTokenWrapper` is the wrsETH token itself. It exposes a public, permissionless `deposit()` function:

```solidity
// RsETHTokenWrapper.sol L69-71
function deposit(address asset, uint256 _amount) external {
    _deposit(asset, msg.sender, _amount);
}
```

The internal `_deposit` only checks that `asset` is in `allowedTokens`, then mints wrsETH 1:1 with zero fee:

```solidity
// RsETHTokenWrapper.sol L134-141
function _deposit(address _asset, address _to, uint256 _amount) internal {
    if (!allowedTokens[_asset]) revert TokenNotAllowed();
    ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);
    _mint(_to, _amount);
    emit Deposit(_asset, msg.sender, _to, _amount);
}
```

`allowedTokens` contains bridged rsETH tokens (altRsETH) that are bridged from L1 to L2 via supported bridges. A user who holds such a token can call `RsETHTokenWrapper.deposit()` and receive wrsETH at a 1:1 rate with no fee deducted, completely bypassing the pool fee.

### Impact Explanation
**High — Theft of unclaimed yield (protocol fee revenue).**

The `feeBps` fee is the protocol's revenue mechanism on L2 deposits. Every user who routes through `RsETHTokenWrapper.deposit()` instead of the L2 pool's `deposit()` pays zero fee. The protocol permanently loses `feeBps / 10_000 * depositAmount` in fee revenue per bypassed deposit. For large depositors, the fee savings easily exceed bridge costs, making this a rational and repeatable strategy.

### Likelihood Explanation
**Medium-High.** The bypass path is always open — `RsETHTokenWrapper.deposit()` is public and requires no special role. Any user who can obtain altRsETH on L2 (by bridging rsETH from L1 via a supported bridge) can execute this. Sophisticated or large depositors are directly incentivized to use this path. The L1 deposit path (`LRTDepositPool.depositETH()`) itself charges no fee, so the only cost is the bridge gas/fee, which is amortized over large deposits.

### Recommendation
Apply the same fee logic inside `RsETHTokenWrapper._deposit()` as is applied in the L2 pool `deposit()` functions, or restrict `RsETHTokenWrapper.deposit()` to only be callable by the pool contracts (i.e., require `MINTER_ROLE` or a dedicated role). Alternatively, document that the wrapper's `deposit()` is intentionally fee-free and accept the bypass as a known design trade-off (analogous to the UNCX acknowledgment in the external report), but this should be an explicit decision.

### Proof of Concept

1. User calls `LRTDepositPool.depositETH{value: 10 ether}(0, "")` on L1 — receives `X` rsETH, no fee charged.
2. User bridges `X` rsETH from L1 to L2 via a bridge whose output token is in `RsETHTokenWrapper.allowedTokens` — receives `X` altRsETH on L2.
3. User calls `RsETHTokenWrapper.deposit(altRsETH, X)` — receives `X` wrsETH, **no fee deducted**.

Compare with the fee-paying path:
- User calls `RSETHPoolV3.deposit{value: 10 ether}("")` on L2 — receives `X - fee` wrsETH, where `fee = 10 ether * feeBps / 10_000`.

The attacker ends up with more wrsETH per ETH spent than a user going through the intended L2 pool path. The protocol's `feeEarnedInETH` is never incremented, so the fee is permanently lost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/L2/RsETHTokenWrapper.sol (L69-71)
```text
    function deposit(address asset, uint256 _amount) external {
        _deposit(asset, msg.sender, _amount);
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

**File:** contracts/pools/RSETHPoolV3.sol (L258-262)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L286-290)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```
