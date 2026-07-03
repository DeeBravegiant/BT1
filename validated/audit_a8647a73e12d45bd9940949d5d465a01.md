### Title
Fee-on-Transfer Token Support Causes rsETH Over-Minting and Protocol Insolvency - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset` computes the rsETH mint amount from the caller-supplied `depositAmount` **before** the `safeTransferFrom` call, then mints that pre-computed amount regardless of how many tokens were actually received. If a supported LST asset charges a transfer fee, the contract receives fewer tokens than `depositAmount` but mints rsETH as if it received the full amount, inflating rsETH supply relative to backing assets and diluting all existing rsETH holders.

### Finding Description
In `depositAsset`, the flow is:

1. `rsethAmountToMint = _beforeDeposit(asset, depositAmount, ...)` — calls `getRsETHAmountToMint(asset, depositAmount)` which computes `(depositAmount * assetPrice) / rsETHPrice`.
2. `IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount)` — actual tokens received may be `depositAmount - fee`.
3. `_mintRsETH(rsethAmountToMint)` — mints based on `depositAmount`, not actual received balance. [1](#0-0) 

The mint calculation in `getRsETHAmountToMint` uses the passed `amount` directly: [2](#0-1) 

There is no balance-before/balance-after check to determine the true received amount. The `_beforeDeposit` helper is a pure view function that never touches on-chain balances: [3](#0-2) 

### Impact Explanation
Every deposit with a fee-on-transfer asset mints more rsETH than the deposited collateral justifies. Because rsETH price is computed from total backing assets divided by total rsETH supply, the inflated supply reduces the redemption value for all existing rsETH holders — a direct, permanent dilution of their position. Repeated deposits drain value from the pool, eventually causing protocol insolvency.

**Impact: Critical — Protocol insolvency / permanent dilution of all rsETH holders.**

### Likelihood Explanation
The protocol is explicitly designed to support arbitrary LSTs via governance (`lrtConfig.addNewSupportedAsset`). If any future supported asset implements a transfer fee (as USDT does, currently at 0% but changeable), this path is immediately exploitable by any unprivileged depositor calling `depositAsset`. The entry point is fully public and requires no special role.

**Likelihood: Medium** — requires a fee-on-transfer token to be added as a supported asset, which is a realistic governance action.

### Recommendation
Record the contract's balance of `asset` before and after `safeTransferFrom`, and use the delta as the actual received amount for all subsequent calculations:

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = getRsETHAmountToMint(asset, actualReceived);
// enforce minRSETHAmountExpected against rsethAmountToMint here
_mintRsETH(rsethAmountToMint);
```

This mirrors the fix applied in [Saddle PR #191](https://github.com/saddle-finance/saddle-contract/pull/191).

### Proof of Concept
1. Governance adds a fee-on-transfer LST (e.g., 1% fee) as a supported asset.
2. Attacker calls `depositAsset(feeToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `1000e18`.
4. `safeTransferFrom` transfers `1000e18` from attacker; contract receives `990e18` (1% fee deducted).
5. `_mintRsETH` mints rsETH equivalent to `1000e18` of the asset.
6. The pool now holds `990e18` tokens but has issued rsETH backed by `1000e18` — a 1% over-issuance per deposit.
7. Repeated deposits continuously inflate rsETH supply relative to backing, reducing the redemption value for all existing holders. [4](#0-3)

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
