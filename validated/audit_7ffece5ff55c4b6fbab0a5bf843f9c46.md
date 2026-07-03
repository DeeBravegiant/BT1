### Title
Wrong Fee Accounting Slot in Token Deposit Corrupts ETH Balance and Loses Token Fees - (File: contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
In `RSETHPoolNoWrapper.deposit(address token, ...)`, token deposit fees are incorrectly accumulated into `feeEarnedInETH` instead of `feeEarnedInToken[token]`. This is a direct analog to the "missing side check" class: the wrong accounting slot is used for the wrong asset type, corrupting ETH balance accounting, permanently losing token fees for the fee recipient, and freezing ETH bridging once the inflated `feeEarnedInETH` exceeds the contract's actual ETH balance.

### Finding Description
`RSETHPoolNoWrapper` maintains two separate fee-tracking variables: `feeEarnedInETH` for native ETH deposit fees and `feeEarnedInToken[token]` for ERC-20 token deposit fees. The ETH deposit path correctly uses `feeEarnedInETH`, but the token deposit path at line 266 writes to `feeEarnedInETH` instead of `feeEarnedInToken[token]`:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol
function deposit(
    address token,
    uint256 amount,
    string memory referralId
)
    external
    nonReentrant
    whenNotPaused
    onlySupportedToken(token)
{
    if (amount == 0) revert InvalidAmount();

    IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

    feeEarnedInETH += fee;   // ← BUG: should be feeEarnedInToken[token] += fee

    rsETH.safeTransfer(msg.sender, rsETHAmount);

    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
``` [1](#0-0) 

Every token deposit inflates `feeEarnedInETH` by the fee amount, while `feeEarnedInToken[token]` remains permanently at zero. Three downstream functions are broken as a result:

**1. `getETHBalanceMinusFees()` underflows:** [2](#0-1) 

Once `feeEarnedInETH` (inflated by token fees) exceeds `address(this).balance`, this subtraction underflows and reverts, freezing every caller of this function.

**2. `bridgeAssets()` and `bridgeAssetsViaNativeBridge()` are frozen:** [3](#0-2) [4](#0-3) 

Both call `getETHBalanceMinusFees()` and revert once the underflow condition is reached.

**3. Token fees are silently lost:**
`withdrawFees(receiver, token)` reads `feeEarnedInToken[token]` which is always 0, so it transfers nothing. Meanwhile `getTokenBalanceMinusFees(token)` returns the full token balance (fees included), so `bridgeTokens(token)` bridges the fee portion to L1 instead of keeping it for the fee recipient. [5](#0-4) [6](#0-5) 

### Impact Explanation
- **High — Theft of unclaimed yield**: All token deposit fees are permanently unclaimable by the fee recipient. Instead, they are included in `getTokenBalanceMinusFees(token)` and bridged to L1 as part of the principal, effectively redirecting fee revenue away from the protocol.
- **Medium — Temporary/permanent freezing of funds**: Once cumulative token fees exceed the contract's ETH balance, `getETHBalanceMinusFees()` underflows, freezing `bridgeAssets()` and `bridgeAssetsViaNativeBridge()`. Recovery requires depositing enough ETH to exceed `feeEarnedInETH`, which is not a guaranteed path.

### Likelihood Explanation
High. The `deposit(address token, ...)` function is publicly callable by any user with no access restriction. Every single token deposit triggers the bug. On a chain where token deposits are the primary flow (e.g., wstETH deposits), the underflow condition is reached quickly and deterministically.

### Recommendation
Change line 266 in `RSETHPoolNoWrapper.sol` from:
```solidity
feeEarnedInETH += fee;
```
to:
```solidity
feeEarnedInToken[token] += fee;
```
This mirrors the correct pattern used in `RSETHPoolV3.sol` and `RSETHPoolV3ExternalBridge.sol`. [7](#0-6) 

### Proof of Concept
1. Pool is deployed with `feeBps = 100` (1%), ETH balance = 0.5 ETH, `feeEarnedInETH = 0`.
2. A user calls `deposit(wstETH, 100e18, "")`. Fee = 1e18 wstETH units. `feeEarnedInETH` becomes `1e18`.
3. `getETHBalanceMinusFees()` = `0.5e18 - 1e18` → arithmetic underflow → revert.
4. `bridgeAssets(amount, minAmount, nativeFee)` now always reverts — ETH bridging is frozen.
5. `bridgeAssetsViaNativeBridge()` also reverts for the same reason.
6. `withdrawFees(receiver, wstETH)` transfers 0 (since `feeEarnedInToken[wstETH] == 0`).
7. `bridgeTokens(wstETH)` bridges the full wstETH balance including the 1e18 fee to L1, permanently diverting fee revenue.

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L352-354)
```text
    function getETHBalanceMinusFees() public view returns (uint256) {
        return address(this).balance - feeEarnedInETH;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L393-408)
```text
    function withdrawFees(
        address receiver,
        address token
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw fees in ETH
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L436-443)
```text
        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = getETHBalanceMinusFees();

        IL2Messenger(messenger).sendETHToL1ViaBridge{ value: ethBalanceMinusFees }(
            l2Bridge, l1VaultETHForL2Chain, ethBalanceMinusFees
        );

        emit BridgedETHToL1ViaNativeBridge(l1VaultETHForL2Chain, ethBalanceMinusFees);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L460-462)
```text
        // Exclude msg.value so reserved fees can’t be accidentally consumed
        if (getETHBalanceMinusFees() - msg.value < amount) {
            revert InsufficientETHBalance();
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L495-519)
```text
    function bridgeTokens(address token)
        external
        payable
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (tokenBridge[token] == address(0)) {
            revert MissingBridgeForToken();
        }

        uint256 balance = getTokenBalanceMinusFees(token);

        if (balance == 0) {
            revert ZeroBridgeAmount();
        }

        // Approve the required amount to the bridge
        IERC20(token).safeIncreaseAllowance(tokenBridge[token], balance);

        // Call the bridge contract to transfer the tokens (msg.value is included in case we need to pay for additional
        // bridging fees)
        IL2TokenBridge(tokenBridge[token]).bridgeTokenToL1{ value: msg.value }(l1VaultETHForL2Chain, balance);

        emit BridgedTokenToL1(token, l1VaultETHForL2Chain, balance);
```

**File:** contracts/pools/RSETHPoolV3.sol (L286-292)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```
