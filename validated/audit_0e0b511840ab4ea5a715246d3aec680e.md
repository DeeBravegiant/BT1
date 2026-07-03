### Title
Fee-on-Transfer Token Deposit Causes wrsETH Over-Minting and Protocol Insolvency - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
`RSETHPoolV3.deposit(address token, uint256 amount, ...)` calculates the wrsETH amount to mint from the caller-supplied `amount` parameter before the `safeTransferFrom` call. If a fee-on-transfer token is ever added as a supported deposit asset, the contract receives fewer tokens than `amount` but mints wrsETH as if the full `amount` arrived, permanently inflating the wrsETH supply beyond its actual backing.

### Finding Description
In `RSETHPoolV3.sol`, the token deposit path is:

```solidity
// line 284
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

// line 286 — rsETHAmount computed from `amount`, not from actual received balance
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

feeEarnedInToken[token] += fee;

// line 290 — new wrsETH minted based on the pre-transfer `amount`
wrsETH.mint(msg.sender, rsETHAmount);
``` [1](#0-0) 

`viewSwapRsETHAmountAndFee` derives `rsETHAmount` purely from the caller-supplied `amount`:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [2](#0-1) 

No balance snapshot is taken before and after the transfer to determine the actual tokens received. For a fee-on-transfer token the contract receives `amount − transferFee`, but mints wrsETH as if it received the full `amount`.

The same pattern exists in `LRTDepositPool.depositAsset`, where `rsethAmountToMint` is computed from `depositAmount` before the `safeTransferFrom`: [3](#0-2) 

New tokens can be added by the `TIMELOCK_ROLE` via `addSupportedToken`: [4](#0-3) 

### Impact Explanation
Every deposit of a fee-on-transfer token mints more wrsETH than the pool's actual token balance can back. When the bridger calls `moveAssetsForBridging` or `bridgeTokens`, it can only forward the real (reduced) token balance to L1. The L1 vault therefore receives fewer assets than the outstanding wrsETH supply represents, making the protocol insolvent: existing wrsETH holders cannot fully redeem their positions. This is **protocol insolvency (Critical)**.

### Likelihood Explanation
Current supported tokens are LSTs (wstETH, etc.) which do not implement fee-on-transfer. However, the `addSupportedToken` function imposes no restriction against fee-on-transfer tokens — it only checks for a non-zero oracle rate. If the protocol expands to support additional LSTs or yield-bearing tokens that carry a transfer fee, the vulnerability becomes immediately exploitable by any depositor. Likelihood is **Low** given current token set, but the attack surface is permanently open.

### Recommendation
Record the contract's token balance before and after `safeTransferFrom` and use the difference as the actual deposited amount for all downstream calculations:

```solidity
uint256 balanceBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(token).balanceOf(address(this)) - balanceBefore;

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(actualReceived, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

Apply the same fix to `LRTDepositPool.depositAsset`. Alternatively, explicitly disallow fee-on-transfer tokens in `addSupportedToken` by verifying a balance-delta check at registration time.

### Proof of Concept
1. Admin calls `addSupportedToken(feeToken, oracle)` where `feeToken` charges a 1% transfer fee.
2. Attacker calls `deposit(feeToken, 1000e18, "")`.
3. Contract receives `990e18` tokens (1% fee deducted by the token).
4. `viewSwapRsETHAmountAndFee(1000e18, feeToken)` returns `rsETHAmount` based on `1000e18`.
5. `wrsETH.mint(attacker, rsETHAmount)` — wrsETH minted for `1000e18` worth, but pool only holds `990e18`.
6. Repeat: each deposit widens the gap between wrsETH supply and actual token backing.
7. When bridger calls `moveAssetsForBridging(feeToken, amount)`, only `990e18 * N` tokens are available for `1000e18 * N` worth of wrsETH — L1 vault is underfunded, protocol is insolvent.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L284-290)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L323-334)
```text
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-554)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
```

**File:** contracts/LRTDepositPool.sol (L110-115)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```
