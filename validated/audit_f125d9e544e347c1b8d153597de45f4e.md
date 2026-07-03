### Title
Fee-on-Transfer Token Support Causes rsETH Over-Minting and Protocol Insolvency - (File: contracts/LRTDepositPool.sol)

### Summary

`LRTDepositPool.depositAsset()` computes the rsETH mint amount from the caller-supplied `depositAmount` parameter **before** the actual `safeTransferFrom` executes. If a fee-on-transfer (FoT) ERC20 token is ever added as a supported asset, the contract receives fewer tokens than `depositAmount`, but mints rsETH as if the full `depositAmount` arrived. This over-mints rsETH relative to actual backing assets, diluting all existing rsETH holders and driving the protocol toward insolvency.

### Finding Description

In `depositAsset`, the sequence is:

1. **Line 111** — `rsethAmountToMint` is calculated from the raw `depositAmount` parameter via `_beforeDeposit → getRsETHAmountToMint(asset, depositAmount)`.
2. **Line 114** — `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` executes. For a FoT token, the contract receives `depositAmount − fee`, not `depositAmount`.
3. **Line 115** — `_mintRsETH(rsethAmountToMint)` mints the amount computed in step 1, which is based on the full `depositAmount`, not the reduced amount actually received. [1](#0-0) 

The mint calculation inside `_beforeDeposit` delegates to `getRsETHAmountToMint`: [2](#0-1) 

There is no balance-before / balance-after check to detect the actual amount received. The same structural flaw exists in `RSETHPoolV3.deposit(address token, uint256 amount, ...)`, where `viewSwapRsETHAmountAndFee(amount, token)` is called with the parameter `amount` before the transfer result is verified: [3](#0-2) 

### Impact Explanation

Every deposit with a FoT token mints more rsETH than the assets that back it. Because rsETH price is derived from total protocol TVL divided by total rsETH supply (`LRTOracle.rsETHPrice()`), each such deposit lowers the effective backing per rsETH share. Repeated deposits drain value from all existing rsETH holders. At sufficient scale this constitutes **protocol insolvency** — the total rsETH supply exceeds the ETH value of all underlying assets.

Impact: **Critical** — protocol insolvency / permanent dilution of all rsETH holders.

### Likelihood Explanation

The vulnerability is latent: it activates the moment any fee-on-transfer ERC20 is added to `LRTConfig` as a supported asset. The `addSupportedAsset` path is a routine admin operation with no code-level guard against FoT tokens. The protocol already supports multiple LSTs and is designed to expand its asset list. A governance decision to add a token that happens to implement transfer fees (e.g., tokens with built-in protocol fees) would silently trigger this bug for every subsequent depositor.

Likelihood: **Low** (requires a FoT token to be onboarded), but the impact when triggered is critical and the code contains no protection.

### Recommendation

Replace the parameter-based amount with an actual-received amount computed via balance snapshots:

```solidity
function depositAsset(...) external ... {
    uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
    IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
    uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

    uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
    _mintRsETH(rsethAmountToMint);

    emit AssetDeposit(msg.sender, asset, actualReceived, rsethAmountToMint, referralId);
}
```

Apply the same fix to `RSETHPoolV3.deposit(address token, ...)`. Additionally, document explicitly that FoT and rebasing tokens are unsupported, and add an on-chain check in `LRTConfig.addSupportedAsset` if feasible.

### Proof of Concept

1. Admin adds a FoT token (2% fee per transfer) as a supported asset via `LRTConfig`.
2. Attacker calls `LRTDepositPool.depositAsset(fotToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `1000e18`.
4. `safeTransferFrom` transfers `1000e18`; contract receives `980e18` (after 2% fee).
5. `_mintRsETH` mints rsETH equivalent to `1000e18` worth of the asset.
6. The protocol now has `980e18` tokens backing rsETH priced as if `1000e18` arrived.
7. All existing rsETH holders are diluted by the `20e18` phantom value. Repeating this at scale collapses the rsETH backing ratio. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L109-117)
```text
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
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
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
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
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-291)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

```
