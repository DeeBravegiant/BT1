### Title
Fee-on-Transfer Token Not Accounted in `depositAsset` Causes rsETH Over-Minting — (`contracts/LRTDepositPool.sol`)

### Summary
`LRTDepositPool.depositAsset` calculates the rsETH amount to mint using the caller-supplied `depositAmount`, then performs `safeTransferFrom`. If the deposited LST has a transfer fee, the contract receives fewer tokens than `depositAmount`, but mints rsETH as if the full `depositAmount` arrived. This inflates rsETH supply relative to backing assets, causing protocol insolvency for all existing rsETH holders.

### Finding Description
In `LRTDepositPool.depositAsset`:

```solidity
// Line 111: rsETH calculated on user-supplied depositAmount
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// Line 114: actual transfer — contract may receive < depositAmount if token has fee
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);

// Line 115: rsETH minted based on depositAmount, not actual received amount
_mintRsETH(rsethAmountToMint);
``` [1](#0-0) 

`_beforeDeposit` delegates to `getRsETHAmountToMint`, which computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

The `amount` passed is the caller-controlled `depositAmount`, not the balance delta actually received. No before/after balance check is performed. If the LST token charges a transfer fee `f`, the contract receives `depositAmount * (1 - f)` but mints rsETH worth `depositAmount`, creating unbacked rsETH equal to `depositAmount * f * assetPrice / rsETHPrice` per deposit.

Contrast this with `KernelDepositPool.notifyRewardAmount`, which correctly uses a before/after balance check to handle fee-on-transfer tokens:

```solidity
uint256 balanceBefore = rewardsToken.balanceOf(address(this));
rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
uint256 balanceAfter = rewardsToken.balanceOf(address(this));
uint256 receivedAmount = balanceAfter - balanceBefore;
``` [3](#0-2) 

The same missing check also exists in `RsETHTokenWrapper._deposit` and `AGETHTokenWrapper._deposit`, which mint wrsETH/agETH 1:1 against `_amount` without verifying actual receipt: [4](#0-3) [5](#0-4) 

### Impact Explanation
**Critical — Protocol insolvency.** Each deposit with a fee-on-transfer LST mints more rsETH than the asset value backing it. Over time, `getTotalAssetDeposits` (which reads actual on-chain balances) will be lower than what the rsETH supply implies, making rsETH permanently under-collateralized. Late redeemers cannot be made whole. [6](#0-5) 

### Likelihood Explanation
**Low-to-Medium.** The currently supported LSTs (stETH, cbETH, rETH, etc.) do not have active transfer fees. However, the protocol's governance can add new supported assets at any time via `LRTConfig`, and some LST designs reserve the right to enable fees. If any supported asset activates a fee, every subsequent `depositAsset` call silently over-mints rsETH. No attacker action beyond a normal deposit is required once such a token is live.

### Recommendation
Replace the fixed-`depositAmount` mint calculation with an actual balance-delta approach, mirroring the pattern already used in `KernelDepositPool.notifyRewardAmount`:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same fix to `RsETHTokenWrapper._deposit` and `AGETHTokenWrapper._deposit`.

### Proof of Concept
1. Governance adds a supported LST `FEE_LST` that charges a 1% transfer fee.
2. Attacker calls `LRTDepositPool.depositAsset(FEE_LST, 100e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `100e18`.
4. `safeTransferFrom` transfers `100e18` from attacker; contract receives `99e18` (1% fee taken).
5. `_mintRsETH` mints rsETH worth `100e18` of `FEE_LST`.
6. `getTotalAssetDeposits(FEE_LST)` now reflects only `99e18` in the pool, but rsETH supply has grown as if `100e18` arrived.
7. Repeating this drains the backing ratio. Existing rsETH holders redeem at a loss; the protocol becomes insolvent. [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L385-396)
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
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L573-577)
```text
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;
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

**File:** contracts/agETH/AGETHTokenWrapper.sol (L125-132)
```text
    function _deposit(address _asset, address _to, uint256 _amount) internal {
        if (!allowedTokens[_asset]) revert TokenNotAllowed();

        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
        emit Deposit(_asset, _to, _amount);
    }
```
