### Title
`depositAsset()` Mints rsETH Based on Input Amount, Not Actual Received Amount, Enabling Over-Minting with Fee-on-Transfer Tokens - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset()` computes `rsethAmountToMint` from the caller-supplied `depositAmount` before performing the `safeTransferFrom`, then mints rsETH unconditionally using that pre-computed value. If a fee-on-transfer token is ever added as a supported asset, the contract receives fewer tokens than `depositAmount` but mints the full rsETH amount, directly over-collateralizing rsETH and making the protocol insolvent.

### Finding Description
In `depositAsset()`, the mint amount is fixed before the transfer occurs:

```solidity
// Line 111 — rsethAmountToMint locked to depositAmount (caller input)
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// Line 114 — actual transfer; may deliver less than depositAmount for fee-on-transfer tokens
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);

// Line 115 — mints based on depositAmount, not actual received amount
_mintRsETH(rsethAmountToMint);
``` [1](#0-0) 

`_beforeDeposit` calls `getRsETHAmountToMint(asset, depositAmount)`, which computes the mint amount purely from the caller-supplied `depositAmount`:

```solidity
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
``` [2](#0-1) 

`getRsETHAmountToMint` uses `amount` (= `depositAmount`) directly without any balance verification:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

There is no balance-before / balance-after check anywhere in this path. The same pattern exists in `RsETHTokenWrapper._deposit()`, which mints `_amount` of wrsETH after `safeTransferFrom` without verifying actual receipt: [4](#0-3) 

### Impact Explanation
If a fee-on-transfer token is added as a supported LST asset, every depositor receives more rsETH than the collateral actually deposited. Because rsETH's price is backed by `getTotalAssetDeposits` (which reads real on-chain balances), the rsETH price will be lower than expected, diluting all existing rsETH holders. At withdrawal time, the protocol cannot redeem all outstanding rsETH at par — **protocol insolvency**. This is a Critical impact under the allowed scope. [5](#0-4) 

### Likelihood Explanation
Likelihood is **Low**. The current supported assets (stETH, wstETH, etc.) are not fee-on-transfer tokens. However, the protocol is designed to support new LST assets added by governance, and there is no on-chain guard preventing a fee-on-transfer token from being added. Once such a token is added, any unprivileged depositor can immediately exploit the gap.

### Recommendation
Record the contract's token balance before and after `safeTransferFrom` and use the delta as the actual received amount for both the rsETH mint calculation and the deposit limit check:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to `RsETHTokenWrapper._deposit()`.

### Proof of Concept
1. Governance adds a fee-on-transfer LST (e.g., 1% fee) as a supported asset.
2. Attacker calls `depositAsset(feeToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` for `1000e18` tokens.
4. `safeTransferFrom` delivers only `990e18` tokens to the contract (1% fee deducted).
5. `_mintRsETH` mints rsETH equivalent to `1000e18` tokens.
6. Attacker holds rsETH backed by `1000e18` worth of collateral but only `990e18` was deposited — 10 ETH worth of rsETH is unbacked.
7. Repeated deposits progressively drain the collateral pool, making the protocol insolvent for all rsETH holders. [6](#0-5)

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

**File:** contracts/LRTDepositPool.sol (L385-397)
```text
    function getTotalAssetDeposits(address asset) public view override returns (uint256 totalAssetDeposit) {
        (
            uint256 assetLyingInDepositPool,
            uint256 assetLyingInNDCs,
            uint256 assetStakedInEigenLayer,
            uint256 assetUnstakingFromEigenLayer,
            uint256 assetLyingInConverter,
            uint256 assetLyingUnstakingVault
        ) = getAssetDistributionData(asset);
        uint256 effectiveAssetWithEigenLayer = assetStakedInEigenLayer + assetUnstakingFromEigenLayer;
        return (assetLyingInDepositPool + assetLyingInNDCs + effectiveAssetWithEigenLayer + assetLyingInConverter
                + assetLyingUnstakingVault);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-665)
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
