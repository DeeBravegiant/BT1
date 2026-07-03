### Title
Deflationary Token Deposit Mints Excess rsETH Relative to Actual Received Collateral — (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset` computes the rsETH mint amount from the caller-supplied `depositAmount` parameter and then executes `safeTransferFrom`. If the deposited asset is a deflationary (transfer-fee) token, the contract receives fewer tokens than `depositAmount`, yet mints rsETH as if the full `depositAmount` arrived. The same pattern is repeated in every pool-side `deposit(token, amount, …)` function across `RSETHPool`, `RSETHPoolV3`, and `RSETHPoolV3WithNativeChainBridge`.

---

### Finding Description

**Root cause — `LRTDepositPool.depositAsset`** [1](#0-0) 

```
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected); // (1)
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);                 // (2)
_mintRsETH(rsethAmountToMint);                                                             // (3)
```

Step (1) calls `getRsETHAmountToMint(asset, depositAmount)`: [2](#0-1) 

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`amount` here is the caller-supplied `depositAmount`, not the balance delta after the transfer. For a deflationary token with a `k%` transfer fee, the contract receives `depositAmount * (1 - k/100)` tokens but mints rsETH as if it received `depositAmount` tokens — an over-mint of `k%`.

**Same pattern in pool contracts**

`RSETHPool.deposit(token, amount, …)`: [3](#0-2) 

`RSETHPoolV3.deposit(token, amount, …)`: [4](#0-3) 

`RSETHPoolV3WithNativeChainBridge.deposit(token, amount, …)`: [5](#0-4) 

In every case `viewSwapRsETHAmountAndFee(amount, token)` is called with the pre-transfer `amount`, and wrsETH is transferred or minted based on that inflated figure.

---

### Impact Explanation

Every deposit with a deflationary token mints more rsETH (or wrsETH) than the actual collateral received justifies. Repeated deposits progressively widen the gap between total rsETH supply and total backing assets, eventually making the protocol insolvent: honest withdrawers cannot redeem their rsETH at par because the collateral pool is insufficient. This is **protocol insolvency** — a Critical impact.

---

### Likelihood Explanation

The currently whitelisted LST assets (stETH, rETH, cbETH, etc.) do not carry transfer fees. However:

- The `onlySupportedERC20Token` / `onlySupportedToken` guards only check whether the token is on the whitelist; they impose no restriction on transfer-fee behaviour.
- Any future whitelisted token that carries a transfer fee (e.g., a rebasing LST that introduces a fee, or a wrapped token with a built-in fee) immediately activates the vulnerability.
- No code-level guard prevents a deflationary token from being added.

Likelihood is **Low** given current token set, but the code is structurally unprotected.

---

### Recommendation

Measure the actual balance delta after the transfer and use that for all downstream calculations:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same pattern in all pool `deposit(token, amount, …)` functions. Alternatively, explicitly restrict supported tokens to those with no transfer fee.

---

### Proof of Concept

1. Admin whitelists a token `FEE_TOKEN` that deducts a 5% fee on every transfer.
2. Attacker calls `LRTDepositPool.depositAsset(FEE_TOKEN, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` for `1000e18` tokens.
4. `safeTransferFrom` executes; contract receives only `950e18` tokens (5% fee burned).
5. `_mintRsETH(rsethAmountToMint)` mints rsETH equivalent to `1000e18` tokens.
6. Attacker holds rsETH backed by only `950e18` tokens — a 5.26% over-issuance per deposit.
7. Repeated deposits drain the collateral pool relative to outstanding rsETH, causing insolvency for all holders. [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/pools/RSETHPool.sol (L296-302)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-290)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L320-326)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```
