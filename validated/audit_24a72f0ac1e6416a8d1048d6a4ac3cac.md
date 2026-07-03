### Title
Fee-on-Transfer Token Deposits Mint Excess rsETH Against Actual Received Balance - (File: contracts/LRTDepositPool.sol)

---

### Summary

`LRTDepositPool.depositAsset` calculates the rsETH mint amount from the caller-supplied `depositAmount` parameter before executing the `safeTransferFrom`. If a supported asset implements a transfer fee, the contract receives fewer tokens than `depositAmount`, yet mints rsETH as if the full `depositAmount` arrived. This inflates rsETH supply relative to actual protocol backing, diluting every existing rsETH holder.

---

### Finding Description

In `depositAsset`, the execution order is:

1. `_beforeDeposit(asset, depositAmount, minRSETHAmountExpected)` — computes `rsethAmountToMint` via `getRsETHAmountToMint(asset, depositAmount)`, which evaluates `(depositAmount × assetPrice) / rsETHPrice`. The full caller-supplied `depositAmount` is used.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` — for a fee-on-transfer token, the contract actually receives `depositAmount − fee`.
3. `_mintRsETH(rsethAmountToMint)` — mints the amount computed in step 1, which is larger than what the actual received balance warrants. [1](#0-0) 

The rsETH price oracle path (`getTotalAssetDeposits` → `getAssetDistributionData`) reads real on-chain `balanceOf` values: [2](#0-1) 

So after the deposit, the rsETH total supply has grown by `rsethAmountToMint` (based on `depositAmount`) while the actual asset backing has only grown by `depositAmount − fee`. The rsETH price, computed as `totalETHBacking / rsETH.totalSupply()`, is now lower than it should be.

The mint calculation that uses the raw input amount without checking actual received balance: [3](#0-2) [4](#0-3) 

The same pattern is present in `RSETHPoolV3.deposit` and `RSETHPoolNoWrapper.deposit`, where `viewSwapRsETHAmountAndFee(amount, token)` is called with the raw input `amount` after `safeTransferFrom`, and wrsETH/rsETH is dispensed based on that unchecked figure: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

Every rsETH holder suffers dilution proportional to the fee taken on each deposit. The rsETH price (`rsETHPrice = totalETHBacking / rsETH.totalSupply`) decreases because the supply numerator grows faster than the backing denominator. Repeated deposits with a fee-on-transfer token drain value from all existing holders. At scale this constitutes **theft of unclaimed yield** (High) and can progress toward **protocol insolvency** (Critical) if the fee-bearing asset constitutes a significant share of TVL.

---

### Likelihood Explanation

The current supported LST set (stETH, cbETH, rETH, etc.) does not implement transfer fees. However, the protocol is explicitly designed to add new supported assets via governance, and no on-chain guard prevents a fee-on-transfer token from being whitelisted. Once such a token is added, any unprivileged depositor can exploit this path repeatedly with zero additional preconditions. Likelihood is **Medium** (requires governance to add a fee-bearing token, but no attacker capability is needed beyond a standard deposit call once that happens).

---

### Recommendation

Measure the actual balance change around the transfer and use that for minting, rather than the caller-supplied parameter:

```solidity
function depositAsset(
    address asset,
    uint256 depositAmount,
    uint256 minRSETHAmountExpected,
    string calldata referralId
) external nonReentrant whenNotPaused onlySupportedERC20Token(asset) {
    uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
    uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

    uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);

    emit AssetDeposit(msg.sender, asset, actualReceived, rsethAmountToMint, referralId);
}
```

Apply the same pattern to `RSETHPoolV3.deposit` and `RSETHPoolNoWrapper.deposit`. Alternatively, explicitly document and enforce (via an on-chain check or allowlist invariant) that no supported asset may implement a transfer fee.

---

### Proof of Concept

Assume a supported LST `FeeToken` charges a 1% transfer fee.

1. Protocol state: `totalETHBacking = 1000 ETH`, `rsETH.totalSupply = 1000`, `rsETHPrice = 1.0 ETH/rsETH`, `FeeToken price = 1 ETH`.
2. Attacker calls `depositAsset(FeeToken, 100e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint = (100e18 × 1e18) / 1e18 = 100e18`.
4. `safeTransferFrom` moves 100 FeeToken; contract receives **99 FeeToken** (1% fee burned/redirected).
5. `_mintRsETH(100e18)` mints 100 rsETH to attacker.
6. New state: `totalETHBacking = 1099 ETH` (99 FeeToken × 1 ETH), `rsETH.totalSupply = 1100`.
7. New `rsETHPrice = 1099 / 1100 ≈ 0.999 ETH/rsETH`.
8. Every existing holder's rsETH is now worth ~0.1% less. The attacker received 100 rsETH worth 99.9 ETH for a 99 ETH deposit — a net gain of ~0.9 ETH extracted from existing holders per iteration. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L110-117)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L506-520)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-292)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L262-270)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```
