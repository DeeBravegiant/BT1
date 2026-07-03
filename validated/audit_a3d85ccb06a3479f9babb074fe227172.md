### Title
stETH Rebasing Token Rounding Causes rsETH Over-Minting on Deposit - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset` calculates the rsETH amount to mint from the caller-supplied `depositAmount` **before** the actual `safeTransferFrom`. Because stETH is a share-based rebasing token, the amount actually credited to the contract can be `depositAmount - 1` wei due to internal share rounding. The protocol mints rsETH against the nominal `depositAmount` while holding one fewer wei of stETH, causing persistent, cumulative over-minting that dilutes existing rsETH holders.

### Finding Description
In `LRTDepositPool.depositAsset`, the execution order is:

1. `_beforeDeposit(asset, depositAmount, ...)` → computes `rsethAmountToMint = (depositAmount * assetPrice) / rsETHPrice` using the caller-supplied `depositAmount`.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` → for stETH, the contract may receive `depositAmount - 1` due to share-to-balance rounding.
3. `_mintRsETH(rsethAmountToMint)` → mints rsETH calculated from `depositAmount`, not from the actual received amount. [1](#0-0) 

The rsETH price is later computed in `LRTOracle._getTotalEthInProtocol` via `getTotalAssetDeposits`, which reads `IERC20(asset).balanceOf(address(this))` — the **actual** balance, not the nominal `depositAmount`. [2](#0-1) [3](#0-2) 

This creates a permanent wedge: rsETH supply grows by `rsethAmountToMint` (based on `depositAmount`) while the actual stETH backing grows by only `depositAmount - 1`. Every stETH deposit with a non-round share amount widens this gap.

stETH is an explicitly supported asset in the protocol, referenced via `ST_ETH_TOKEN` and `stakeEthForStETH`. [4](#0-3) 

### Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

Each stETH deposit over-mints rsETH by at most 1 wei worth of stETH. The rsETH price (computed from actual balances) is therefore infinitesimally lower than it should be after each deposit. Existing rsETH holders are diluted by the 1-wei discrepancy per deposit. The effect is cumulative but negligible per transaction; it does not cause a fund freeze or direct theft.

### Likelihood Explanation
**High.** stETH's share-based accounting rounds down on virtually every non-trivial transfer amount. Any user depositing stETH via `depositAsset` triggers this path. No special conditions are required.

### Recommendation
Measure the actual received amount by comparing balances before and after the transfer, and use that value for both the rsETH mint calculation and the deposit-limit check:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

This pattern is the standard mitigation for fee-on-transfer and rebasing tokens.

### Proof of Concept
1. Alice holds 1 stETH (1 000 000 000 000 000 000 shares-equivalent, but stETH balance may be `1e18 - 1` after a rebase).
2. Alice calls `depositAsset(stETH, 1e18, minRSETH, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` from `1e18`.
4. `safeTransferFrom` credits the contract with `1e18 - 1` stETH (share rounding).
5. `_mintRsETH` mints rsETH for `1e18`.
6. `getTotalAssetDeposits(stETH)` returns `1e18 - 1` (actual balance).
7. `rsETHPrice` is computed from `1e18 - 1` stETH but rsETH supply includes the extra unit — price is fractionally lower than it should be, diluting all prior holders. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTDepositPool.sol (L110-115)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L444-444)
```text
        assetLyingInDepositPool = IERC20(asset).balanceOf(address(this));
```

**File:** contracts/LRTDepositPool.sol (L565-570)
```text
    function stakeEthForStETH(address referral, uint256 ethAmount) external onlyLRTManager {
        address stETHAddress = lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN);

        uint256 stETHShares = ILido(stETHAddress).submit{ value: ethAmount }(referral);

        emit AssetStaked(stETHAddress, ethAmount, stETHShares);
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

**File:** contracts/LRTDepositPool.sol (L676-681)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

**File:** contracts/LRTOracle.sol (L341-343)
```text
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
